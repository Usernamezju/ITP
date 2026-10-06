import { useEffect, useState } from 'react';
import { accountApi, type Account } from './accountApi';
import { yuanText } from './money';
import { PaymentPanel } from './PaymentPanel';

export type Plan = { id: string; name: string; audience: string; price_cents: number;
  period_months: number; purchasable: boolean; entitlements: Record<string, number | boolean> };
export type Pricing = { currency: string; model_price_cents: number; plans: Plan[] };
type Summary = { balance_cents: number; entitlements: Record<string, number | boolean>;
  subscriptions: { id: string; plan_id: string; starts: number; ends: number }[];
  upload_usage: { used: number; limit: number; remaining: number; ends: number } | null };
type Entry = { id: string; delta_cents: number; balance_cents: number; kind: string; created: number };

const labels: Record<string, string> = { recharge: '充值', model_debit: '人体建模', model_refund: '建模退款' };

export function CommercePanel({ user }: { user: Account }) {
  const [data, setData] = useState<{ summary: Summary; pricing: Pricing; ledger: Entry[] } | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    void Promise.all([accountApi<Summary>('/api/account/commerce'), accountApi<Pricing>('/api/pricing'),
      accountApi<{ items: Entry[] }>('/api/account/ledger')]).then(([summary, pricing, ledger]) => {
      if (alive) setData({ summary, pricing, ledger: ledger.items });
    }).catch((err: Error) => { if (alive) setError(err.message); });
    return () => { alive = false; };
  }, [user.id, refresh]);
  return <section className="account-card"><h2>钱包与会员</h2>
    {error ? <p role="alert">钱包信息读取失败：{error}</p> : !data ? <p>正在读取…</p> : <>
      <p>钱包余额 <strong>¥{yuanText(data.summary.balance_cents)}</strong></p>
      <p>人体建模：¥{yuanText(data.pricing.model_price_cents)} / 次</p>
      <p>个性化推荐权益：{data.summary.entitlements.personalized_recommendation ? '已开通' : '未开通'}</p>
      {data.summary.subscriptions.map((subscription) => <p key={subscription.id}>
        {data.pricing.plans.find((plan) => plan.id === subscription.plan_id)?.name || subscription.plan_id}
        {' · '}{new Date(subscription.ends * 1000).toLocaleDateString()} 到期
      </p>)}
      {data.pricing.plans.filter((plan) => plan.purchasable && (plan.audience === 'customer' || user.role === 'merchant'))
        .map((plan) => <p key={plan.id}>{plan.name}：¥{yuanText(plan.price_cents)} / {plan.period_months} 个月</p>)}
      {data.summary.upload_usage && <p>本周期上传 {data.summary.upload_usage.used} / {data.summary.upload_usage.limit} 次
        {' · '}{new Date(data.summary.upload_usage.ends * 1000).toLocaleDateString()} 重置。删除商品不恢复次数。</p>}
      <PaymentPanel plans={data.pricing.plans.filter((plan) => plan.purchasable && (plan.audience === 'customer' || user.role === 'merchant'))}
        onPaid={() => setRefresh((value) => value + 1)} />
      <h3>最近流水</h3>{!data.ledger.length ? <small>暂无交易</small> : data.ledger.map((entry) => <p key={entry.id}>
        {labels[entry.kind] || entry.kind} · {entry.delta_cents >= 0 ? '+' : ''}¥{yuanText(entry.delta_cents)}
        {' · '}{new Date(entry.created * 1000).toLocaleString()}
      </p>)}
    </>}
  </section>;
}
