import { useEffect, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, RefreshCw, Wallet } from 'lucide-react';
import { accountApi, type Account } from './accountApi';
import { yuanText } from './money';
import { PaymentPanel } from './PaymentPanel';
import './CommercePanel.css';

export type Plan = { id: string; name: string; audience: string; price_cents: number;
  period_months: number; purchasable: boolean; entitlements: Record<string, number | boolean> };
export type Pricing = { currency: string; model_price_cents: number; plans: Plan[] };
type Summary = { balance_cents: number; entitlements: Record<string, number | boolean>;
  subscriptions: { id: string; plan_id: string; starts: number; ends: number }[];
  upload_usage: { used: number; limit: number; remaining: number; ends: number } | null };
type Entry = { id: string; delta_cents: number; balance_cents: number; kind: string; reference: string; created: number };

const labels: Record<string, string> = { recharge: '充值', model_debit: '人体建模', model_refund: '建模退款' };
const PAGE_SIZE = 10;

export function CommercePanel({ user }: { user: Account }) {
  const [data, setData] = useState<{ summary: Summary; pricing: Pricing } | null>(null);
  const [ledger, setLedger] = useState<Entry[]>([]);
  const [ledgerOffset, setLedgerOffset] = useState(0);
  const [ledgerError, setLedgerError] = useState('');
  const [ledgerLoading, setLedgerLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { setData(null); setLedgerOffset(0); }, [user.id]);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError('');
    void Promise.all([accountApi<Summary>('/api/account/commerce'), accountApi<Pricing>('/api/pricing')])
      .then(([summary, pricing]) => { if (alive) setData({ summary, pricing }); })
      .catch((err: Error) => { if (alive) setError(`钱包信息读取失败：${err.message}。请检查网络后重新查询。`); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [user.id, refresh]);
  useEffect(() => {
    let alive = true;
    setLedgerLoading(true); setLedgerError(''); setLedger([]);
    void accountApi<{ items: Entry[] }>(`/api/account/ledger?limit=${PAGE_SIZE}&offset=${ledgerOffset}`)
      .then((response) => { if (alive) setLedger(response.items); })
      .catch((err: Error) => { if (alive) setLedgerError(`资金流水读取失败：${err.message}。请重新查询。`); })
      .finally(() => { if (alive) setLedgerLoading(false); });
    return () => { alive = false; };
  }, [user.id, ledgerOffset, refresh]);
  function reload() { setRefresh((value) => value + 1); }
  return <section className="account-card commerce-panel"><div className="commerce-heading">
    <h2><Wallet size={19} />钱包与会员</h2>
    <button type="button" className="text-button" disabled={loading || ledgerLoading} onClick={reload} aria-label="刷新钱包与流水">
      <RefreshCw size={15} />刷新</button></div>
    {error && <p role="alert" className="commerce-error">{error}</p>}
    {!data ? <p role="status">{loading ? '正在读取钱包…' : '暂时无法读取钱包，请点击刷新重试。'}</p> : <>
      <div className="wallet-balance"><span>当前余额（元）</span><strong>¥{yuanText(data.summary.balance_cents)}</strong>
        <small>仅属于当前 ITP 账号 · {user.display_name}</small>
        <button type="button" className="button" onClick={() => {
          document.getElementById('wallet-recharge-amount')?.focus();
        }}>立即充值</button></div>
      <div className="commerce-benefits"><p>人体建模：¥{yuanText(data.pricing.model_price_cents)} / 次</p>
      {data.summary.balance_cents < data.pricing.model_price_cents && <p className="commerce-warning">余额不足以支付一次建模，请先充值。</p>}
      <p>个性化推荐权益：{data.summary.entitlements.personalized_recommendation ? '已开通' : '未开通'}</p>
      {data.summary.subscriptions.map((subscription) => <p key={subscription.id}>
        {data.pricing.plans.find((plan) => plan.id === subscription.plan_id)?.name || subscription.plan_id}
        {' · '}{subscription.starts > Date.now() / 1000 ? `${new Date(subscription.starts * 1000).toLocaleDateString()} 生效 · ` : ''}
        {new Date(subscription.ends * 1000).toLocaleDateString()} 到期
      </p>)}
      {data.pricing.plans.filter((plan) => plan.purchasable && (plan.audience === 'customer' || user.role === 'merchant'))
        .map((plan) => <p key={plan.id}>{plan.name}：¥{yuanText(plan.price_cents)} / {plan.period_months} 个月</p>)}
      {data.summary.upload_usage && <p>本周期上传 {data.summary.upload_usage.used} / {data.summary.upload_usage.limit} 次
        {' · '}{new Date(data.summary.upload_usage.ends * 1000).toLocaleDateString()} 重置。删除商品不恢复次数。</p>}</div>
      <PaymentPanel key={user.id} plans={data.pricing.plans.filter((plan) => plan.purchasable && (plan.audience === 'customer' || user.role === 'merchant'))}
        onPaid={() => { setLedgerOffset(0); reload(); }} />
      <section className="commerce-ledger" aria-label="最近资金流水" aria-busy={ledgerLoading}><h3>最近资金流水</h3>
        <small>包含充值、建模消费和自动退款。失败建模的退款由服务器自动处理。</small>
        {ledgerError && <p role="alert" className="commerce-error">{ledgerError}</p>}
        {!ledgerLoading && !ledgerError && !ledger.length && <p className="commerce-empty">暂无交易</p>}
        {ledger.map((entry) => <div key={entry.id} className="ledger-entry">
          <span className={`ledger-icon ${entry.delta_cents >= 0 ? 'income' : ''}`} aria-hidden="true">
            {entry.delta_cents >= 0 ? <ArrowDownLeft size={17} /> : <ArrowUpRight size={17} />}</span>
          <div className="ledger-description"><strong>{labels[entry.kind] || '资金变动'}
            {entry.kind === 'model_refund' && <span className="commerce-status success">已退款到账</span>}</strong>
            <time dateTime={new Date(entry.created * 1000).toISOString()}>{new Date(entry.created * 1000).toLocaleString()}</time>
            <small className="ledger-reference" title={entry.reference}>业务编号：{entry.reference}</small></div>
          <div className="ledger-amount"><strong className={entry.delta_cents >= 0 ? 'income' : ''}>
            {entry.delta_cents >= 0 ? '+' : '−'}¥{yuanText(Math.abs(entry.delta_cents))}</strong>
            <small>余额 ¥{yuanText(entry.balance_cents)}</small></div>
        </div>)}
        <div className="commerce-pagination"><button type="button" className="text-button" disabled={ledgerLoading || ledgerOffset === 0}
          onClick={() => setLedgerOffset((value) => Math.max(0, value - PAGE_SIZE))}>上一页流水</button>
          <span>第 {ledgerOffset / PAGE_SIZE + 1} 页</span>
          <button type="button" className="text-button" disabled={ledgerLoading || Boolean(ledgerError) || ledger.length < PAGE_SIZE}
            onClick={() => setLedgerOffset((value) => value + PAGE_SIZE)}>下一页流水</button></div>
      </section>
    </>}
  </section>;
}
