import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { accountApi } from './accountApi';
import { useCustomer } from './customerState';
import type { PointSummary } from './PointsPanel';
import './Benefits.css';

const states: Record<string, string> = { signed: '已签到', today: '今天待签', missed: '漏签',
  supplemented: '已补签', future: '未开始' };

export default function SigninPage() {
  const { account } = useCustomer();
  const [data, setData] = useState<PointSummary | null>(null);
  const [month, setMonth] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [target, setTarget] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!account.user) return;
    let active = true;
    void accountApi<PointSummary>(`/api/account/points${month ? `?month=${month}` : ''}`)
      .then((d) => { if (active) { setData(d); setError(''); } })
      .catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [account.user?.id, month, refresh]);
  async function sign(day?: string) {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await accountApi<{ points: number; replayed: boolean }>('/api/account/signin', 'POST', { day });
      setNotice(result.replayed ? '今天已经领取过奖励' : `${day ? '补签' : '签到'}成功，获得 ${result.points} 积分`);
      dialog.current?.close(); setTarget(''); setRefresh((n) => n + 1);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function changeMonth(delta: number) {
    const date = new Date(`${data?.month || month}-01T12:00:00`);
    date.setMonth(date.getMonth() + delta);
    setMonth(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`);
  }
  if (!account.user) return <main className="benefits-page"><p>登录后查看签到与积分。</p><Link className="button" to="/account">登录账号</Link></main>;
  return <main className="benefits-page"><section className="account-card signin-card" aria-label="签到月历">
    <div className="commerce-heading"><h2>每日签到</h2><button type="button" className="text-button"
      onClick={() => setRefresh((n) => n + 1)}>刷新签到</button></div>
    {error && <p role="alert" className="account-error">{error}，请重试。</p>}
    {notice && <p role="status" className="account-notice">{notice}</p>}
    {data ? <>
      <div className="signin-stats"><div><span>积分余额</span><strong>{data.balance_points}</strong></div>
        <div><span>连续签到</span><strong>{data.streak} 天</strong></div>
        <div><span>补签卡</span><strong>{data.cards} 张</strong></div>
        <div><span>会员月全勤</span><strong>{data.attendance_count} / {data.attendance_required} 天</strong></div></div>
      <p>{data.member ? '月会员每日签到 50 积分，每周期 5 张补签卡，全勤额外 100 积分。'
        : data.first_month ? '注册首月每日签到 10 积分；首月结束后已获积分保留。' : '首月免费福利已结束，开通月会员后可以签到。'}</p>
      <button className="button" disabled={busy || !data.signin_points || data.days.some((d) => d.day === data.today && ['signed', 'supplemented'].includes(d.state))}
        onClick={() => void sign()}>{busy ? '正在签到…' : `今日签到 · ${data.signin_points} 积分`}</button>
      <div className="calendar-heading"><button type="button" className="text-button" onClick={() => changeMonth(-1)}>上个月</button>
        <h3>{data.month}</h3><button type="button" className="text-button" onClick={() => changeMonth(1)}>下个月</button></div>
      <div className="signin-calendar" role="group" aria-label={`${data.month}签到日历`}>
        {['日', '一', '二', '三', '四', '五', '六'].map((day) => <span className="calendar-weekday" key={day}>{day}</span>)}
        {Array.from({ length: new Date(`${data.month}-01T12:00:00`).getDay() }, (_, i) => <span key={`blank${i}`} />)}
        {data.days.map((day) => <button type="button" key={day.day} className={`calendar-day ${day.state}`}
          disabled={!day.can_makeup || busy} aria-label={`${day.day} ${states[day.state]}${day.can_makeup ? '，可补签' : ''}`}
          onClick={() => { setTarget(day.day); dialog.current?.showModal(); }}>
          <strong>{Number(day.day.slice(-2))}</strong><small>{states[day.state]}</small></button>)}
      </div>
      <p className="calendar-legend">已签到 · 今天待签 · 漏签 · 已补签 · 未开始</p>
      <small>签到按上海时间计算。补签只限当前有效会员周期，成功扣 1 张卡并计入全勤；每周期重发，卡片不结转。会员优先于首月福利，不重复领奖。</small>
      <Link className="text-button" to="/pricing">查看会员套餐</Link>
    </> : <p role="status">正在读取签到记录…</p>}
    <dialog ref={dialog} className="makeup-dialog" aria-label="确认补签" onCancel={() => setTarget('')}>
      <h3>确认补签 {target}</h3><p>使用 1 张补签卡，获得 50 积分并计入本会员月全勤。此操作不可撤销。</p>
      {error && <p role="alert">{error}</p>}
      <div className="benefit-actions"><button type="button" className="button" disabled={busy} onClick={() => void sign(target)}>确认使用补签卡</button>
        <button type="button" className="text-button" disabled={busy} onClick={() => { dialog.current?.close(); setTarget(''); }}>取消补签</button></div>
    </dialog>
  </section></main>;
}
