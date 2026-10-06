import { useState, type FormEvent } from 'react';
import { LoaderCircle, LogOut, UserRound } from 'lucide-react';
import { accountApi, logoutAccount, type Account } from './accountApi';
import { sessionToken } from './session';
import './AccountPage.css';
import { CommercePanel } from './CommercePanel';

export function AccountPage({ user, onChanged }: { user: Account | null; onChanged: () => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [fields, setFields] = useState({ name: '', display_name: '', contact: '', password: '', role: 'customer' });
  const [profile, setProfile] = useState({ display_name: user?.display_name || '', contact: user?.contact || '' });
  const [passwords, setPasswords] = useState({ current: '', next: '', again: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

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

  return <main className="account-page">
    {error && <p role="alert" className="account-error">{error}</p>}
    {notice && <p role="status" className="account-notice">{notice}</p>}
    {!user ? <form className="account-card" onSubmit={authenticate}>
      <h2><UserRound size={18} /> {mode === 'login' ? '登录账号' : '创建账号'}</h2>
      <div className="account-tabs"><button type="button" className={mode === 'login' ? 'active' : ''}
        onClick={() => setMode('login')}>登录</button><button type="button" className={mode === 'register' ? 'active' : ''}
        onClick={() => setMode('register')}>注册</button></div>
      <label>账号<input className="text-input" autoComplete="username" required minLength={3} maxLength={32}
        value={fields.name} onChange={(event) => setFields({ ...fields, name: event.target.value })} /></label>
      {mode === 'register' && <>
        <label>昵称 / 商家名称<input className="text-input" required maxLength={40} value={fields.display_name}
          onChange={(event) => setFields({ ...fields, display_name: event.target.value })} /></label>
        <label>联系方式<input className="text-input" maxLength={80} value={fields.contact}
          onChange={(event) => setFields({ ...fields, contact: event.target.value })} /></label>
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
    </form> : <div className="account-columns">
      <CommercePanel user={user} />
      <form className="account-card" onSubmit={saveProfile}><h2>个人资料</h2>
        <div className="account-identity"><span className="account-avatar">{user.display_name.slice(0, 1).toUpperCase()}</span>
          <div><strong>{user.name}</strong><small>{user.role === 'merchant' ? '商家' : '普通顾客'}</small></div></div>
        <label>昵称 / 商家名称<input className="text-input" required maxLength={40} value={profile.display_name}
          onChange={(event) => setProfile({ ...profile, display_name: event.target.value })} /></label>
        <label>联系方式<input className="text-input" maxLength={80} value={profile.contact}
          onChange={(event) => setProfile({ ...profile, contact: event.target.value })} /></label>
        <button className="button" disabled={busy}>保存个人资料</button>
        <button type="button" className="text-button" disabled={busy} onClick={() => void run(async () => {
          await logoutAccount(); onChanged();
        })}><LogOut size={15} />退出登录</button>
      </form>
      <form className="account-card" onSubmit={changePassword}><h2>修改密码</h2>
        <label>当前密码<input className="text-input" type="password" required autoComplete="current-password" value={passwords.current}
          onChange={(event) => setPasswords({ ...passwords, current: event.target.value })} /></label>
        <label>新密码<input className="text-input" type="password" required minLength={8} maxLength={128}
          autoComplete="new-password" value={passwords.next} onChange={(event) => setPasswords({ ...passwords, next: event.target.value })} /></label>
        <label>确认新密码<input className="text-input" type="password" required autoComplete="new-password" value={passwords.again}
          onChange={(event) => setPasswords({ ...passwords, again: event.target.value })} /></label>
        <button className="button" disabled={busy}>修改密码</button>
        <small>修改成功后，所有旧登录状态都会失效。</small>
      </form>
    </div>}
  </main>;
}

export function AccountAvatar({ user, checking, onAccount, onLogout }: {
  user: Account | null; checking: boolean; onAccount: () => void; onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  return <div className="account-menu" onKeyDown={(event) => { if (event.key === 'Escape') setOpen(false); }}
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button type="button" className="account-avatar" disabled={checking}
      aria-label={user ? '打开账号菜单' : '登录或注册'} aria-expanded={user ? open : undefined}
      aria-haspopup={user ? 'menu' : undefined}
      onClick={() => { if (!user) onAccount(); else setOpen(!open); }}>
      {checking ? <LoaderCircle size={15} className="spin" /> : user ? user.display_name.slice(0, 1).toUpperCase() : <UserRound size={17} />}
    </button>
    {user && open && <div className="account-menu-panel" role="menu" aria-label="账号菜单">
      <strong>{user.display_name}</strong>
      <button type="button" role="menuitem" onClick={() => { setOpen(false); onAccount(); }}>账号设置</button>
      <button type="button" role="menuitem" onClick={() => { setOpen(false); onLogout(); }}>退出登录</button>
    </div>}
  </div>;
}
