import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import {
  Activity, AlertCircle, Database, KeyRound, LoaderCircle, LogOut, MessageSquarePlus, Package,
  RefreshCw, ShieldCheck, Sparkles, Users, Wallet,
} from 'lucide-react';
import {
  ApiError, api, type AdminAccount, type AdminFeedback, type AdminJobs, type AdminManualPayment,
  type AdminOrder, type AdminPaymentDocument, type AdminPaymentUpdate, type AdminProductAiDocument,
  type AdminProductAiUpdate, type AdminProviderSettings, type AdminStatus, type AdminUsage,
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
 * holds.  Model credentials stay read-only — they live in the server `.env` and
 * never in a browser — with one deliberate exception: the merchant keys of the
 * real Alipay and WeChat channels can only come from the operator's own payment
 * accounts, so the payment section posts them once to the server, which stores
 * them in its settings file and reports back readiness only.  The console is
 * reachable only by an account whose role is `admin`, which exists solely
 * because an operator ran `scripts/create_admin.py`.
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
        <div><strong>ClothiNation 系统管理后台</strong><small>平台运维控制台 · 仅支付配置可写</small></div>
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
      <section className="admin-section wide">
        <h2 className="admin-section-title"><MessageSquarePlus size={15} />用户反馈</h2>
        <FeedbackSection />
      </section>
      <Section title="平台数据统计" icon={<Database size={15} />} loadable={sections.usage}
        render={(data) => <UsageSection usage={data} />} />
      <Section title="订单与支付" icon={<Wallet size={15} />} loadable={sections.orders}
        render={(data) => <OrdersSection orders={data} />} />
      <Section title="服务端任务" icon={<Package size={15} />} loadable={sections.jobs}
        render={(data) => <JobsSection jobs={data} />} />
      {/* Ten provider blocks would stretch any column to twice its neighbours. */}
      <Section wide title="模型服务配置（只读）" icon={<KeyRound size={15} />}
        loadable={sections.settings} render={(data) => <SettingsSection settings={data} />} />
      <section className="admin-section wide">
        <h2 className="admin-section-title"><Wallet size={15} />支付配置（可写）</h2>
        <PaymentConfigSection onSaved={() => void load()} />
      </section>
      <section className="admin-section wide">
        <h2 className="admin-section-title"><Sparkles size={15} />AI 识图配置（可写）</h2>
        <ProductAiSection onSaved={() => void load()} />
      </section>
      <section className="admin-section wide">
        <h2 className="admin-section-title"><Wallet size={15} />待确认人工支付订单</h2>
        <ManualOrdersSection />
      </section>
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
    { number: '10', title: '商品图片识别', detail: '商家粘贴商品链接后自动填写卡片字段',
      rows: [
        { label: '服务地址', value: settings.product_ai_endpoint },
        { label: '模型', value: settings.product_ai_model },
        { label: 'API Key', value: String(settings.product_ai_api_key_set), secret: true },
      ] },
    { number: '11', title: '穿搭图片检索', detail: '为穿搭推荐获取真实穿搭图片',
      rows: [
        { label: '当前图源', value: settings.image_provider },
        { label: 'Unsplash Access Key', value: String(settings.unsplash_access_key_set), secret: true },
        { label: 'Pixabay API Key', value: String(settings.pixabay_api_key_set), secret: true },
      ] },
  ];
  return <>
    <p className="admin-note">模型凭据由服务器 <code>.env</code> 管理，本节只读：密钥只显示是否已配置，
      修改模型配置需要运维人员编辑服务器上的 <code>.env</code> 并重启服务；支付凭据在下方「支付配置」中填写。</p>
    <div className="admin-providers">{sections.map((section) =>
      <section className="settings-section" key={section.number}>
        <div className="settings-section-title"><span>{section.number}</span>
          <div><h3>{section.title}</h3><p>{section.detail}</p></div></div>
        <ProviderRows rows={section.rows} />
      </section>)}</div>
  </>;
}

type PaymentFields = AdminPaymentUpdate;
type PaymentField = {
  key: keyof PaymentFields; label: string; hint: string;
  area?: boolean; password?: boolean; configured?: boolean;
};

/**
 * The one writable section of the console.  Merchant credentials can only come
 * from the operator's own Alipay/WeChat accounts, so they are typed here and
 * posted once: the server validates the key material, writes it to its own
 * settings file and reports back only readiness and the official probe result.
 * Nothing the operator typed is ever rendered again.
 */
function PaymentConfigSection({ onSaved }: { onSaved: () => void }) {
  const [document, setDocument] = useState<AdminPaymentDocument | null>(null);
  const [loadError, setLoadError] = useState('');
  const [fields, setFields] = useState<PaymentFields>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Kept apart from the document so the refresh that follows a save cannot
  // wipe the probe results the operator needs to read.
  const [checks, setChecks] = useState<{ channel: string; ok: boolean; message: string }[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadError('');
    try {
      setDocument(await accountApi<AdminPaymentDocument>('/api/admin/payments'));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) sessionToken.write('');
      setLoadError(err instanceof Error ? err.message : '读取失败');
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  function change(key: keyof PaymentFields, value: string) {
    setFields((current) => ({ ...current, [key]: value }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(''); setNotice(''); setChecks([]);
    try {
      const result = await accountApi<AdminPaymentDocument>(
        '/api/admin/payments/config', 'POST', fields);
      setDocument(result);
      setFields({});
      setChecks(result.checks || []);
      const failed = (result.checks || []).filter((check) => !check.ok);
      setNotice(failed.length
        ? `配置已保存到服务器；${failed.map((check) => check.message).join('；')}`
        : '配置已保存到服务器，渠道已重新加载并通过官方接口校验。');
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  async function removeKey(identifier: string) {
    if (busy) return;
    setBusy(true); setError(''); setNotice(''); setChecks([]);
    try {
      setDocument(await accountApi<AdminPaymentDocument>(
        '/api/admin/payments/config', 'POST', { wechat_platform_key_remove: identifier }));
      setNotice(`已移除微信支付公钥 ${identifier}。`);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return <p role="alert" className="admin-error"><AlertCircle size={14} />支付配置读取失败：{loadError}</p>;
  }
  if (!document) return <p className="muted" role="status">正在读取支付配置…</p>;

  const { alipay, wechat } = document.settings;
  const groups: { id: string; title: string; hint: string; fields: PaymentField[] }[] = [
    { id: 'alipay', title: '支付宝', hint: '在支付宝开放平台创建网页/APP 支付应用后获取',
      fields: [
        { key: 'alipay_app_id', label: 'APP_ID', hint: '应用 APPID', configured: alipay.app_id_set },
        { key: 'alipay_seller_id', label: 'SELLER_ID', hint: '商户 UID（2088 开头）', configured: alipay.seller_id_set },
        { key: 'alipay_private_key', label: '应用私钥', hint: 'PKCS#8 或 PKCS#1，2048 位以上', area: true, configured: alipay.private_key_set },
        { key: 'alipay_public_key', label: '支付宝公钥', hint: '支付宝公钥或应用公钥证书', area: true, configured: alipay.public_key_set },
      ] },
    { id: 'wechat', title: '微信支付', hint: '在微信支付商户平台开通 Native 支付后获取',
      fields: [
        { key: 'wechat_app_id', label: 'APP_ID', hint: '绑定的公众号/应用 APPID', configured: wechat.app_id_set },
        { key: 'wechat_mch_id', label: 'MCH_ID', hint: '微信支付商户号', configured: wechat.mch_id_set },
        { key: 'wechat_merchant_serial', label: '商户证书序列号', hint: '商户 API 证书的序列号', configured: wechat.merchant_serial_set },
        { key: 'wechat_private_key', label: '商户私钥', hint: 'apiclient_key.pem 的内容', area: true, configured: wechat.private_key_set },
        { key: 'wechat_api_v3_key', label: 'APIv3 密钥', hint: '32 位 APIv3 密钥', password: true, configured: wechat.api_v3_key_set },
        { key: 'wechat_platform_key_id', label: '微信支付公钥 ID', hint: '公钥 ID，回调验签用' },
        { key: 'wechat_platform_public_key', label: '微信支付公钥', hint: '公钥或平台证书内容', area: true },
      ] },
  ];
  return <form className="admin-payment" onSubmit={(event) => void submit(event)}>
    <p className="admin-note">凭据只写入服务器配置文件（权限 600），接口只回传“是否已配置”和渠道状态，
      不会把任何密钥返回页面、写进日志或提交到 Git。保存后服务器立即重新加载支付渠道，
      并用一笔探测查询向官方接口校验凭据。</p>
    <div className="admin-payment-status">{document.status.channels.map((channel) =>
      <div className="admin-payment-channel" key={channel.id}>
        <b className={channel.ready ? 'admin-pill ready' : 'admin-pill'}>
          {channel.ready ? '可用' : '未就绪'}</b>
        <div><strong>{channel.id === 'alipay' ? '支付宝' : channel.id === 'wechat' ? '微信支付' : '模拟支付'}</strong>
          <small>{channel.ready ? '服务器已加载该渠道' : channel.reason}</small>
          {checks.filter((check) => check.channel === channel.id).map((check) =>
            <small key={check.channel} className={check.ok ? 'admin-check ok' : 'admin-check bad'}>
              {check.ok ? '✓ ' : '× '}{check.message}</small>)}</div>
      </div>)}</div>
    {document.status.notify_origin
      ? <p className="admin-note">回调地址（在商户平台按此填写）：支付宝 <code>{document.status.callbacks.alipay}</code>，
          微信支付 <code>{document.status.callbacks.wechat}</code>。</p>
      : <p role="alert" className="admin-error"><AlertCircle size={14} />
          服务器尚未配置公网回调地址（ITP_PUBLIC_ORIGIN），支付渠道在补上之前不会启用。</p>}
    {groups.map((group) => <section className="settings-section" key={group.id}>
      <div className="settings-section-title"><span>{group.id === 'alipay' ? '01' : '02'}</span>
        <div><h3>{group.title}</h3><p>{group.hint}</p></div></div>
      <div className="admin-config-fields">{group.fields.map((field) => {
        const value = fields[field.key] ?? '';
        return <label key={field.key} className={field.area ? 'wide' : ''}>
          <span className="admin-config-label">{field.label}
            <i className={field.configured ? 'admin-pill ready' : 'admin-pill'}>
              {field.configured ? '已配置' : '未配置'}</i></span>
          {field.area
            ? <textarea rows={4} spellCheck={false} autoComplete="off" value={value}
                aria-label={field.label} onChange={(event) => change(field.key, event.target.value)} />
            : <input type={field.password ? 'password' : 'text'} spellCheck={false} autoComplete="off"
                value={value} aria-label={field.label}
                onChange={(event) => change(field.key, event.target.value)} />}
          <small>{field.hint}</small>
        </label>;
      })}</div>
      {group.id === 'wechat' && <div className="admin-payment-keys">
        <span>已登记的公钥 ID：</span>
        {wechat.platform_key_ids.length
          ? wechat.platform_key_ids.map((identifier) => <span className="admin-payment-key" key={identifier}>
              <code>{identifier}</code>
              <button type="button" className="text-button" disabled={busy}
                onClick={() => void removeKey(identifier)}>移除</button></span>)
          : <small>尚未登记公钥；请在上面填写公钥 ID 与公钥内容。</small>}
      </div>}
    </section>)}
    {document.manual && <ManualQrFields manual={document.manual} busy={busy}
      onDocument={setDocument} onChanged={onSaved} onError={setError} onNotice={setNotice} />}
    {error && <p role="alert" className="admin-error"><AlertCircle size={14} />{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    <div className="admin-config-actions">
      <button type="submit" className="button" disabled={busy || Object.keys(fields).length === 0}>
        {busy ? <LoaderCircle size={14} className="spin" /> : <KeyRound size={14} />}
        {busy ? '正在保存并校验…' : '保存并校验'}
      </button>
      <small>只保存本次填写过的字段；把某个字段内容清空再保存即可清除该值。</small>
    </div>
  </form>;
}

/**
 * The second writable section: which model reads product pictures.  The
 * operator switches provider or model here, the server stores it in its own
 * settings file and proves the credentials with one tiny vision call.
 */
function ProductAiSection({ onSaved }: { onSaved: () => void }) {
  const [document, setDocument] = useState<AdminProductAiDocument | null>(null);
  const [loadError, setLoadError] = useState('');
  const [fields, setFields] = useState<AdminProductAiUpdate>({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadError('');
    try {
      setDocument(await accountApi<AdminProductAiDocument>('/api/admin/product-ai'));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) sessionToken.write('');
      setLoadError(err instanceof Error ? err.message : '读取失败');
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await accountApi<AdminProductAiDocument>(
        '/api/admin/product-ai/config', 'POST', fields);
      setDocument(result);
      setFields({});
      setNotice(result.check?.ok
        ? `配置已保存，自检通过：${result.check.message}`
        : `配置已保存到服务器；自检未通过：${result.check?.message || '未知原因'}`);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return <p role="alert" className="admin-error"><AlertCircle size={14} />
      AI 配置读取失败：{loadError}</p>;
  }
  if (!document) return <p className="muted" role="status">正在读取 AI 配置…</p>;
  const rows: { key: keyof AdminProductAiUpdate; label: string; hint: string;
    value: string; password?: boolean }[] = [
    { key: 'product_ai_endpoint', label: '视觉模型接口地址',
      hint: 'OpenAI 兼容的 chat/completions 地址，例如 https://ark.cn-beijing.volces.com/api/v3/chat/completions',
      value: document.settings.endpoint },
    { key: 'product_ai_model', label: '模型名',
      hint: '例如 doubao-seed-2-1-lite-260915 / qwen-vl-max / gpt-4o',
      value: document.settings.model },
    { key: 'product_ai_api_key', label: 'API Key（选填）',
      hint: '留空则沿用姿势编辑的密钥；填写后立即生效，不会回显',
      value: '', password: true },
  ];
  return <form className="admin-payment" onSubmit={(event) => void submit(event)}>
    <p className="admin-note">商家粘贴商品链接时用它读图。密钥只写入服务器配置文件（权限 600），
      接口只回传是否已配置；保存后立即生效，并用一张 32×32 的测试图向该模型发起一次真实自检。</p>
    <div className="admin-payment-status">
      <div className="admin-payment-channel">
        <b className={document.settings.ready ? 'admin-pill ready' : 'admin-pill'}>
          {document.settings.ready ? '可用' : '未就绪'}</b>
        <div><strong>{document.settings.model || '未填写模型'}</strong>
          <small>{document.settings.key_set ? '已使用单独的 API Key'
            : document.settings.key_from_pose ? '沿用姿势编辑的密钥'
            : '尚未配置密钥'}</small>
          {document.settings.endpoint && <small>{document.settings.endpoint}</small>}
          {document.check && <small className={document.check.ok ? 'admin-check ok' : 'admin-check bad'}>
            {document.check.ok ? '✓ ' : '× '}{document.check.message}</small>}</div>
      </div>
    </div>
    <div className="admin-config-fields">{rows.map((row) => <label key={row.key}
      className={row.key === 'product_ai_endpoint' ? 'wide' : ''}>
      <span className="admin-config-label">{row.label}</span>
      <input type={row.password ? 'password' : 'text'} spellCheck={false} autoComplete="off"
        placeholder={row.value || row.hint} aria-label={row.label}
        value={fields[row.key] ?? ''}
        onChange={(event) => setFields({ ...fields, [row.key]: event.target.value })} />
      <small>{row.hint}</small>
    </label>)}</div>
    {error && <p role="alert" className="admin-error"><AlertCircle size={14} />{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    <div className="admin-config-actions">
      <button type="submit" className="button" disabled={busy || Object.keys(fields).length === 0}>
        {busy ? <LoaderCircle size={14} className="spin" /> : <Sparkles size={14} />}
        {busy ? '正在保存并自检…' : '保存并校验'}
      </button>
      <small>只保存本次填写过的字段；把字段清空再保存即可清除该值（密钥清空后回落到姿势编辑的密钥）。</small>
    </div>
  </form>;
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

/**
 * The operator's own WeChat/Alipay collection codes.  A picture alone changes
 * nothing: the channel is offered to payers only after the switch below is on,
 * and even then an order stays pending until this console confirms it.
 */
function ManualQrFields({ manual, busy, onDocument, onChanged, onError, onNotice }: {
  manual: AdminManualPayment; busy: boolean; onDocument: (document: AdminPaymentDocument) => void;
  onChanged: () => void; onError: (message: string) => void; onNotice: (message: string) => void;
}) {
  const [uploading, setUploading] = useState('');
  const [working, setWorking] = useState(false);
  const channels: { id: 'manual_wechat' | 'manual_alipay'; hint: string }[] = [
    { id: 'manual_wechat', hint: '微信「我 → 服务 → 收付款 → 二维码收款」保存的图片' },
    { id: 'manual_alipay', hint: '支付宝「收钱」页面保存的收款码图片' },
  ];

  async function run(action: () => Promise<void>) {
    if (working || busy) return;
    setWorking(true); onError(''); onNotice('');
    try { await action(); } catch (err) { onError(err instanceof Error ? err.message : '操作失败'); }
    finally { setWorking(false); }
  }

  function upload(channel: string, file: File | undefined) {
    if (!file) return;
    const body = new FormData();
    // The route names channels without the provider prefix: wechat / alipay.
    body.append('channel', channel.replace('manual_', ''));
    body.append('file', file);
    void run(async () => {
      setUploading(channel);
      try {
        const updated = await api<AdminPaymentDocument>('/api/admin/payments/manual/qr',
          { method: 'POST', body });
        onDocument(updated);
        onNotice('收款码已上传；确认无误后再打开人工收款开关。');
        onChanged();
      } finally { setUploading(''); }
    });
  }

  return <section className="settings-section admin-manual">
    <div className="settings-section-title"><span>03</span>
      <div><h3>人工收款码</h3>
        <p>上传个人收款码作为备用通道：顾客照旧下单并看到二维码，但订单只能由管理员核实到账后确认。</p></div></div>
    <div className="admin-config-fields">
      <label className="wide admin-manual-switch">
        <span className="admin-config-label">人工收款
          <i className={manual.enabled ? 'admin-pill ready' : 'admin-pill'}>
            {manual.enabled ? '已启用' : '未启用'}</i></span>
        <input type="checkbox" checked={manual.enabled} disabled={working || busy}
          aria-label="启用人工收款"
          onChange={(event) => void run(async () => {
            onDocument(await accountApi<AdminPaymentDocument>('/api/admin/payments/config', 'POST',
              { payment_manual_enabled: event.target.checked }));
            onNotice(event.target.checked
              ? '人工收款已启用：上传了收款码的渠道会出现在顾客的支付方式里。'
              : '人工收款已关闭：不再创建新的人工订单，已有订单仍可确认。');
            onChanged();
          })} />
        <small>关闭后顾客不能再选择人工收款；已经下单的订单不受影响，仍可确认到账。</small>
      </label>
      {channels.map(({ id, hint }) => {
        const channel = manual.channels[id];
        return <div key={id} className="wide admin-manual-channel">
          <div className="admin-manual-preview">
            {channel?.qr_set
              ? <img src={`/api/payments/manual/qr/${channel.qr_key}`} alt={`${channel.label}收款码`} />
              : <span className="admin-manual-empty">未上传</span>}
          </div>
          <div className="admin-manual-copy">
            <span className="admin-config-label">{channel?.label || id}收款码
              <i className={channel?.qr_set ? 'admin-pill ready' : 'admin-pill'}>
                {channel?.qr_set ? '已上传' : '未上传'}</i></span>
            <small>{hint}</small>
            <small>{channel?.qr_set
              ? `上传时间：${when(channel.updated)}${channel.ready ? '' : ` · ${channel.reason}`}`
              : '上传后立即生效，替换会换一个新的图片地址。'}</small>
            <span className="admin-manual-actions">
              <label className="button small">
                {uploading === id ? '正在上传…' : '上传/替换'}
                <input type="file" accept="image/png,image/jpeg,image/webp"
                  aria-label={`上传${channel?.label || id}收款码`} disabled={working || busy}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    upload(id, file);
                  }} />
              </label>
              {channel?.qr_set && <button type="button" className="text-button" disabled={working || busy}
                onClick={() => void run(async () => {
                  if (!window.confirm(`移除${channel.label}收款码？已创建的订单不受影响。`)) return;
                  onDocument(await accountApi<AdminPaymentDocument>('/api/admin/payments/config', 'POST',
                    { payment_manual_clear: id.slice(7) }));
                  onNotice(`${channel.label}收款码已移除。`);
                  onChanged();
                })}>移除</button>}
            </span>
          </div>
        </div>;
      })}
    </div>
  </section>;
}

/** Pending manual orders: the only place a manual transfer becomes money. */
function ManualOrdersSection() {
  const [data, setData] = useState<{ total: number; items: AdminOrder[] } | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      setData(await accountApi<{ total: number; items: AdminOrder[] }>(
        `/api/admin/payments/manual/orders?limit=${FEEDBACK_PAGE}&offset=${offset}`));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) sessionToken.write('');
      setData(null);
      setError(err instanceof Error ? err.message : '读取失败');
    } finally { setLoading(false); }
  }, [offset]);
  useEffect(() => { void load(); }, [load]);

  async function decide(order: AdminOrder, action: 'confirm' | 'reject') {
    if (working) return;
    if (action === 'reject'
      && !window.confirm(`拒绝订单 ${order.id.slice(0, 8)}…？顾客看到的二维码会失效，金额不会入账。`)) return;
    setWorking(order.id); setError(''); setNotice('');
    try {
      await accountApi(`/api/admin/payments/manual/orders/${order.id}/${action}`, 'POST', {});
      setNotice(action === 'confirm'
        ? `订单 ${order.id.slice(0, 8)}… 已确认到账，余额或会员权益已按既有逻辑入账。`
        : `订单 ${order.id.slice(0, 8)}… 已拒绝，未产生任何入账。`);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : '操作失败'); }
    finally { setWorking(''); }
  }

  if (error && !data) return <p role="alert" className="admin-error">
    <AlertCircle size={14} />本节读取失败：{error}</p>;
  if (!data) return <p className="muted">{loading ? '正在读取人工订单…' : '暂时无法读取人工订单'}</p>;
  const pages = Math.max(1, Math.ceil(data.total / FEEDBACK_PAGE));
  return <>
    <p className="admin-note">只有这里能让人工订单入账：服务端在同一个事务里写入流水、按类型入账并标记订单，
      重复确认不会重复入账，顾客刷新订单即可看到结果。</p>
    <Table head={['订单号', '账号', '金额', '类型', '支付渠道', '创建时间', '状态', '处理']}
      empty="没有待确认的人工支付订单"
      rows={data.items.map((order) => [
        <code>{order.id}</code>,
        order.account_name || '—',
        money(order.amount_cents),
        orderKinds[order.kind] || order.kind,
        order.provider === 'manual_wechat' ? '微信收款码' : '支付宝收款码',
        when(order.created),
        <span className="admin-pending">{orderStates[order.state] || order.state}</span>,
        <span className="admin-manual-actions">
          <button type="button" className="button small" disabled={Boolean(working)}
            onClick={() => void decide(order, 'confirm')}>
            {working === order.id ? <LoaderCircle size={13} className="spin" /> : null} 确认到账</button>
          <button type="button" className="text-button" disabled={Boolean(working)}
            onClick={() => void decide(order, 'reject')}>拒绝</button>
        </span>,
      ])} />
    {error && <p role="alert" className="admin-error"><AlertCircle size={14} />{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    <div className="admin-pagination">
      <button type="button" className="button small" disabled={loading || offset === 0}
        onClick={() => setOffset((value) => Math.max(0, value - FEEDBACK_PAGE))}>上一页</button>
      <span>第 {Math.floor(offset / FEEDBACK_PAGE) + 1} / {pages} 页 · 共 {data.total} 条</span>
      <button type="button" className="button small"
        disabled={loading || offset + FEEDBACK_PAGE >= data.total}
        onClick={() => setOffset((value) => value + FEEDBACK_PAGE)}>下一页</button>
    </div>
  </>;
}

/** How many reports one page of the operator inbox holds. */
const FEEDBACK_PAGE = 20;

/**
 * The user feedback inbox.  It loads its own page because it is the one
 * section an operator pages through; nothing here is writable, so a report
 * stays exactly as its author submitted it.
 */
function FeedbackSection() {
  const [data, setData] = useState<AdminFeedback | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {
      setData(await accountApi<AdminFeedback>(
        `/api/admin/feedback?limit=${FEEDBACK_PAGE}&offset=${offset}`));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) sessionToken.write('');
      setData(null);
      setError(err instanceof Error ? err.message : '读取失败');
    } finally {
      setLoading(false);
    }
  }, [offset]);
  useEffect(() => { void load(); }, [load]);

  if (error) return <p role="alert" className="admin-error">
    <AlertCircle size={14} />本节读取失败：{error}</p>;
  if (!data) return <p className="muted">{loading ? '正在读取用户反馈…' : '暂时无法读取反馈'}</p>;
  const pages = Math.max(1, Math.ceil(data.total / FEEDBACK_PAGE));
  return <>
    <Kpis items={[
      { label: '反馈总数', value: `${data.total} 条` },
      { label: '本页显示', value: `${data.items.length} 条` },
    ]} />
    <p className="admin-note">这里只读：反馈按提交时间倒序排列，未登录访客的条目显示为「匿名」，
      账号信息由服务端从登录令牌读取，不是提交内容的一部分。</p>
    <Table head={['提交时间', '账号', '类型', '内容', '联系方式', '来源页面']} empty="还没有用户反馈"
      rows={data.items.map((item) => [
        when(item.created),
        item.account_name || '匿名',
        item.kind,
        <span className="admin-feedback-body">{item.body}</span>,
        item.contact || '—',
        item.page || '—',
      ])} />
    <div className="admin-pagination">
      <button type="button" className="button small" disabled={loading || offset === 0}
        onClick={() => setOffset((value) => Math.max(0, value - FEEDBACK_PAGE))}>上一页</button>
      <span>第 {Math.floor(offset / FEEDBACK_PAGE) + 1} / {pages} 页 · 共 {data.total} 条</span>
      <button type="button" className="button small"
        disabled={loading || offset + FEEDBACK_PAGE >= data.total}
        onClick={() => setOffset((value) => value + FEEDBACK_PAGE)}>下一页</button>
    </div>
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
    {usage.points && <p>积分余额合计：{usage.points.total_balance_points} 积分</p>}
    {usage.point_model_charges && <Table head={['积分建模状态', '次数', '积分']} rows={
      Object.entries(usage.point_model_charges).map(([state, charge]) => [
        chargeStates[state] || state, String(charge.count), `${charge.amount_points} 积分`,
      ])} />}
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
