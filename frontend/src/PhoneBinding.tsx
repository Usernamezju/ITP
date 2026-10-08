import { useEffect, useState } from 'react';
import { accountApi, type Account } from './accountApi';

export function PhoneBinding({ user, onChanged }: { user: Account; onChanged: () => void }) {
  const [phone, setPhone] = useState(user.phone || '');
  const [code, setCode] = useState('');
  const [oldCode, setOldCode] = useState('');
  const [challenge, setChallenge] = useState('');
  const [oldChallenge, setOldChallenge] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const replacing = Boolean(user.phone_verified && phone !== user.phone);
  useEffect(() => { setChallenge(''); setCode(''); }, [phone]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('');
    try { await action(); } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function send(old = false) {
    const answer = await accountApi<{ challenge_id: string }>('/api/account/phone/sms', 'POST',
      { phone: old ? user.phone : phone });
    if (old) setOldChallenge(answer.challenge_id); else setChallenge(answer.challenge_id);
    setMessage('验证码已发送，5 分钟内有效');
  }
  return <section className="phone-binding" aria-label="手机号绑定">
    <h3>手机号绑定</h3>
    <p>{user.phone ? `${user.phone} · ${user.phone_verified ? '已验证' : '未验证'}` : '尚未绑定手机号'}</p>
    <label>绑定手机号<input className="text-input" inputMode="tel" autoComplete="tel-national"
      maxLength={11} pattern="1[3-9][0-9]{9}" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
    {!user.sms_available && <small>短信服务未配置，保存后标记为“未验证”，不影响现有账号登录。</small>}
    {user.sms_available && <>
      {replacing && <><button className="text-button" type="button" disabled={busy}
        onClick={() => void run(() => send(true))}>发送原手机号验证码</button>
        <label>原手机号验证码<input className="text-input" inputMode="numeric" maxLength={6}
          value={oldCode} onChange={(e) => setOldCode(e.target.value)} /></label></>}
      <button type="button" className="text-button" disabled={busy || !/^1[3-9]\d{9}$/.test(phone)}
        onClick={() => void run(() => send())}>发送新手机号验证码</button>
      <label>新手机号验证码<input className="text-input" autoComplete="one-time-code" inputMode="numeric"
        maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} /></label>
    </>}
    {error && <p role="alert" className="account-error">{error}</p>}
    {message && <p role="status">{message}</p>}
    <button className="button account-outline" type="button" disabled={busy || !/^1[3-9]\d{9}$/.test(phone)}
      onClick={() => void run(async () => {
        await accountApi('/api/account/phone', 'PUT', { phone, code, challenge_id: challenge,
          old_code: oldCode, old_challenge_id: oldChallenge });
        setMessage('手机号已保存'); onChanged();
      })}>保存手机号</button>
  </section>;
}
