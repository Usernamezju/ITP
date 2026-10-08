import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Gift, LoaderCircle } from 'lucide-react';
import { accountApi } from './accountApi';
import { ApiError } from './api';

type Recipient = { id: string; name: string; display_name: string; role: string; balance_points: number };
type Plan = { id: string; name: string; audience: string; period_months: number };
type GiftPayload = { request_id: string; user_id: string; kind: 'membership' | 'points'; reason: string;
  plan_id?: string; periods?: number; points?: number };
type Review = { payload: GiftPayload; account: Recipient; label: string; attempted: boolean };
type GiftRecord = { id: string; account_name: string; admin_name?: string; kind: string; reason: string;
  points?: number; plan_name?: string; periods?: number; created: number; balance_points?: number;
  subscriptions?: { starts: number; ends: number }[] };
type Document = { total: number; items: GiftRecord[]; plans: Plan[] };
const when = (stamp: number) => new Date(stamp * 1000).toLocaleString('zh-CN');

export default function AdminGifts({ adminId, onGift }: { adminId: string; onGift: () => void }) {
  const key = `itp.admin.gift.${adminId}`;
  const [review, setReview] = useState<Review | null>(() => {
    try {
      const saved = sessionStorage.getItem(key);
      return saved ? JSON.parse(saved) as Review : null;
    } catch { return null; }
  });
  const [document, setDocument] = useState<Document | null>(null);
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<Recipient[]>([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<Recipient | null>(null);
  const [kind, setKind] = useState<'membership' | 'points'>('membership');
  const [planId, setPlanId] = useState('');
  const [amount, setAmount] = useState('1');
  const [points, setPoints] = useState('1000');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [offset, setOffset] = useState(0);
  const plans = document?.plans.filter(p => p.audience === selected?.role) ?? [];
  const plan = plans.find(p => p.id === planId) ?? plans[0];

  const reload = useCallback(async () => {
    try { setDocument(await accountApi<Document>(`/api/admin/gifts?limit=20&offset=${offset}`)); }
    catch (err) { setError(err instanceof Error ? err.message : '读取赠送记录失败'); }
  }, [offset]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    let current = true;
    setMatches([]);
    if (!query.trim()) { setSearching(false); return; }
    setSearching(true);
    const timer = window.setTimeout(() => {
      void accountApi<{ items: Recipient[] }>(`/api/admin/gifts/accounts?q=${encodeURIComponent(query.trim())}`)
        .then(data => { if (current) setMatches(data.items); })
        .catch(err => { if (current) setError(err instanceof Error ? err.message : '搜索账号失败'); })
        .finally(() => { if (current) setSearching(false); });
    }, 250);
    return () => { current = false; window.clearTimeout(timer); };
  }, [query]);

  function preview(event: FormEvent) {
    event.preventDefault(); setError(''); setNotice('');
    if (!selected || !reason.trim()) { setError('请选择赠送账号并填写原因'); return; }
    const value = Number(kind === 'membership' ? amount : points);
    if (!Number.isSafeInteger(value) || value < 1 || value > (kind === 'membership' ? 12 : 1_000_000_000)
      || (kind === 'membership' && !plan)) { setError('请填写有效的赠送额度'); return; }
    const payload: GiftPayload = { request_id: crypto.randomUUID(), user_id: selected.id, kind, reason: reason.trim(),
      ...(kind === 'membership' ? { plan_id: plan.id, periods: value } : { points: value }) };
    setReview({ payload, account: selected, attempted: false,
      label: kind === 'points' ? `${value} 积分` : `${plan.name} ${value} 期（每期 ${plan.period_months} 个月）` });
  }

  async function confirm() {
    if (!review || busy) return;
    const pending = { ...review, attempted: true };
    setReview(pending); setBusy(true); setError(''); setNotice('');
    try {
      // Keep the same request across an interrupted response or page refresh.
      sessionStorage.setItem(key, JSON.stringify(pending));
      const result = await accountApi<GiftRecord>('/api/admin/gifts', 'POST', review.payload);
      sessionStorage.removeItem(key);
      setReview(null); setReason('');
      if (selected && result.balance_points !== undefined) setSelected({ ...selected, balance_points: result.balance_points });
      setNotice(`已向 ${result.account_name} 赠送${review.label}。${result.subscriptions?.length
        ? `本次权益结束时间：${when(result.subscriptions[result.subscriptions.length - 1].ends)}` : ''}`);
      await reload(); onGift();
    } catch (err) {
      setError(err instanceof Error ? err.message : '赠送失败，请重试本次赠送');
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        sessionStorage.removeItem(key); setReview({ ...review, attempted: false });
      }
    } finally { setBusy(false); }
  }

  return <div className="admin-gifts">
    <p className="admin-note">向正常的顾客或商家赠送会员、积分。已有同套餐会员会续期；赠送不扣钱包余额，赠送原因和操作人会留存。</p>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {notice && <p role="status" className="admin-check ok">{notice}</p>}
    <form onSubmit={preview}>
      <fieldset className="admin-gift-fields" disabled={busy || Boolean(review)}>
        <label>搜索赠送账号<input className="text-input" value={query} maxLength={128}
          placeholder="账号、昵称或完整账号 ID" onChange={e => { setQuery(e.target.value); setSelected(null); }} /></label>
        {searching ? <p className="muted">正在搜索…</p> : matches.length > 0 ? <div className="admin-gift-matches">
          {matches.map(item => <button className="button small" type="button" key={item.id}
            aria-pressed={selected?.id === item.id} onClick={() => { setSelected(item); setPlanId(''); }}>
            {item.name} · {item.display_name} · {item.role === 'merchant' ? '商家' : '顾客'}
          </button>)}
        </div> : query.trim() && <p className="muted">没有匹配的正常顾客或商家账号</p>}
        {selected && <p className="admin-gift-selected">赠送对象：<strong>{selected.name}（{selected.display_name}）</strong>
          <small>账号 ID：{selected.id} · 当前 {selected.balance_points} 积分</small></p>}
        <label>赠送类型<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}>
          <option value="membership">会员</option><option value="points">积分</option></select></label>
        {kind === 'membership' ? <>
          <label>会员套餐<select value={plan?.id ?? ''} disabled={!plans.length} onChange={e => setPlanId(e.target.value)}>
            {!plans.length && <option value="">请先选择账号</option>}
            {plans.map(p => <option key={p.id} value={p.id}>{p.name}（每期 {p.period_months} 个月）</option>)}
          </select></label>
          <label>赠送期数<input type="number" min={1} max={12} step={1} required value={amount}
            onChange={e => setAmount(e.target.value)} /></label>
        </> : <label>赠送积分<input type="number" min={1} max={1_000_000_000} step={1} required value={points}
          onChange={e => setPoints(e.target.value)} /></label>}
        <label>赠送原因<textarea required maxLength={200} rows={2} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <button className="button" type="submit" disabled={!selected || !document}>预览赠送</button>
      </fieldset>
    </form>
    {review && <div className="admin-gift-review" aria-label="赠送确认">
      <strong>确认向 {review.account.name}（{review.account.display_name}）赠送 {review.label}</strong>
      <p>账号 ID：{review.payload.user_id}<br />原因：{review.payload.reason}</p>
      {review.attempted && <p>上次请求结果尚未确认，请重试本次赠送；成功的请求不会重复发放。</p>}
      <button className="button" disabled={busy} onClick={() => void confirm()}>
        {busy ? <LoaderCircle size={14} className="spin" /> : <Gift size={14} />}
        {busy ? '正在赠送…' : review.attempted ? '重试本次赠送' : '确认赠送'}</button>
      {!review.attempted && <button className="button small" onClick={() => setReview(null)}>取消</button>}
    </div>}
    <h3>赠送记录</h3>
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr>{['时间', '账号', '赠送内容', '原因', '操作人'].map(h => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{document?.items.map(item => <tr key={item.id}><td>{when(item.created)}</td><td>{item.account_name}</td>
        <td>{item.kind === 'points' ? `${item.points} 积分` : `${item.plan_name} ${item.periods} 期`}</td>
        <td className="admin-gift-reason">{item.reason}</td><td>{item.admin_name}</td></tr>)}</tbody>
    </table></div>
    {!document ? <button className="button small" onClick={() => void reload()}>重新读取赠送记录</button>
      : !document.items.length && <p className="muted">暂无赠送记录</p>}
    {document && <div className="admin-pagination"><span>共 {document.total} 条赠送记录</span>
      <button className="button small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button>
      <button className="button small" disabled={offset + 20 >= document.total} onClick={() => setOffset(offset + 20)}>下一页</button>
    </div>}
  </div>;
}
