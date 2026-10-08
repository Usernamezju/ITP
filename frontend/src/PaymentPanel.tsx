import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Clock3, CircleAlert, FileText, RefreshCw } from 'lucide-react';
import { accountApi } from './accountApi';
import { api } from './api';
import { parseYuan, yuanText } from './money';
import type { Plan } from './CommercePanel';
import { PlanCards } from './PlanCards';

type Method = { id: string; name: string; ready: boolean };
type Order = { id: string; kind: string; provider: string; amount_cents: number; state: string;
  description: string; created: number; expires: number;
  // `manual` marks a personal collection code: nothing signed ever arrives for
  // it, so an operator confirms the transfer by hand.
  checkout: { mock?: boolean; manual?: boolean; qr_image?: string } | null };
const states: Record<string, string> = { created: '等待支付', submitting: '支付订单处理中',
  pending: '等待支付', uncertain: '支付状态待确认', paid: '支付已确认' };

/** True while a personal collection code is waiting on an operator, not a bank. */
const awaitingOperator = (order: Order) => Boolean(order.checkout?.manual) && order.state !== 'paid';
const PAGE_SIZE = 10;
const methodNames: Record<string, string> = { alipay: '支付宝', wechat: '微信支付', mock: '模拟支付（仅开发测试）', free: '平台免费权益' };

function OrderStatus({ order }: { order: Order }) {
  return <span className={`commerce-status ${order.state === 'paid' ? 'success' : ''}`}>
    {order.state === 'paid' ? <CheckCircle2 size={14} aria-hidden="true" />
      : order.state === 'uncertain' ? <CircleAlert size={14} aria-hidden="true" /> : <Clock3 size={14} aria-hidden="true" />}
    {/* A collection code is confirmed by a person, and saying "等待支付" would
        invite a second transfer from someone who has already sent the money. */}
    {awaitingOperator(order) ? '待人工确认' : states[order.state] || '订单状态待确认'}</span>;
}

export function PaymentPanel({ plans, currentPlans = [], onPaid }: { plans: Plan[]; currentPlans?: string[]; onPaid: () => void }) {
  const [methods, setMethods] = useState<Method[]>([]);
  const [provider, setProvider] = useState('');
  const [amount, setAmount] = useState('');
  const [orders, setOrders] = useState<Order[]>([]);
  const [active, setActive] = useState<Order | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ordersOffset, setOrdersOffset] = useState(0);
  const [ordersLoading, setOrdersLoading] = useState(true);
  const [ordersError, setOrdersError] = useState('');
  const [ordersRefresh, setOrdersRefresh] = useState(0);
  const attempts = useRef(new Map<string, string>());
  const notified = useRef(new Set<string>());
  const generation = useRef(0);

  function reload() { setOrdersRefresh((value) => value + 1); }
  useEffect(() => {
    let alive = true;
    void api<{ methods: Method[] }>('/api/payments/methods').then((data) => {
      if (!alive) return;
      setMethods(data.methods); setProvider(data.methods.find((method) => method.ready)?.id || '');
    }).catch((err: Error) => { if (alive) setError(err.message); });
    return () => { alive = false; generation.current++; };
  }, []);
  useEffect(() => {
    let alive = true;
    setOrdersLoading(true); setOrdersError(''); setOrders([]);
    void accountApi<{ items: Order[] }>(`/api/account/orders?limit=${PAGE_SIZE}&offset=${ordersOffset}`).then((response) => {
      if (alive) setOrders(response.items);
    }).catch((err: Error) => { if (alive) setOrdersError(`订单读取失败：${err.message}。请点击刷新订单重试。`); })
      .finally(() => { if (alive) setOrdersLoading(false); });
    return () => { alive = false; };
  }, [ordersOffset, ordersRefresh]);

  function receive(order: Order) {
    setActive(order);
    setOrders((old) => old.map((item) => item.id === order.id ? order : item));
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
      }).catch((err: Error) => { if (alive) setError(`暂时无法读取支付状态：${err.message}。订单仍保留，请稍后刷新支付状态。`); });
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
    if (parsed !== null && parsed > 10000000) { setError('单次充值不能超过 100000 元'); return; }
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
      attempts.current.delete(key); receive(order); setOrdersOffset(0); reload();
    } catch (err) { if (marker === generation.current) {
      setError(`充值或购买未完成：${(err as Error).message}。请检查我的订单；同一金额和支付方式重试会复用本次请求，避免重复创建订单。`);
      reload();
    } }
    finally { if (marker === generation.current) setBusy(false); }
  }
  async function refresh(mock = false) {
    if (!active || busy) return;
    const marker = generation.current;
    setBusy(true); setError('');
    try {
      const order = await accountApi<Order>(`/api/account/orders/${active.id}/${mock ? 'mock-pay' : 'refresh'}`, 'POST', {});
      if (marker !== generation.current) return;
      receive(order); reload();
    } catch (err) { if (marker === generation.current) setError(`查询支付结果失败：${(err as Error).message}。请稍后重试，已支付时请勿重复付款。`); }
    finally { if (marker === generation.current) setBusy(false); }
  }

  const timedOut = active && active.state !== 'paid' && active.expires * 1000 <= Date.now();

  return <div className="payment-panel">
    <section className="account-card payment-recharge-card" aria-label="充值与会员">
    <h3>充值与会员</h3>
    <div className="payment-fields"><label htmlFor="wallet-recharge-amount">充值金额（元）<input id="wallet-recharge-amount" className="text-input" inputMode="decimal" value={amount}
      placeholder="请输入金额，最多两位小数" onChange={(event) => setAmount(event.target.value)} aria-describedby={error ? 'payment-error' : undefined} /></label>
    <div className="payment-amounts" aria-label="快捷充值金额">{['30', '50', '100'].map((value) => <button key={value} type="button"
      className={amount === value ? 'selected' : ''} aria-pressed={amount === value} onClick={() => setAmount(value)}>¥{value}</button>)}</div>
    {methods.some((method) => method.ready) ? <label>支付方式<select className="text-input" value={provider}
      onChange={(event) => setProvider(event.target.value)}>
      {methods.filter((method) => method.ready).map((method) => <option key={method.id} value={method.id}>{method.name}</option>)}
    </select></label> : <div className="commerce-warning"><p>平台暂未开放在线支付</p><small>支付渠道未配置。请稍后重试或联系平台；已有余额仍可正常使用。</small></div>}
    {error && <p id="payment-error" role="alert" className="commerce-error">{error}</p>}
    <button type="button" className="button" disabled={busy || !provider} onClick={() => void purchase('recharge')}>{busy ? '正在处理订单…' : '创建充值订单'}</button></div>
    <PlanCards plans={plans} current={currentPlans} disabled={busy || !provider} onPurchase={(plan) => void purchase('membership', plan)} />
    {active && <section aria-label="支付订单" className="payment-active"><p>{active.description}</p><p className="payment-total">¥{yuanText(active.amount_cents)}</p>
      <p role="status"><OrderStatus order={active} /></p>
      <small>支付方式：{methodNames[active.provider] || '支付渠道'}<br />订单编号：<span className="payment-reference">{active.id}</span></small>
      {active.state === 'uncertain' && <p className="commerce-warning">支付渠道暂未确认结果，请查询原订单，不要重复付款。</p>}
      {awaitingOperator(active) && <div className="payment-manual">
        <p>请向下方收款码转账 <strong>¥{yuanText(active.amount_cents)}</strong>，金额请精确到分。</p>
        <small>收款码是平台所有者的个人收款图片，<b>不含金额</b>，转账金额需要您自行填写；平台无法读取您的支付结果。
          转账后由管理员核对到账再确认，确认前订单一直显示「待人工确认」，本页会自动感知，无需再次付款。</small>
      </div>}
      {timedOut && <p className="commerce-warning">付款时限已到，请先刷新支付状态确认结果。若已付款，请勿重复付款；超时不代表支付失败。</p>}
      {active.state !== 'paid' && !timedOut && <small>付款期限：{new Date(active.expires * 1000).toLocaleString()}</small>}
      {active.state !== 'paid' && !timedOut && active.checkout?.qr_image && <img width="200" height="200" src={active.checkout.qr_image} alt="扫码支付二维码" />}
      {active.state !== 'paid' && <button type="button" className="text-button" disabled={busy} onClick={() => void refresh()}>
        {awaitingOperator(active) ? '查询人工确认结果' : '刷新支付状态'}</button>}
      {active.state !== 'paid' && active.checkout?.mock && <button type="button" className="text-button" disabled={busy}
        onClick={() => void refresh(true)}>模拟付款（仅开发测试）</button>}
      <small>{active.state === 'paid' ? '支付成功，服务端已确认并更新余额或会员权益。'
        : awaitingOperator(active) ? '余额与会员由管理员核对到账后发放；点击「已支付」或上传截图都不会入账。'
          : '余额与会员仅在服务端核实支付后更新。'}</small>
    </section>}
    </section>
    <section className="account-card payment-orders" aria-label="我的订单">
    <div className="commerce-heading"><h3>我的订单</h3><button type="button" className="text-button" disabled={ordersLoading} onClick={reload}>
      <RefreshCw size={14} />刷新订单</button></div>
    {ordersError && <p role="alert" className="commerce-error">{ordersError}</p>}
    {ordersLoading && <small role="status">正在读取订单…</small>}
    <div className="payment-order-list" aria-busy={ordersLoading}>{orders.map((order) => <button type="button" key={order.id}
      className={active?.id === order.id ? 'selected' : ''} onClick={() => { setError(''); receive(order); }}>
      <span className="payment-order-title"><span>{order.description}</span><strong>¥{yuanText(order.amount_cents)}</strong></span>
      <OrderStatus order={order} /><small>{new Date(order.created * 1000).toLocaleString()} · {methodNames[order.provider] || '支付渠道'}</small>
    </button>)}</div>
    {!ordersLoading && !ordersError && !orders.length && <div className="account-history-empty">
      <FileText size={38} aria-hidden="true" /><strong>暂无订单</strong><p>您还没有创建任何订单</p>
      <button type="button" className="button account-outline"
        onClick={() => document.getElementById('wallet-recharge-amount')?.focus()}>去充值</button></div>}
    <div className="commerce-pagination"><button type="button" className="text-button" disabled={ordersLoading || ordersOffset === 0}
      onClick={() => setOrdersOffset((value) => Math.max(0, value - PAGE_SIZE))}>上一页订单</button><span>第 {ordersOffset / PAGE_SIZE + 1} 页</span>
      <button type="button" className="text-button" disabled={ordersLoading || Boolean(ordersError) || orders.length < PAGE_SIZE}
        onClick={() => setOrdersOffset((value) => value + PAGE_SIZE)}>下一页订单</button></div>
    </section>
  </div>;
}
