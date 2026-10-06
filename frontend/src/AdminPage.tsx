import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import {
  Activity, AlertCircle, Database, KeyRound, LoaderCircle, LogOut, Package,
  RefreshCw, ShieldCheck, Users, Wallet,
} from 'lucide-react';
import {
  ApiError, type AdminAccount, type AdminJobs, type AdminOrder, type AdminProviderSettings,
  type AdminStatus, type AdminUsage,
} from './api';
import { accountApi, logoutAccount, useAccountSession, type Account } from './accountApi';
import { stageLabels } from './jobView';
import { yuanText } from './money';
import { sessionToken } from './session';
import { restoreTheme } from './theme';
import './AdminPage.css';

/**
 * The platform operator console at `/admin`.  It is the developer's view of the
 * running system: which model services are configured and reachable, how the
 * customers and shops are using the platform, and which tasks the server still
 * holds.  It is read-only on purpose — credentials live in the server `.env`,
 * never in a browser — and it is reachable only by an account whose role is
 * `admin`, which exists solely because an operator ran `scripts/create_admin.py`.
 */

const roleLabels: Record<string, string> = {
  customer: '顾客', merchant: '商家', admin: '管理员',
};
const providerLabels: Record<string, string> = {
  seedream: '火山引擎 SeedDream 5.0', flux: 'FLUX.2 Pro', flux_max: 'FLUX.2 Max',
  flux_klein: 'FLUX.2 Klein 4B（自建）', flux_klein_9b: 'FLUX.2 Klein 9B（自建）',
  gpt_image: 'GPT Image 2',
};
const jobStates: Record<string, string> = {
  queued: '等待处理', running: '正在生成', submitting: '正在提交', awaiting_review: '等待确认姿势',
  ready: '已完成', succeeded: '已完成', failed: '失败', rejected: '已放弃',
};
const chargeStates: Record<string, string> = {
  reserved: '进行中预扣', completed: '已完成扣费', refunded: '已退款',
};
const orderStates: Record<string, string> = {
  created: '待支付', submitting: '提交中', pending: '支付确认中', paid: '已支付', uncertain: '状态未知',
};
const ledgerKinds: Record<string, string> = {
  recharge: '钱包充值', model_debit: '建模扣费', model_refund: '建模退款',
};
const orderKinds: Record<string, string> = { recharge: '钱包充值', membership: '会员购买' };

function when(seconds: number | null | undefined): string {
  return seconds ? new Date(seconds * 1000).toLocaleString('zh-CN') : '—';
}

function short(id: string | null | undefined): string {
  return id ? `${id.slice(0, 8)}…` : '—';
}

function money(cents: number): string {
  return `¥${yuanText(cents)}`;
}

type Loadable<T> = { data: T | null; error: string };
type Accounts = { total: number; items: AdminAccount[] };
type Orders = { total: number; items: AdminOrder[] };
type Sections = {
  status: Loadable<AdminStatus>;
  settings: Loadable<AdminProviderSettings>;
  accounts: Loadable<Accounts>;
  usage: Loadable<AdminUsage>;
  orders: Loadable<Orders>;
  jobs: Loadable<AdminJobs>;
};

async function loadSection<T>(path: string): Promise<Loadable<T>> {
  try {
    return { data: await accountApi<T>(path), error: '' };
  } catch (error) {
    // An expired session drops the console back to its sign-in card.
    if (error instanceof ApiError && error.status === 401) sessionToken.write('');
    return { data: null, error: error instanceof Error ? error.message : '读取失败' };
  }
}

export default function AdminPage() {
  const account = useAccountSession();
  // The console has no theme controls of its own; it follows the saved choice.
  useEffect(restoreTheme, []);

  let body: ReactNode;
  if (account.checking) {
    body = <Notice icon={<LoaderCircle size={17} className="spin" />} title="正在验证账号"
      text="请稍候，正在确认当前登录身份。" />;
  } else if (!account.user) {
    body = <AdminLogin onSignedIn={account.refresh} />;
  } else if (account.user.role !== 'admin') {
    body = <Notice icon={<ShieldCheck size={17} />} title="管理员后台仅限管理员账号访问"
      text={`当前账号（${account.user.name}）的角色是${roleLabels[account.user.role] || account.user.role}，没有管理权限。管理员账号由服务器运维在命令行创建。`} />;
  } else {
    body = <AdminDashboard account={account.user} onSignOut={() => { void logoutAccount(); account.refresh(); }} />;
  }

  return <div className="admin-shell">
    <header className="admin-topbar">
      <div className="admin-brand">
        <ShieldCheck size={19} />
        <div><strong>ITP 系统管理后台</strong><small>平台运维控制台 · 只读</small></div>
      </div>
      <nav className="admin-topnav" aria-label="站点导航">
        <a href="/">顾客端</a><a href="/merchant">商家端</a><a href="/docs">接口文档</a>
      </nav>
    </header>
    {body}
  </div>;
}

function Notice({ icon, title, text, action }: {
  icon: ReactNode; title: string; text: string; action?: ReactNode;
}) {
  return <main className="admin-main admin-gate"><div className="admin-card">
    <h2>{icon} {title}</h2><p>{text}</p>{action}
  </div></main>;
}

function AdminLogin({ onSignedIn }: { onSignedIn: () => void }) {
  const [fields, setFields] = useState({ name: '', password: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const data = await accountApi<{ access_token: string }>('/api/auth/login', 'POST', {
        name: fields.name.trim(), password: fields.password,
      });
      sessionToken.write(data.access_token);
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setBusy(false);
    }
  };

  return <main className="admin-main admin-gate">
    <form className="admin-card admin-login" onSubmit={(event) => void submit(event)}>
      <h2><KeyRound size={17} /> 管理员登录</h2>
      <p>使用管理员账号登录。管理员账号由服务器运维通过 <code>scripts/create_admin.py</code> 创建，
        本页不提供注册入口。</p>
      <label htmlFor="admin-name">账号</label>
      <input id="admin-name" className="text-input" required autoComplete="username"
        value={fields.name} onChange={(event) => setFields({ ...fields, name: event.target.value })} />
      <label htmlFor="admin-password">密码</label>
      <input id="admin-password" className="text-input" type="password" required
        autoComplete="current-password" value={fields.password}
        onChange={(event) => setFields({ ...fields, password: event.target.value })} />
      {error && <p role="alert" className="admin-error">{error}</p>}
      <button className="generate-button" type="submit" disabled={busy}>
        {busy ? <LoaderCircle size={15} className="spin" /> : <ShieldCheck size={15} />} 进入管理后台
      </button>
    </form>
  </main>;
}

function AdminDashboard({ account, onSignOut }: { account: Account; onSignOut: () => void }) {
  const [sections, setSections] = useState<Sections | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    const [status, settings, accounts, usage, orders, jobs] = await Promise.all([
      loadSection<AdminStatus>('/api/admin/status'),
      loadSection<AdminProviderSettings>('/api/admin/settings'),
      loadSection<Accounts>('/api/admin/accounts'),
      loadSection<AdminUsage>('/api/admin/usage'),
      loadSection<Orders>('/api/admin/orders'),
      loadSection<AdminJobs>('/api/admin/jobs'),
    ]);
    // Each section keeps its own error, so one failing endpoint leaves the rest readable.
    setSections({ status, settings, accounts, usage, orders, jobs });
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  return <main className="admin-main">
    <div className="admin-heading">
      <div>
        <span className="eyebrow">ADMIN CONSOLE</span>
        <h1>系统状态与平台数据</h1>
        <p>已登录：{account.display_name || account.name}（{account.name} · 管理员）</p>
      </div>
      <div className="admin-actions">
        <button className="button small" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> 刷新
        </button>
        <button className="button small" onClick={onSignOut}><LogOut size={14} /> 退出登录</button>
      </div>
    </div>
    {!sections ? <p className="muted">正在读取系统状态…</p> : <div className="admin-grid">
      <Section title="运行状态" icon={<Activity size={15} />} loadable={sections.status}
        render={(data) => <StatusSection status={data} />} />
      <Section title="账号与商户" icon={<Users size={15} />} loadable={sections.accounts}
        render={(data) => <AccountsSection accounts={data} />} />
      <Section title="平台数据统计" icon={<Database size={15} />} loadable={sections.usage}
        render={(data) => <UsageSection usage={data} />} />
      <Section title="订单与支付" icon={<Wallet size={15} />} loadable={sections.orders}
        render={(data) => <OrdersSection orders={data} />} />
      <Section title="服务端任务" icon={<Package size={15} />} loadable={sections.jobs}
        render={(data) => <JobsSection jobs={data} />} />
      {/* Ten provider blocks would stretch any column to twice its neighbours. */}
      <Section wide title="模型服务配置（只读）" icon={<KeyRound size={15} />}
        loadable={sections.settings} render={(data) => <SettingsSection settings={data} />} />
    </div>}
  </main>;
}

function Section<T>({ title, icon, loadable, render, wide = false }: {
  title: string; icon: ReactNode; loadable: Loadable<T>; render: (data: T) => ReactNode;
  wide?: boolean;
}) {
  return <section className={`admin-section${wide ? ' wide' : ''}`}>
    <h2 className="admin-section-title">{icon}{title}</h2>
    {loadable.data
      ? render(loadable.data)
      : <p role="alert" className="admin-error"><AlertCircle size={14} />
          本节读取失败：{loadable.error || '未知错误'}</p>}
  </section>;
}

function Kpis({ items }: { items: { label: string; value: string }[] }) {
  return <div className="admin-kpis">{items.map((item) =>
    <div className="admin-kpi" key={item.label}>
      <span>{item.label}</span><strong>{item.value}</strong>
    </div>)}</div>;
}

function Table({ head, rows, empty = '暂无记录' }: {
  head: string[]; rows: ReactNode[][]; empty?: string;
}) {
  return <div className="admin-table-wrap"><table className="admin-table">
    <thead><tr>{head.map((cell) => <th key={cell}>{cell}</th>)}</tr></thead>
    <tbody>{rows.length
      ? rows.map((cells, index) => <tr key={index}>{cells.map((cell, cellIndex) =>
          <td key={cellIndex}>{cell}</td>)}</tr>)
      : <tr><td colSpan={head.length} className="muted">{empty}</td></tr>}</tbody>
  </table></div>;
}

function StatusSection({ status }: { status: AdminStatus }) {
  const { services } = status;
  const faceverse = services.faceverse;
  return <>
    <Kpis items={[
      { label: '平台版本', value: status.version },
      { label: '3D 生成 · 混元 AI3D', value: services.geometry ? '已配置' : '未配置' },
      { label: '姿势编辑 · 千问', value: services.pose ? '已配置' : '未配置' },
      { label: '抠图权重', value: services.segmentation ? '已安装' : '未安装' },
      { label: '穿搭图片源', value: services.outfit_images || '未配置' },
      { label: 'FaceVerse 头脸重建',
        value: faceverse.reachable ? `可用 · ${faceverse.model}`
          : faceverse.configured ? '已配置但不可达' : '未配置' },
    ]} />
    <dl className="admin-facts">
      <div><dt>FaceVerse 状态</dt><dd>{faceverse.status || '—'}</dd></div>
      <div><dt>FaceVerse CUDA</dt><dd>{faceverse.cuda || '—'}</dd></div>
      <div><dt>FaceVerse GPU</dt><dd>{faceverse.gpu || '—'}</dd></div>
    </dl>
    <h3 className="admin-subtitle">试穿模型</h3>
    <Table head={['标识', '服务', '可用', '模型']} rows={
      Object.entries(services.tryon).map(([name, service]) => [
        name, providerLabels[name] || name,
        <span className={service.ready ? 'admin-ok' : 'admin-off'}>{service.ready ? '可用' : '未配置'}</span>,
        service.model || '—',
      ])
    } />
    <h3 className="admin-subtitle">自建推理服务</h3>
    <Table head={['服务', '可用', '模型']} rows={[
      ['FLUX.2 Klein 4B', <span className={services.flux_klein.ready ? 'admin-ok' : 'admin-off'}>
        {services.flux_klein.ready ? '可用' : '不可达'}</span>, services.flux_klein.model || '—'],
      ['FLUX.2 Klein 9B', <span className={services.flux_klein_9b.ready ? 'admin-ok' : 'admin-off'}>
        {services.flux_klein_9b.ready ? '可用' : '不可达'}</span>, services.flux_klein_9b.model || '—'],
    ]} />
    <h3 className="admin-subtitle">支付方式</h3>
    <Table head={['通道', '名称', '可用']} rows={status.payments.map((method) => [
      method.id, method.name,
      <span className={method.ready ? 'admin-ok' : 'admin-off'}>{method.ready ? '已配置' : '未配置'}</span>,
    ])} />
  </>;
}

/** One provider's model names plus whether each secret is present.  Read-only. */
function ProviderRows({ rows }: { rows: { label: string; value: string; secret?: boolean }[] }) {
  return <div className="admin-rows">{rows.map((row) =>
    <div className="admin-row" key={row.label}>
      <span>{row.label}</span>
      {row.secret
        ? <b className={row.value ? 'admin-pill ready' : 'admin-pill'}>{row.value ? '已配置' : '未配置'}</b>
        : <code>{row.value || '未配置'}</code>}
    </div>)}</div>;
}

function SettingsSection({ settings }: { settings: AdminProviderSettings }) {
  const sections: { number: string; title: string; detail: string; rows: { label: string; value: string; secret?: boolean }[] }[] = [
    { number: '01', title: '腾讯云混元 AI3D', detail: '用于 3D 生成、拓扑、纹理、绑骨与 FBX 转换',
      rows: [
        { label: '服务地址', value: settings.tencent_endpoint },
        { label: '地域', value: settings.tencent_region },
        { label: '模型', value: settings.tencent_model },
        { label: 'SecretId', value: String(settings.tencent_secret_id_set), secret: true },
        { label: 'SecretKey', value: String(settings.tencent_secret_key_set), secret: true },
      ] },
    { number: '02', title: '阿里云千问图像编辑', detail: '用于 A-Pose、T-Pose 和自定义姿势编辑',
      rows: [
        { label: '服务地址', value: settings.pose_endpoint },
        { label: '模型', value: settings.pose_model },
        { label: 'API Key', value: String(settings.pose_api_key_set), secret: true },
      ] },
    { number: '03', title: '火山引擎 SeedDream 5.0', detail: '用于独立虚拟试穿页面的六视图换装',
      rows: [
        { label: '服务地址', value: settings.seedream_endpoint },
        { label: '模型', value: settings.seedream_model },
        { label: 'API Key', value: String(settings.seedream_api_key_set), secret: true },
      ] },
    { number: '04', title: 'FLUX.2 Pro', detail: '官方 BFL 或兼容的多参考图接口',
      rows: [
        { label: '服务地址', value: settings.flux_endpoint },
        { label: '模型', value: settings.flux_model },
        { label: 'API Key', value: String(settings.flux_api_key_set), secret: true },
      ] },
    { number: '05', title: 'FLUX.2 Max', detail: '官方 BFL 或兼容的多参考图接口',
      rows: [
        { label: '服务地址', value: settings.flux_max_endpoint },
        { label: '模型', value: settings.flux_max_model },
        { label: 'API Key', value: String(settings.flux_max_api_key_set), secret: true },
      ] },
    { number: '06', title: 'FLUX.2 Klein 4B · 自建服务', detail: '独立推理服务，最多四张参考图',
      rows: [
        { label: '服务地址', value: settings.flux_klein_endpoint },
        { label: '模型', value: settings.flux_klein_model },
        { label: 'API Key', value: String(settings.flux_klein_api_key_set), secret: true },
      ] },
    { number: '07', title: 'FLUX.2 Klein 9B · 自建服务', detail: '多参考图换装，使用独立的 9B 推理服务',
      rows: [
        { label: '服务地址', value: settings.flux_klein_9b_endpoint },
        { label: '模型', value: settings.flux_klein_9b_model },
        { label: 'API Key', value: String(settings.flux_klein_9b_api_key_set), secret: true },
      ] },
    { number: '08', title: 'GPT Image 2', detail: '多图编辑；可填写官方接口或兼容的 HTTPS 服务地址',
      rows: [
        { label: '服务地址', value: settings.gpt_image_endpoint },
        { label: '模型', value: settings.gpt_image_model },
        { label: 'API Key', value: String(settings.gpt_image_api_key_set), secret: true },
      ] },
    { number: '09', title: 'FaceVerse 远程服务器', detail: '用于 3D 完成后的高精度头脸重建与网格融合',
      rows: [
        { label: '服务地址', value: settings.faceverse_endpoint },
        { label: '模型', value: settings.faceverse_model },
        { label: 'API Key', value: String(settings.faceverse_api_key_set), secret: true },
      ] },
    { number: '10', title: '穿搭图片检索', detail: '为穿搭推荐获取真实穿搭图片',
      rows: [
        { label: '当前图源', value: settings.image_provider },
        { label: 'Unsplash Access Key', value: String(settings.unsplash_access_key_set), secret: true },
        { label: 'Pixabay API Key', value: String(settings.pixabay_api_key_set), secret: true },
      ] },
  ];
  return <>
    <p className="admin-note">凭据由服务器 <code>.env</code> 管理，此页只读：密钥只显示是否已配置，
      修改配置需要运维人员编辑服务器上的 <code>.env</code> 并重启服务。</p>
    <div className="admin-providers">{sections.map((section) =>
      <section className="settings-section" key={section.number}>
        <div className="settings-section-title"><span>{section.number}</span>
          <div><h3>{section.title}</h3><p>{section.detail}</p></div></div>
        <ProviderRows rows={section.rows} />
      </section>)}</div>
  </>;
}

function AccountsSection({ accounts }: { accounts: Accounts }) {
  return <>
    <Kpis items={[{ label: '账号总数', value: `${accounts.total} 个` }]} />
    <Table head={['账号', '昵称', '角色', '商品', '上传额度', '状态', '注册时间']} rows={
      accounts.items.map((item) => [
        item.name, item.display_name || '—', roleLabels[item.role] || item.role,
        String(item.garment_count), String(item.quota),
        item.disabled ? <span className="admin-off">已禁用</span> : <span className="admin-ok">正常</span>,
        when(item.created),
      ])
    } />
  </>;
}

function UsageSection({ usage }: { usage: AdminUsage }) {
  const charges = Object.entries(usage.model_charges);
  return <>
    <Kpis items={[
      { label: '钱包账号', value: `${usage.wallets.count} 个` },
      { label: '钱包余额合计', value: money(usage.wallets.total_balance_cents) },
      { label: '商品', value: `${usage.garments.total} 件 · 已发布 ${usage.garments.published || 0} 件` },
      { label: '穿搭方案', value: `${usage.looks.total} 套 · 已发布 ${usage.looks.published || 0} 套` },
    ]} />
    <h3 className="admin-subtitle">模型扣费</h3>
    <Table head={['状态', '次数', '金额']} rows={charges.map(([state, charge]) => [
      chargeStates[state] || state, String(charge.count), money(charge.amount_cents),
    ])} />
    <h3 className="admin-subtitle">订单状态</h3>
    <Table head={['状态', '数量', '金额']} rows={
      Object.entries(usage.orders).map(([state, charge]) => [
        orderStates[state] || state, String(charge.count), money(charge.amount_cents),
      ])
    } />
    <h3 className="admin-subtitle">最近钱包流水（20 条）</h3>
    <Table head={['时间', '账号', '类型', '变动', '余额']} rows={
      usage.recent_ledger.map((entry) => [
        when(entry.created), entry.account_name || short(entry.user_id),
        ledgerKinds[entry.kind] || entry.kind,
        `${entry.delta_cents >= 0 ? '+' : ''}${money(entry.delta_cents)}`,
        money(entry.balance_cents),
      ])
    } />
  </>;
}

function OrdersSection({ orders }: { orders: Orders }) {
  return <>
    <Kpis items={[{ label: '订单总数', value: `${orders.total} 笔` }]} />
    <Table head={['订单号', '账号', '类型', '金额', '状态', '创建时间']} rows={
      orders.items.map((order) => [
        short(order.id), order.account_name || short(order.user_id),
        orderKinds[order.kind] || order.kind, money(order.amount_cents),
        <span className={order.state === 'paid' ? 'admin-ok' : ''}>
          {orderStates[order.state] || order.state}</span>,
        when(order.created),
      ])
    } />
  </>;
}

function JobsSection({ jobs }: { jobs: AdminJobs }) {
  const steps = (job: AdminJobs['jobs'][number]) =>
    job.steps.map((step) => `${stageLabels[step.name] || step.name}·${step.status}`).join('、') || '—';
  return <>
    <p className="admin-note">{jobs.note}</p>
    <h3 className="admin-subtitle">人体建模任务（{jobs.jobs.length}）</h3>
    <Table head={['任务', '账号', '状态', '步骤', '创建时间', '更新时间']} rows={
      jobs.jobs.map((job) => [
        short(job.id), short(job.owner_id),
        <span className={`state ${job.state}`}>{jobStates[job.state] || job.state}</span>,
        steps(job), when(job.created), when(job.updated),
      ])
    } />
    <h3 className="admin-subtitle">虚拟试穿任务（{jobs.tryons.length}）</h3>
    <Table head={['任务', '账号', '状态', '模型', '创建时间']} rows={
      jobs.tryons.map((item) => [
        short(item.id), short(item.owner_id),
        <span className={`state ${item.state}`}>{jobStates[item.state] || item.state}</span>,
        item.model || '—', when(item.created),
      ])
    } />
    <h3 className="admin-subtitle">脸部精修任务（{jobs.face_refinements.length}）</h3>
    <Table head={['任务', '账号', '状态', '模型', '创建时间']} rows={
      jobs.face_refinements.map((item) => [
        short(item.id), short(item.owner_id),
        <span className={`state ${item.state}`}>{jobStates[item.state] || item.state}</span>,
        item.model || '—', when(item.created),
      ])
    } />
  </>;
}
