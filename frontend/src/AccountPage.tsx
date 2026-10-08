import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { CircleHelp, LoaderCircle, LogOut } from 'lucide-react';
import { accountApi, logoutAccount, removeAvatar, uploadAvatar, type Account } from './accountApi';
import { AvatarImage, DefaultAvatar } from './Avatar';
import { BrandLockup } from './BrandLogo';
import { sessionToken } from './session';
import './AccountPage.css';
import { CommercePanel } from './CommercePanel';
import { PhoneBinding } from './PhoneBinding';
import { PrivateStoragePanel } from './PrivateStoragePanel';

export function AccountPage({ user, onChanged, children }: { user: Account | null; onChanged: () => void; children?: ReactNode }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [fields, setFields] = useState({ name: '', display_name: '', contact: '', password: '', role: 'customer',
    phone: '', sms_challenge_id: '', sms_code: '' });
  const [smsAvailable, setSmsAvailable] = useState(false);
  useEffect(() => { void accountApi<{ sms_available: boolean }>('/api/auth/phone-policy')
    .then((p) => setSmsAvailable(p.sms_available)).catch(() => {}); }, []);
  const [profile, setProfile] = useState({ display_name: user?.display_name || '', contact: user?.contact || '' });
  const [passwords, setPasswords] = useState({ current: '', next: '', again: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editingPassword, setEditingPassword] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await action(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  function authenticate(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      if (mode === 'register') await accountApi('/api/auth/register', 'POST', fields);
      const result = await accountApi<{ access_token: string }>('/api/auth/login', 'POST', {
        name: fields.name.trim(), password: fields.password,
      });
      sessionToken.write(result.access_token);
      setFields((old) => ({ ...old, password: '' }));
      onChanged();
    });
  }
  function saveProfile(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      await accountApi('/api/account/me', 'PATCH', profile);
      setNotice('个人资料已保存'); onChanged();
    });
  }
  function changePassword(event: FormEvent) {
    event.preventDefault();
    if (passwords.next !== passwords.again) { setError('两次输入的新密码不一致'); return; }
    void run(async () => {
      await accountApi('/api/account/password', 'POST', {
        current_password: passwords.current, new_password: passwords.next,
      });
      sessionToken.write('');
      setPasswords({ current: '', next: '', again: '' });
      setNotice('密码已修改，请重新登录'); onChanged();
    });
  }

  async function changeAvatar(file: File | undefined) {
    if (!file || avatarBusy) return;
    if (file.size > 4 * 1024 * 1024) { setError('头像不能超过 4 MiB'); return; }
    setAvatarBusy(true); setError(''); setNotice('');
    try {
      await uploadAvatar(file);
      setNotice('头像已更新'); onChanged();
    } catch (err) { setError((err as Error).message); }
    finally { setAvatarBusy(false); }
  }
  function resetAvatar() {
    if (avatarBusy) return;
    setAvatarBusy(true); setError(''); setNotice('');
    void removeAvatar().then(() => { setNotice('已恢复默认头像'); onChanged(); })
      .catch((err: Error) => setError(err.message))
      .finally(() => setAvatarBusy(false));
  }

  return <main className="account-page">
    <nav className="account-section-nav" aria-label="账号设置分区">
      <a href="#account-information">账户信息与资金</a><a href="#account-appearance">主题设置</a>
    </nav>
    {error && <p role="alert" className="account-error">{error}</p>}
    {notice && <p role="status" className="account-notice">{notice}</p>}
    {!user ? <form id="account-information" className="account-card account-signin-card" tabIndex={-1} onSubmit={authenticate}>
      <BrandLockup className="account-signin-brand" />
      <h2>{mode === 'login' ? '登录账号' : '创建账号'}</h2>
      <p className="account-card-description">衣想国 ClothiNation · 图像到三维的穿搭空间</p>
      <div className="account-tabs"><button type="button" className={mode === 'login' ? 'active' : ''}
        onClick={() => setMode('login')}>登录</button><button type="button" className={mode === 'register' ? 'active' : ''}
        onClick={() => setMode('register')}>注册</button></div>
      <label>账号<input className="text-input" autoComplete="username" required minLength={3} maxLength={32}
        value={fields.name} onChange={(event) => setFields({ ...fields, name: event.target.value })} /></label>
      {mode === 'register' && <>
        <label>昵称 / 商家名称<input className="text-input" required maxLength={40} value={fields.display_name}
          onChange={(event) => setFields({ ...fields, display_name: event.target.value })} /></label>
        <label>手机号<input className="text-input" maxLength={11} pattern="1[3-9][0-9]{9}" inputMode="tel" autoComplete="tel-national" value={fields.phone}
          onChange={(event) => setFields({ ...fields, phone: event.target.value, sms_challenge_id: '', sms_code: '' })} /></label>
        <small>建议绑定中国大陆手机号。{smsAvailable ? '填写号码后请完成短信验证。' : '短信服务未配置，填写后标记为“未验证”。'}</small>
        {smsAvailable && <><button type="button" className="text-button" disabled={busy || !fields.phone || !fields.name}
          onClick={() => void run(async () => {
            const answer = await accountApi<{ challenge_id: string }>('/api/auth/sms', 'POST', { phone: fields.phone, name: fields.name.trim() });
            setFields((old) => ({ ...old, sms_challenge_id: answer.challenge_id })); setNotice('验证码已发送');
          })}>获取注册验证码</button><label>短信验证码<input className="text-input" inputMode="numeric" autoComplete="one-time-code"
          maxLength={6} value={fields.sms_code} onChange={(event) => setFields({ ...fields, sms_code: event.target.value })} /></label></>}
        <label>账号身份<select className="text-input" value={fields.role}
          onChange={(event) => setFields({ ...fields, role: event.target.value })}>
          <option value="customer">普通顾客</option><option value="merchant">商家</option>
        </select></label>
      </>}
      <label>密码<input className="text-input" type="password" required minLength={8} maxLength={128}
        autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={fields.password}
        onChange={(event) => setFields({ ...fields, password: event.target.value })} /></label>
      <button className="button" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" />
        : mode === 'register' ? '注册并登录' : '登录账号'}</button>
    </form> : <>
      <p className="account-welcome">欢迎回来，管理您的账户、资金与外观设置</p>
      <CommercePanel user={user}>
      <section className="account-card account-profile-card" aria-label="个人资料与账号安全">
      <form className="account-profile-form" onSubmit={saveProfile}><h2>个人资料</h2>
        <p className="account-card-description">管理您的个人信息，用于账号识别与联系</p>
        <div className="account-identity"><AvatarImage className="account-avatar" user={user} />
          <div><div className="account-identity-name"><strong>{user.name}</strong>
            <small className="account-role">{user.role === 'admin' ? '管理员' : user.role === 'merchant' ? '商家' : '普通用户'}</small></div>
            <span className="account-display-name">{user.display_name}</span></div></div>
        <div className="account-avatar-actions">
          <label className="button account-outline">{avatarBusy ? '正在上传…' : '上传头像'}
            <input type="file" accept="image/png,image/jpeg,image/webp" disabled={avatarBusy}
              aria-label="上传头像图片"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                void changeAvatar(file);
              }} /></label>
          {user.avatar_key && <button type="button" className="text-button" disabled={avatarBusy}
            onClick={resetAvatar}>恢复默认头像</button>}
          <small>支持 PNG / JPEG / WebP，最大 4 MiB，建议使用正方形图片；上传的图片会重新压缩并去掉拍摄信息。</small>
        </div>
        <label>昵称 / 商家名称<input className="text-input" required maxLength={40} value={profile.display_name}
          onChange={(event) => setProfile({ ...profile, display_name: event.target.value })} /></label>
        <label>联系方式<input className="text-input" maxLength={80} value={profile.contact}
          onChange={(event) => setProfile({ ...profile, contact: event.target.value })} /></label>
        <div className="account-profile-actions"><button className="button primary" disabled={busy}>
          {busy ? <><LoaderCircle size={15} className="spin" />正在处理…</> : '保存个人资料'}</button>
          <button type="button" className="button account-outline" disabled={busy}
            aria-expanded={editingPassword} aria-controls="account-password-form"
            onClick={() => { setEditingPassword(!editingPassword); setPasswords({ current: '', next: '', again: '' }); }}>
            {editingPassword ? '收起密码修改' : '修改密码'}</button></div>
        <small className="account-profile-hint"><CircleHelp size={15} aria-hidden="true" />修改个人资料后将立即生效。</small>
      </form>
      <PhoneBinding user={user} onChanged={onChanged} />
      {editingPassword && <form id="account-password-form" className="account-password-form" onSubmit={changePassword}><h3>修改密码</h3>
        <label>当前密码<input className="text-input" type="password" required autoComplete="current-password" value={passwords.current}
          onChange={(event) => setPasswords({ ...passwords, current: event.target.value })} /></label>
        <label>新密码<input className="text-input" type="password" required minLength={8} maxLength={128}
          autoComplete="new-password" value={passwords.next} onChange={(event) => setPasswords({ ...passwords, next: event.target.value })} /></label>
        <label>确认新密码<input className="text-input" type="password" required autoComplete="new-password" value={passwords.again}
          onChange={(event) => setPasswords({ ...passwords, again: event.target.value })} /></label>
        <button className="button primary" disabled={busy}>确认修改密码</button>
        <small>修改成功后，所有旧登录状态都会失效。</small>
      </form>}
        <button type="button" className="text-button" disabled={busy} onClick={() => void run(async () => {
          await logoutAccount(); onChanged();
        })}><LogOut size={15} />退出登录</button>
      </section>
      </CommercePanel>
      <PrivateStoragePanel />
    </>}
    {children}
  </main>;
}

export function AccountAvatar({ user, checking, onAccount }: {
  user: Account | null; checking: boolean; onAccount: () => void;
}) {
  return <button type="button" className="account-avatar" disabled={checking}
      aria-label={user ? '账户与设置' : '登录或注册'} onClick={onAccount}>
      {checking ? <LoaderCircle size={15} className="spin" />
        : user ? <AvatarImage className="account-avatar-image" user={user} decorative />
          : <DefaultAvatar className="account-avatar-image" />}
    </button>;
}
