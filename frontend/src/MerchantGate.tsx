import { LoaderCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useCustomer } from './customerState';
import { MerchantPage } from './MerchantPage';

/**
 * `/merchant` for signed-in accounts.  A customer is refused, an anonymous
 * visitor gets the shop's own sign-in form, and a merchant opens the console.
 */
export default function MerchantGate() {
  const { account } = useCustomer();
  const navigate = useNavigate();

  if (account.checking) {
    return <section className="account-page"><div className="account-card">
      <h2><LoaderCircle size={17} className="spin" /> 正在验证账号</h2>
      <p>请稍候，正在确认当前登录身份。</p>
    </div></section>;
  }
  if (account.user?.role === 'customer') {
    return <section className="account-page"><div className="account-card">
      <h2>商家后台仅限商家账号访问</h2><p>当前账号为普通顾客，可通过右上角管理账号。</p>
      <button className="button" onClick={() => navigate('/account')}>查看账号</button>
    </div></section>;
  }
  return <MerchantPage />;
}
