import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { accountApi } from './accountApi';
import './Benefits.css';

export type PointSummary = { balance_points: number; model_price_points: number;
  member: boolean; first_month: boolean; first_month_ends: number; signin_points: number;
  recommend_limit: number; recommend_used: number; cards: number; streak: number;
  attendance_count: number; attendance_required: number; month: string; today: string;
  days: { day: string; state: string; can_makeup: boolean }[]; demo_unlimited: boolean };
type PointEntry = { id: string; delta: number; balance: number; kind: string; created: number };
const labels: Record<string, string> = { registration: '新顾客赠送', first_membership: '首次付费会员赠送',
  signin: '每日签到', makeup: '补签奖励', full_attendance: '会员月全勤奖', model_debit: '基础人体建模', model_refund: '建模失败退还' };

export function PointsPanel() {
  const [summary, setSummary] = useState<PointSummary | null>(null);
  const [entries, setEntries] = useState<PointEntry[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    void Promise.all([accountApi<PointSummary>('/api/account/points'),
      accountApi<{ items: PointEntry[] }>(`/api/account/points/ledger?limit=10&offset=${offset}`)])
      .then(([s, l]) => { if (active) { setSummary(s); setEntries(l.items); setError(''); } })
      .catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [refresh, offset]);
  return <section className="account-card points-panel" aria-label="积分与权益">
    <div className="commerce-heading"><h3>积分与权益</h3><button type="button" className="text-button"
      onClick={() => setRefresh((n) => n + 1)}>刷新积分</button></div>
    {error && <p role="alert">积分读取失败：{error}，请刷新重试。</p>}
    <p>积分余额 <strong className="points-total">{summary?.balance_points ?? '—'}</strong></p>
    <p>基础人体建模 {summary?.model_price_points ?? '—'} 积分 / 次，失败退还</p>
    {summary && <p>今日推荐 {summary.recommend_used} / {summary.demo_unlimited ? '演示不限量' : summary.recommend_limit} 次
      {' · '}{summary.member ? '月会员有效' : summary.first_month ? '首月福利有效' : '首月福利已结束'}
      {!summary.member && summary.first_month && `（${new Date(summary.first_month_ends * 1000).toLocaleString()} 截止）`}</p>}
    <div className="benefit-actions"><Link className="button" to="/signin">每日签到</Link><Link className="button account-outline" to="/pricing">会员套餐</Link></div>
    <details><summary>积分流水</summary>
      {entries.map((entry) => <div key={entry.id} className="point-entry"><span>{labels[entry.kind] || '积分变动'}
        <small>{new Date(entry.created * 1000).toLocaleString()}</small></span>
        <span>{entry.delta >= 0 ? '+' : ''}{entry.delta} · 余额 {entry.balance}</span></div>)}
      <div className="commerce-pagination"><button type="button" className="text-button" disabled={!offset}
        onClick={() => setOffset((n) => Math.max(0, n - 10))}>上一页积分</button>
        <button type="button" className="text-button" disabled={entries.length < 10}
          onClick={() => setOffset((n) => n + 10)}>下一页积分</button></div>
    </details>
    <small>积分与人民币钱包分别记账，人民币充值不会自动变成积分。</small>
  </section>;
}
