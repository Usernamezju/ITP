import { Box, CalendarCheck, CircleHelp, Clock3, Crown, Layers3, MessageSquarePlus, Plus, Settings2, Shirt, Sparkles, Store, X } from 'lucide-react';
import { useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AccountAvatar } from './AccountPage';
import { CustomerProvider, useCustomer } from './customerState';
import { FeedbackDialog } from './FeedbackDialog';

/** The heading each customer address shows; unknown paths fall back to the workspace. */
const TITLES: Record<string, string> = {
  '/': '从一张图，到一个世界', '/tryon': '虚拟试穿', '/outfits': '穿搭推荐',
  '/merchant': '商家后台', '/history': '你的创作记录', '/account': '账户概览',
  '/signin': '每日签到', '/pricing': '会员套餐',
};

const railClass = ({ isActive }: { isActive: boolean }) => (isActive ? 'selected' : '');

/** Addresses with parameters of their own still show one stable page title. */
function titleFor(pathname: string): string {
  if (TITLES[pathname]) return TITLES[pathname];
  if (pathname.startsWith('/merchant/analytics')) return '点击数据详情';
  return TITLES['/'];
}

/**
 * The customer side of the site: one rail, one top bar and one page title
 * shared by every customer URL.  The shell itself holds no business logic;
 * the workspace state lives in the provider so it survives navigation.
 */
export default function CustomerLayout() {
  return <CustomerProvider><CustomerChrome /></CustomerProvider>;
}

function CustomerChrome() {
  const { account, error, setError, newProject, uploadCount } = useCustomer();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [feedback, setFeedback] = useState(false);

  return <div className="app-shell">
    <aside className="rail">
      <a href="/" className="brand" aria-label="ClothiNation 首页"><Box size={27} strokeWidth={1.6} /></a>
      <NavLink to="/" end className={railClass} aria-label="人体建模" title="人体建模"><Layers3 size={21} /></NavLink>
      <NavLink to="/tryon" className={railClass} aria-label="虚拟试穿" title="虚拟试穿"><Shirt size={21} /></NavLink>
      <NavLink to="/outfits" className={railClass} aria-label="穿搭推荐" title="穿搭推荐"><Sparkles size={21} /></NavLink>
      <NavLink to="/merchant" className={railClass} aria-label="商家后台" title="商家后台"><Store size={21} /></NavLink>
      <NavLink to="/history" className={railClass} aria-label="任务记录" title="任务记录"><Clock3 size={21} /></NavLink>
      <NavLink to="/signin" className={railClass} aria-label="每日签到" title="每日签到"><CalendarCheck size={21} /></NavLink>
      <NavLink to="/pricing" className={railClass} aria-label="会员套餐" title="会员套餐"><Crown size={21} /></NavLink>
      <div className="rail-spacer" />
      <NavLink to="/account" className={railClass} aria-label="设置" title="账号与外观设置"><Settings2 size={21} /></NavLink>
      <a href="/docs" target="_blank" rel="noreferrer" aria-label="接口文档" title="接口文档"><CircleHelp size={20} /></a>
      <div className="avatar">IT</div>
    </aside>
    <div className="workspace-shell">
      <header className="topbar"><div className="wordmark">ClothiNation <span>穿搭空间</span><i /> <span className="breadcrumb">发现适合你的穿搭</span></div>
        <div className="topbar-right"><span className="local-badge"><span /> 素材保存在本机</span>
          <button className="button small" onClick={newProject} disabled={uploadCount > 0}><Plus size={14} /> 新建资产</button>
          <button type="button" className="feedback-trigger" aria-haspopup="dialog"
            aria-label="意见反馈" onClick={() => setFeedback(true)}>
            <MessageSquarePlus size={16} /><span>反馈</span></button>
          <AccountAvatar user={account.user} checking={account.checking} onAccount={() => navigate('/account')} />
        </div></header>
      <div className={`page-title${pathname === '/account' ? ' account-page-title' : ''}`}><div>
        {pathname !== '/account' && <div className="eyebrow">穿搭 · 试穿 · 人体建模</div>}
        <h1>{titleFor(pathname)}</h1></div></div>
      {error && <div className="error-banner" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError('')}><X size={15} /></button></div>}
      <Outlet />
      {pathname !== '/outfits' && <footer className="statusbar"><span /><span>ClothiNation STUDIO <i>v0.1</i></span></footer>}
    </div>
    <FeedbackDialog open={feedback} user={account.user} onClose={() => setFeedback(false)} />
  </div>;
}
