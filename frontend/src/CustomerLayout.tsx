import { Box, CircleHelp, Clock3, Layers3, Plus, Settings2, Shirt, Sparkles, Store, X } from 'lucide-react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AccountAvatar } from './AccountPage';
import { logoutAccount } from './accountApi';
import { CustomerProvider, useCustomer } from './customerState';

/** The heading each customer address shows; unknown paths fall back to the workspace. */
const TITLES: Record<string, string> = {
  '/': '从一张图，到一个世界', '/tryon': '虚拟试穿', '/outfits': '穿搭推荐',
  '/merchant': '商家后台', '/history': '你的创作记录', '/account': '账号设置',
  '/appearance': '外观设置',
};

const railClass = ({ isActive }: { isActive: boolean }) => (isActive ? 'selected' : '');

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

  return <div className="app-shell">
    <aside className="rail">
      <a href="/" className="brand" aria-label="ITP 首页"><Box size={27} strokeWidth={1.6} /></a>
      <NavLink to="/" end className={railClass} aria-label="人体建模" title="人体建模"><Layers3 size={21} /></NavLink>
      <NavLink to="/tryon" className={railClass} aria-label="虚拟试穿" title="虚拟试穿"><Shirt size={21} /></NavLink>
      <NavLink to="/outfits" className={railClass} aria-label="穿搭推荐" title="穿搭推荐"><Sparkles size={21} /></NavLink>
      <NavLink to="/merchant" className={railClass} aria-label="商家后台" title="商家后台"><Store size={21} /></NavLink>
      <NavLink to="/history" className={railClass} aria-label="任务记录" title="任务记录"><Clock3 size={21} /></NavLink>
      <div className="rail-spacer" />
      <NavLink to="/appearance" className={railClass} aria-label="外观" title="外观"><Settings2 size={21} /></NavLink>
      <a href="/docs" target="_blank" rel="noreferrer" aria-label="接口文档" title="接口文档"><CircleHelp size={20} /></a>
      <div className="avatar">IT</div>
    </aside>
    <div className="workspace-shell">
      <header className="topbar"><div className="wordmark">ITP <span>穿搭空间</span><i /> <span className="breadcrumb">发现适合你的穿搭</span></div>
        <div className="topbar-right"><span className="local-badge"><span /> 素材保存在本机</span>
          <button className="button small" onClick={newProject} disabled={uploadCount > 0}><Plus size={14} /> 新建资产</button>
          <AccountAvatar user={account.user} checking={account.checking} onAccount={() => navigate('/account')}
            onLogout={() => { void logoutAccount().catch((err) => setError((err as Error).message)); }} />
        </div></header>
      <div className="page-title"><div><div className="eyebrow">穿搭 · 试穿 · 人体建模</div><h1>{TITLES[pathname] ?? TITLES['/']}</h1></div></div>
      {error && <div className="error-banner" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError('')}><X size={15} /></button></div>}
      <Outlet />
      <footer className="statusbar"><span /><span>ITP STUDIO <i>v0.1</i></span></footer>
    </div>
  </div>;
}
