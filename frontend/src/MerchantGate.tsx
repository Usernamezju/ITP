import { useState, type FormEvent } from 'react';
import { LoaderCircle, Store } from 'lucide-react';
import { accountApi, type Account } from './accountApi';
import { BrandLockup, BrandSpinner } from './BrandLogo';
import { useCustomer } from './customerState';
import { MerchantPage } from './MerchantPage';

/**
 * `/merchant` for signed-in accounts.  A merchant opens the console, an
 * anonymous visitor gets the shop's own sign-in form, and a customer can turn
 * the account they already have into a shop instead of creating a second one.
 */
export default function MerchantGate() {
  const { account } = useCustomer();

  if (account.checking) {
    return <section className="account-page"><div className="account-card account-gate-card">
      <BrandSpinner size={56} />
      <h2>正在验证账号</h2>
      <p>请稍候，正在确认当前登录身份。</p>
    </div></section>;
  }
  if (account.user?.role === 'customer') {
    return <MerchantUpgrade account={account.user} onUpgraded={account.refresh} />;
  }
  return <MerchantPage />;
}

/** One upgrade path: the same account gains the merchant role and keeps the rest. */
function MerchantUpgrade({ account, onUpgraded }: { account: Account; onUpgraded: () => void }) {
  const [fields, setFields] = useState({
    display_name: account.display_name, contact: account.contact,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    void accountApi<Account>('/api/account/merchant', 'POST', fields)
      .then(onUpgraded)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  }

  return <section className="account-page"><div className="account-card merchant-upgrade">
    <BrandLockup className="merchant-upgrade-brand" />
    <h2><Store size={18} /> 注册成为商家</h2>
    <p className="account-card-description">
      把当前账号升级为商家：余额、订单、创作记录都留在同一个账号里。
      升级后在这个页面管理商品、套装和点击数据，同时继续使用顾客端的建模、试穿与穿搭推荐。
    </p>
    <form className="account-profile-form" onSubmit={submit}>
      <label>商家名称<input className="text-input" required maxLength={40} value={fields.display_name}
        onChange={(event) => setFields({ ...fields, display_name: event.target.value })} /></label>
      <label>手机号<input className="text-input" maxLength={80} inputMode="tel" value={fields.contact}
        onChange={(event) => setFields({ ...fields, contact: event.target.value })} /></label>
      {error && <p role="alert" className="account-error">{error}</p>}
      <button className="button primary" disabled={busy}>
        {busy ? <><LoaderCircle size={15} className="spin" />正在升级…</> : '注册成为商家'}</button>
      <small className="account-profile-hint">升级不需要新密码，也不会新建账号；如需使用另一个商家身份，请退出后用其它账号登录。</small>
    </form>
  </div></section>;
}
