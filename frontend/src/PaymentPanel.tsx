import { useEffect, useRef, useState } from 'react';
import { accountApi } from './accountApi';
import { api } from './api';
import { parseYuan, yuanText } from './money';
import type { Plan } from './CommercePanel';

type Method = { id: string; name: string; ready: boolean };
type Order = { id: string; kind: string; provider: string; amount_cents: number; state: string;
  description: string; created: number; expires: number;
  checkout: { mock?: boolean; qr_image?: string } | null };
const states: Record<string, string> = { created: '等待支付', submitting: '支付订单处理中',
  pending: '等待支付', uncertain: '支付状态待确认', paid: '支付已确认' };

export function PaymentPanel({ plans, onPaid }: { plans: Plan[]; onPaid: () => void }) {
  const [methods, setMethods] = useState<Method[]>([]);
  const [provider, setProvider] = useState('');
  const [amount, setAmount] = useState('');
  const [orders, setOrders] = useState<Order[]>([]);
  const [active, setActive] = useState<Order | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const attempts = useRef(new Map<string, string>());
  const notified = useRef(new Set<string>());
  const generation = useRef(0);

  async function reload() {
    const response = await accountApi<{ items: Order[] }>('/api/account/orders');
    setOrders(response.items);
  }
  useEffect(() => {
    let alive = true;
    void api<{ methods: Method[] }>('/api/payments/methods').then((data) => {
      if (!alive) return;
      setMethods(data.methods); setProvider(data.methods.find((method) => method.ready)?.id || '');
    }).catch((err: Error) => { if (alive) setError(err.message); });
    void accountApi<{ items: Order[] }>('/api/account/orders').then((response) => {
      if (alive) setOrders(response.items);
    }).catch((err: Error) => { if (alive) setError(err.message); });
    return () => { alive = false; generation.current++; };
  }, []);

  function receive(order: Order) {
    setActive(order);
    if (order.state === 'paid' && !notified.current.has(order.id)) {
      notified.current.add(order.id); onPaid();
    }
  }
  useEffect(() => {
    if (!active || active.state === 'paid') return;
    let alive = true;
    const timer = window.setInterval(() => {
      void accountApi<Order>(`/api/account/orders/${active.id}`).then((order) => {
        if (alive) receive(order);
      }).catch((err: Error) => { if (alive) setError(err.message); });
    }, 3000);
    return () => { alive = false; window.clearInterval(timer); };
    // Polling reads persisted verified state; it never claims browser success.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, active?.state]);

  async function purchase(kind: 'recharge' | 'membership', plan?: Plan) {
    if (busy) return;
    const parsed = kind === 'recharge' ? parseYuan(amount) : null;
    if (kind === 'recharge' && (parsed === null || parsed <= 0)) {
      setError('请输入大于 0 且最多两位小数的充值金额'); return;
    }
    if (!provider && (kind === 'recharge' || plan?.price_cents !== 0)) {
      setError('平台暂未开放在线支付'); return;
    }
    const body = { kind, provider: provider || null, ...(plan ? { plan_id: plan.id } : { amount_cents: parsed }) };
    const key = JSON.stringify(body);
    if (!attempts.current.has(key)) attempts.current.set(key, crypto.randomUUID());
    const marker = generation.current;
    setBusy(true); setError('');
    try {
      const order = await api<Order>('/api/account/orders', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': attempts.current.get(key)! },
        body: JSON.stringify(body) });
      if (marker !== generation.current) return;
      attempts.current.delete(key); receive(order); await reload();
    } catch (err) { if (marker === generation.current) setError((err as Error).message); }
    finally { if (marker === generation.current) setBusy(false); }
  }
  async function refresh(mock = false) {
    if (!active || busy) return;
    setBusy(true); setError('');
    try {
      receive(await accountApi<Order>(`/api/account/orders/${active.id}/${mock ? 'mock-pay' : 'refresh'}`, 'POST', {}));
      await reload();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }

  return <div className="payment-panel">
    <h3>充值与会员购买</h3>
    {error && <p role="alert">{error}</p>}
    {methods.some((method) => method.ready) ? <label>支付方式<select className="text-input" value={provider}
      onChange={(event) => setProvider(event.target.value)}>
      {methods.filter((method) => method.ready).map((method) => <option key={method.id} value={method.id}>{method.name}</option>)}
    </select></label> : <small>平台暂未开放在线支付</small>}
    <label>充值金额（元）<input className="text-input" inputMode="decimal" value={amount}
      onChange={(event) => setAmount(event.target.value)} /></label>
    <button type="button" className="button" disabled={busy || !provider} onClick={() => void purchase('recharge')}>创建充值订单</button>
    {plans.map((plan) => <button key={plan.id} type="button" className="text-button"
      disabled={busy || (!provider && plan.price_cents !== 0)} onClick={() => void purchase('membership', plan)}>
      购买{plan.name} · ¥{yuanText(plan.price_cents)}</button>)}
    {active && <section aria-label="支付订单"><p>{active.description} · ¥{yuanText(active.amount_cents)}</p>
      <p role="status">{states[active.state] || active.state}</p>
      {active.state !== 'paid' && active.checkout?.qr_image && <img width="200" height="200" src={active.checkout.qr_image} alt="扫码支付二维码" />}
      {active.state !== 'paid' && <button type="button" className="text-button" disabled={busy} onClick={() => void refresh()}>刷新支付状态</button>}
      {active.state !== 'paid' && active.checkout?.mock && <button type="button" className="text-button" disabled={busy}
        onClick={() => void refresh(true)}>模拟付款（仅开发测试）</button>}
      <small>余额与会员仅在服务端核实支付后更新。</small>
    </section>}
    <h3>我的订单</h3>{orders.length ? orders.map((order) => <button type="button" key={order.id} className="text-button"
      onClick={() => receive(order)}>{order.description} · ¥{yuanText(order.amount_cents)} · {states[order.state] || order.state}</button>)
      : <small>暂无订单</small>}
  </div>;
}
