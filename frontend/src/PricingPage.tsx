import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { accountApi } from './accountApi';
import { api } from './api';
import type { Pricing } from './CommercePanel';
import { useCustomer } from './customerState';
import { PaymentPanel } from './PaymentPanel';
import { PlanCards } from './PlanCards';
import './Benefits.css';

export default function PricingPage() {
  const { account } = useCustomer();
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [current, setCurrent] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    void api<Pricing>('/api/pricing').then((p) => { if (alive) setPricing(p); }).catch((e: Error) => { if (alive) setError(e.message); });
    if (account.user) void accountApi<{ subscriptions: { plan_id: string; starts: number; ends: number }[] }>('/api/account/commerce')
      .then((s) => { if (alive) setCurrent(s.subscriptions.filter((p) => p.starts <= Date.now() / 1000 && p.ends > Date.now() / 1000).map((p) => p.plan_id)); })
      .catch((e: Error) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [account.user?.id, refresh]);
  return <main className="benefits-page pricing-page"><h2>选择适合你的会员</h2>
    <p>顾客积分与人民币分开记账。商家上传按当期新增件数计算，已有商品到期后保留。</p>
    {error && <p role="alert">{error}</p>}
    {pricing ? account.user ? <PaymentPanel plans={pricing.plans} currentPlans={current}
      onPaid={() => setRefresh((n) => n + 1)} /> : <><PlanCards plans={pricing.plans} disabled onPurchase={() => {}} />
      <Link className="button" to="/account">登录后购买</Link></> : <p>正在读取套餐…</p>}
  </main>;
}
