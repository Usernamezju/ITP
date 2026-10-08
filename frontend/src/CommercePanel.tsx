import { useEffect, useState, type ReactNode } from 'react';
import { ArrowDownLeft, ArrowUpRight, ChartNoAxesColumn, ChevronRight, Crown, RefreshCw, Wallet } from 'lucide-react';
import { accountApi, type Account } from './accountApi';
import { yuanText } from './money';
import { PaymentPanel } from './PaymentPanel';
import './CommercePanel.css';
import { PointsPanel } from './PointsPanel';

export type Plan = { id: string; name: string; audience: string; price_cents: number;
  period_months: number; purchasable: boolean; entitlements: Record<string, number | boolean> };
export type Pricing = { currency: string; model_price_cents: number; model_price_points: number; plans: Plan[] };
type Summary = { balance_cents: number; entitlements: Record<string, number | boolean>;
  subscriptions: { id: string; plan_id: string; starts: number; ends: number }[];
  upload_usage: { used: number; limit: number; remaining: number; ends: number } | null };
type Entry = { id: string; delta_cents: number; balance_cents: number; kind: string; reference: string; created: number };

const labels: Record<string, string> = { recharge: '充值', model_debit: '人体建模', model_refund: '建模退款' };
const PAGE_SIZE = 10;

export function CommercePanel({ user, children }: { user: Account; children?: ReactNode }) {
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
  const now = Date.now() / 1000;
  const activeSubscriptions = data?.summary.subscriptions.filter((subscription) => subscription.starts <= now && subscription.ends > now) || [];
  const plans = data?.pricing.plans.filter((plan) => plan.audience === 'customer' || user.role === 'merchant') || [];
  const memberPlan = plans.find((plan) => plan.id === 'customer_monthly') || plans.find((plan) => plan.purchasable);
  function focusRecharge() { document.getElementById('wallet-recharge-amount')?.focus(); }
  function showMembership() {
    const details = document.getElementById('account-member-details') as HTMLDetailsElement | null;
    if (details) { details.open = true; details.scrollIntoView({ block: 'nearest' }); }
  }
  return <section id="account-information" className="commerce-panel account-dashboard" aria-label="账户资金概览" tabIndex={-1}><div className="commerce-heading">
    <span>账户资金与权益</span>
    <button type="button" className="text-button" disabled={loading || ledgerLoading} onClick={reload} aria-label="刷新钱包与流水">
      <RefreshCw size={15} />刷新</button></div>
    {error && <p role="alert" className="commerce-error">{error}</p>}
    <div className="account-overview" aria-busy={loading}>
      <section className="account-overview-card wallet-balance" aria-label="当前余额">
        <span className="account-metric-icon balance"><Wallet size={27} aria-hidden="true" /></span>
        <div className="account-metric-copy"><span>当前余额（元）</span>
          <strong>{data ? `¥${yuanText(data.summary.balance_cents)}` : '—'}</strong></div>
        <button type="button" className="account-metric-more" aria-label="查看资金流水"
          onClick={() => document.getElementById('account-ledger')?.scrollIntoView({ block: 'start' })}><ChevronRight size={20} /></button>
        <button type="button" className="button" disabled={!data} onClick={focusRecharge}>立即充值</button>
      </section>
      <section className="account-overview-card" aria-label="会员状态">
        <span className="account-metric-icon membership"><Crown size={28} aria-hidden="true" /></span>
        <div className="account-metric-copy"><span>会员状态</span>
          <strong>{!data ? '—' : activeSubscriptions.length ? '已开通' : '未开通'}</strong></div>
        <button type="button" className="account-metric-more" aria-label="查看会员详情" disabled={!data}
          onClick={showMembership}><ChevronRight size={20} /></button>
        <small>{memberPlan ? `${memberPlan.name}：¥${yuanText(memberPlan.price_cents)} / ${memberPlan.period_months} 个月`
          : data ? '暂时没有可购买的会员套餐' : '正在读取会员信息…'}</small>
      </section>
      </div>
    {!data && <p role="status" className="account-funds-loading">{loading ? '正在读取钱包…' : '暂时无法读取钱包，请点击刷新重试。'}</p>}
    {children}
    <PointsPanel key={`${user.id}:${refresh}`} />
    <PaymentPanel key={user.id} plans={plans} currentPlans={activeSubscriptions.map((s) => s.plan_id)} onPaid={() => { setLedgerOffset(0); reload(); }} />
      <section id="account-ledger" className="account-card commerce-ledger" aria-label="最近资金流水" aria-busy={ledgerLoading}>
        <div className="commerce-heading"><h3>最近资金流水</h3>
          <button type="button" className="text-button" disabled={ledgerLoading} onClick={reload}>刷新流水<RefreshCw size={14} /></button></div>
        <small>包含充值、建模消费和自动退款。失败建模的退款由服务器自动处理。</small>
        {ledgerError && <p role="alert" className="commerce-error">{ledgerError}</p>}
        {!ledgerLoading && !ledgerError && !ledger.length && <div className="account-history-empty">
          <ChartNoAxesColumn size={38} aria-hidden="true" /><strong>暂无交易</strong>
          <p>您还没有资金流水记录</p><button type="button" className="button account-outline" onClick={focusRecharge}>查看充值方式</button></div>}
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
    <details id="account-member-details" className="account-card commerce-benefits">
      <summary>会员与使用权益</summary>
      {!data ? <p>权益信息暂未读取成功，请刷新钱包重试。</p> : <>
        <p>基础人体建模 {data.pricing.model_price_points} 积分 / 次，失败退还</p>
        <p>个性化推荐权益：{data.summary.entitlements.personalized_recommendation ? '已开通' : '未开通'}</p>
        {data.summary.subscriptions.map((subscription) => <p key={subscription.id}>
          {data.pricing.plans.find((plan) => plan.id === subscription.plan_id)?.name || subscription.plan_id}
          {' · '}{subscription.starts > now ? `${new Date(subscription.starts * 1000).toLocaleDateString()} 生效 · ` : ''}
          {new Date(subscription.ends * 1000).toLocaleDateString()} 到期
        </p>)}
        {data.summary.upload_usage && <p>本周期上传 {data.summary.upload_usage.used} / {data.summary.upload_usage.limit} 次
          {' · '}{new Date(data.summary.upload_usage.ends * 1000).toLocaleDateString()} 重置。删除商品不恢复次数。</p>}
      </>}
    </details>
  </section>;
}
