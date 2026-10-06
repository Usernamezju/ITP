import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, Box, Check, ChevronRight, CircleHelp,
  Clock3, FileBox, FolderOpen, ImagePlus, Layers3, LoaderCircle, Plus, Settings2, Shirt,
  SlidersHorizontal, Sparkles, Store, Unplug, Upload, X } from 'lucide-react';
import { api, post, fileUrl, type Asset, type Capabilities, type Job, type PoseMode } from './api';
import { SettingsPage } from './SettingsPage';
import { explainJobError } from './errors';
import { applyTheme, loadColorTheme, loadContrastTheme, type ColorTheme, type ContrastTheme } from './theme';
import { Viewer } from './Viewer';
import { TryOnPage } from './TryOnPage';
import { OutfitsPage } from './OutfitsPage';
import { MerchantPage } from './MerchantPage';
import { BodyMetricsPanel } from './BodyMetricsPanel';
import { FaceRefinePanel } from './FaceRefinePanel';

const stageLabels: Record<string, string> = {
  pose: '姿势编辑', geometry: '几何生成', topology: '智能拓扑', texture: 'PBR 纹理', rig: '自动绑骨', export: 'FBX 导出', face_refine: '脸部精修',
};
const stateLabels: Record<string, string> = {
  queued: '等待处理', running: '正在生成', awaiting_review: '等待确认姿势',
  succeeded: '生成完成', failed: '生成失败', rejected: '姿势图已放弃',
};
function jobState(job: Job): { label: string; className: string } {
  if (job.state === 'failed' && job.artifacts.length > 0) {
    return { label: '部分完成', className: 'partial' };
  }
  return { label: stateLabels[job.state] || job.state, className: job.state };
}
function completedStages(job: Job): string {
  return [...new Set(job.artifacts.map((item) => stageLabels[item.stage] || item.stage))].join('、');
}
const modes: { key: PoseMode; label: string }[] = [
  { key: 'original', label: '原始姿势' }, { key: 'a-pose', label: 'A-Pose' },
  { key: 't-pose', label: 'T-Pose' }, { key: 'custom', label: '自定义' },
];

function UploadCard({ label, asset, onChange, onPreview, background, compact = false, onError, onBusy }: {
  label: string; asset?: Asset; onChange: (asset?: Asset) => void; background: boolean;
  onPreview: (asset: Asset, label: string) => void;
  compact?: boolean; onError: (message: string) => void; onBusy: (delta: number) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(false);
  const [drag, setDrag] = useState(false);
  async function upload(file?: File) {
    if (!file || loading) return;
    if (file.size > 10 * 1024 * 1024) { onError('单张图片不能超过 10 MiB'); return; }
    setLoading(true); onBusy(1);
    try {
      const form = new FormData(); form.append('file', file);
      const uploaded = await api<Asset>(`/api/assets?remove_background=${background}`, { method: 'POST', body: form });
      onChange(uploaded);
    } catch (error) { onError((error as Error).message); }
    finally { setLoading(false); onBusy(-1); if (input.current) input.current.value = ''; }
  }
  return <div className={`upload-card ${compact ? 'compact' : ''} ${drag ? 'dragging' : ''} ${asset ? 'has-image' : ''}`}
    onDragOver={(event) => { event.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
    onDrop={(event) => { event.preventDefault(); setDrag(false); void upload(event.dataTransfer.files[0]); }}>
    <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" aria-label={`上传${label}`}
      onChange={(event) => void upload(event.target.files?.[0])} disabled={loading} />
    <button className="upload-hit" onClick={() => asset ? onPreview(asset, label) : input.current?.click()} disabled={loading}
      aria-label={asset ? `放大查看${label}` : `选择${label}`}>
      {asset ? <img src={asset.url} alt={label} /> : <>
        <span className="upload-icon">{compact ? <Plus size={18} /> : <ImagePlus size={26} strokeWidth={1.4} />}</span>
        <strong>{label}</strong>{!compact && <small>拖入图片，或点击上传<br />PNG / JPG / WEBP · 最大 10 MiB</small>}
      </>}
      {loading && <span className="upload-loading"><LoaderCircle className="spin" /> 处理中</span>}
    </button>
    {asset && <><span className="image-label">{label}{asset.background_removed ? ' · 已去背景' : ''}</span>
      <button className="replace-image" title={`更换${label}`} aria-label={`更换${label}`}
        onClick={() => input.current?.click()} disabled={loading}><ImagePlus size={13} /></button>
      <button className="remove-image" title={`移除${label}`} aria-label={`移除${label}`}
        onClick={() => onChange()} disabled={loading}><X size={13} /></button></>}
  </div>;
}

function Toggle({ checked, onChange, title, description, disabled = false }: {
  checked: boolean; onChange: (value: boolean) => void; title: string; description: string; disabled?: boolean;
}) {
  return <label className={`toggle-row ${disabled ? 'disabled' : ''}`}><span><strong>{title}</strong><small>{description}</small></span>
    <input type="checkbox" role="switch" checked={checked} disabled={disabled}
      onChange={(event) => onChange(event.target.checked)} /><span className="switch" /></label>;
}

export default function App() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<'workspace' | 'tryon' | 'outfits' | 'merchant' | 'history' | 'settings'>('workspace');
  const [error, setError] = useState('');
  const [colorTheme, setColorTheme] = useState<ColorTheme>(loadColorTheme);
  const [contrastTheme, setContrastTheme] = useState<ContrastTheme>(loadContrastTheme);
  const [showGenerateIssues, setShowGenerateIssues] = useState(false);
  const [name, setName] = useState('');
  const [front, setFront] = useState<Asset>();
  const [reference, setReference] = useState<Asset>();
  const [views, setViews] = useState<Record<string, Asset | undefined>>({});
  const [viewsConsistent, setViewsConsistent] = useState(false);
  const [poseMode, setPoseMode] = useState<PoseMode>('original');
  const [background, setBackground] = useState(false);
  const [topology, setTopology] = useState(false);
  const [texture, setTexture] = useState(true);
  const [rig, setRig] = useState(false);
  const [neutral, setNeutral] = useState(false);
  const [fbx, setFbx] = useState(false);
  const [faceCount, setFaceCount] = useState(100000);
  const [faceLevel, setFaceLevel] = useState('medium');
  const [polygon, setPolygon] = useState('triangle');
  const [submitting, setSubmitting] = useState(false);
  const [uploadCount, setUploadCount] = useState(0);
  const [localModel, setLocalModel] = useState<{ url: string; name: string } | null>(null);
  const [artifact, setArtifact] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<{ asset: Asset; label: string } | null>(null);
  const importInput = useRef<HTMLInputElement>(null);
  const imageDialog = useRef<HTMLDialogElement>(null);
  const job = jobs.find((item) => item.id === selected);
  const active = jobs.filter((item) => ['queued', 'running', 'awaiting_review'].includes(item.state)).length;

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [capabilities, history] = await Promise.all([
          api<Capabilities>('/api/capabilities'), api<Job[]>('/api/jobs'),
        ]);
        if (!stopped) { setCaps(capabilities); setJobs(history); }
      } catch (err) { if (!stopped) setError(`连接工作台失败：${(err as Error).message}`); }
      finally { if (!stopped) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);
  useEffect(() => () => { if (localModel) URL.revokeObjectURL(localModel.url); }, [localModel]);
  useEffect(() => { applyTheme(colorTheme, contrastTheme); }, [colorTheme, contrastTheme]);
  useEffect(() => {
    if (imagePreview && !imageDialog.current?.open) imageDialog.current?.showModal();
    if (!imagePreview && imageDialog.current?.open) imageDialog.current.close();
  }, [imagePreview]);

  function chooseJob(item: Job) {
    setSelected(item.id); setTab('workspace'); setArtifact(null); setLocalModel(null);
  }
  function newProject() {
    setSelected(null); setArtifact(null); setLocalModel(null); setTab('workspace');
    setName(''); setFront(undefined); setReference(undefined); setViews({}); setViewsConsistent(false); setImagePreview(null);
    setPoseMode('original'); setRig(false); setNeutral(false); setError(''); setShowGenerateIssues(false);
  }
  function changePose(mode: PoseMode) {
    setPoseMode(mode);
    if (mode !== 'original') setViews({});
    if (mode !== 'custom') setReference(undefined);
    if (mode === 'custom') { setRig(false); setNeutral(false); }
  }
  const generateIssues = [
    ...(!front ? ['请上传角色图片'] : []),
    ...(uploadCount ? ['请等待图片上传完成'] : []),
    ...(!caps ? ['请等待本地服务连接'] : !caps.geometry ? ['请在设置页填写腾讯云服务地址、地域、Secret ID 和 Secret Key'] : []),
    ...(poseMode !== 'original' && caps && !caps.pose ? ['请在设置页填写千问服务地址和 API Key'] : []),
    ...(poseMode === 'custom' && !reference ? ['请上传姿势参考图'] : []),
    ...(Object.values(views).some(Boolean) && !viewsConsistent ? ['请确认所有视角为同一人物、同一服装和同一姿势'] : []),
    ...(rig && !neutral ? ['请确认自动绑骨所需的中性姿态'] : []),
  ];
  async function generate() {
    if (submitting) return;
    setShowGenerateIssues(true);
    if (generateIssues.length || !front) return;
    setSubmitting(true); setError('');
    try {
      const created = await post<Job>('/api/jobs', {
        name: name.trim() || '未命名资产', front: front.id,
        views: Object.fromEntries(Object.entries(views).filter(([, value]) => value).map(([key, value]) => [key, value!.id])),
        views_consistent_confirmed: viewsConsistent,
        pose_mode: poseMode, pose_reference: poseMode === 'custom' ? reference?.id : null,
        topology, polygon_type: polygon, face_level: faceLevel, face_count: faceCount,
        texture, rig, neutral_pose_confirmed: neutral, export_fbx: fbx,
      });
      setJobs((list) => [created, ...list]); chooseJob(created);
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }
  async function review(approve: boolean) {
    if (!job) return;
    setSubmitting(true);
    try {
      const updated = await post<Job>(`/api/jobs/${job.id}/review`, { approve });
      setJobs((list) => list.map((item) => item.id === updated.id ? updated : item));
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }
  const generatedGlb = job?.artifacts.filter((item) => item.format === 'GLB').at(-1)?.asset_id;
  const modelUrl = localModel?.url ?? (artifact ? fileUrl(artifact) : generatedGlb ? fileUrl(generatedGlb) : null);
  const planned = ['geometry', ...(topology ? ['topology'] : []), ...(texture ? ['texture'] : []),
    ...(rig ? ['rig'] : []), ...(fbx ? ['export'] : [])];

  return <div className="app-shell">
    <aside className="rail">
      <a href="/" className="brand" aria-label="ITP Studio 首页"><Box size={27} strokeWidth={1.6} /></a>
      <button className={tab === 'workspace' ? 'selected' : ''} aria-label="人体建模" title="人体建模" onClick={() => setTab('workspace')}><Layers3 size={21} /></button>
      <button className={tab === 'tryon' ? 'selected' : ''} aria-label="虚拟试穿" title="虚拟试穿" onClick={() => setTab('tryon')}><Shirt size={21} /></button>
      <button className={tab === 'outfits' ? 'selected' : ''} aria-label="穿搭推荐" title="穿搭推荐" onClick={() => setTab('outfits')}><Sparkles size={21} /></button>
      <button className={tab === 'merchant' ? 'selected' : ''} aria-label="商家后台" title="商家后台" onClick={() => setTab('merchant')}><Store size={21} /></button>
      <button className={tab === 'history' ? 'selected' : ''} aria-label="任务记录" title="任务记录" onClick={() => setTab('history')}><Clock3 size={21} /></button>
      <div className="rail-spacer" />
      <button className={tab === 'settings' ? 'selected' : ''} aria-label="设置" title="设置" onClick={() => setTab('settings')}><Settings2 size={21} /></button>
      <a href="/docs" target="_blank" rel="noreferrer" aria-label="接口文档" title="接口文档"><CircleHelp size={20} /></a>
      <div className="avatar">IT</div>
    </aside>
    <div className="workspace-shell">
      <header className="topbar"><div className="wordmark">ITP <span>STUDIO</span><i /> <span className="breadcrumb">创作空间</span></div>
        <div className="topbar-right"><span className="local-badge"><span /> 本地工作台</span>
          <button className="button small" onClick={newProject} disabled={uploadCount > 0}><Plus size={14} /> 新建资产</button></div></header>
      <div className="page-title"><div><div className="eyebrow">IMAGE TO POSSIBILITY</div><h1>{tab === 'workspace' ? '从一张图，到一个世界' : tab === 'tryon' ? '虚拟试穿' : tab === 'outfits' ? '穿搭推荐' : tab === 'merchant' ? '商家后台' : tab === 'history' ? '你的创作记录' : '服务设置'}</h1></div></div>
      {error && <div className="error-banner" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError('')}><X size={15} /></button></div>}
      {tab === 'settings' ? <SettingsPage onCapabilities={setCaps} colorTheme={colorTheme} contrastTheme={contrastTheme}
        onColorTheme={setColorTheme} onContrastTheme={setContrastTheme} /> : tab === 'tryon' ?
        <TryOnPage caps={caps} onSettings={() => setTab('settings')} onContinue={(created) => { setJobs((list) => [created, ...list]); chooseJob(created); }} /> : tab === 'outfits' ?
        <OutfitsPage caps={caps} jobs={jobs} onSettings={() => setTab('settings')} onModeling={() => setTab('workspace')} /> : tab === 'merchant' ?
        <MerchantPage /> : tab === 'history' ? <section className="history-page">
        <div className="section-heading"><h2>任务记录 <span>{jobs.length}</span></h2><small>{active} 个待处理任务</small></div>
        {!jobs.length ? <div className="history-empty"><FolderOpen size={42} strokeWidth={1} /><h3>第一件作品，从这里开始</h3><p>你的生成任务与中间产物会保存在本地。</p><button className="button" onClick={() => setTab('workspace')}>前往工作台 <ArrowRight size={16} /></button></div> :
          <div className="history-grid">{jobs.map((item) => <button className="history-card" key={item.id} onClick={() => chooseJob(item)}>
            <img src={fileUrl(item.pose_asset || item.request.front)} alt={item.name} /><div><strong>{item.name}</strong><span className={`state ${jobState(item).className}`}>{jobState(item).label}</span><small>{new Date(item.created * 1000).toLocaleString('zh-CN')}</small></div><ChevronRight size={17} />
          </button>)}</div>}
      </section> : <main className="studio-grid">
        <section className="input-panel">
          <div className="panel-heading"><h2><SlidersHorizontal size={16} /> 生成设置</h2><span>01</span></div>
          <div className="form-scroll"><label className="field-label" htmlFor="asset-name">资产名称</label>
            <input id="asset-name" className="text-input" placeholder="为你的灵感命名" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
            <div className="field-heading"><label className="field-label">角色参考</label><span>必选</span></div>
            <UploadCard label="上传角色图片" asset={front} onChange={setFront} onPreview={(asset, label) => setImagePreview({ asset, label })} background={background} onError={setError} onBusy={(d) => setUploadCount((n) => n + d)} />
            <Toggle title="自动去背景" description="应用于之后上传的图片 · 本地处理" checked={background} onChange={setBackground} disabled={!caps?.segmentation} />
            <div className="field-heading"><label className="field-label">姿势控制</label><span>POSE</span></div>
            <div className="pose-tabs">{modes.map((mode) => <button key={mode.key} className={poseMode === mode.key ? 'active' : ''} disabled={uploadCount > 0} onClick={() => changePose(mode.key)}>{mode.label}</button>)}</div>
            {poseMode === 'custom' ? <><UploadCard label="上传姿势参考图" asset={reference} onChange={setReference} onPreview={(asset, label) => setImagePreview({ asset, label })} background={false} onError={setError} onBusy={(d) => setUploadCount((n) => n + d)} /><p className="hint">保留角色外观，参考第二张图的身体姿势。生成的姿势图将由你确认。</p></> :
              <p className="hint">{poseMode === 'original' ? '保留原图姿态。可补充同一姿势的多视角图片。' : '先生成中性姿态参考图，确认后进入 3D 生成。'}</p>}
            {poseMode === 'original' && <><div className="views-row">{[['left', '左视图'], ['right', '右视图'], ['back', '背视图'], ['left_front', '左前 45°'], ['right_front', '右前 45°']].map(([key, label]) => <UploadCard key={key} label={label} asset={views[key]} compact onChange={(value) => setViews((old) => ({ ...old, [key]: value }))} onPreview={(asset, label) => setImagePreview({ asset, label })} background={background} onError={setError} onBusy={(d) => setUploadCount((n) => n + d)} />)}</div>
              {Object.values(views).some(Boolean) && <label className="confirmation"><input type="checkbox" checked={viewsConsistent} onChange={(event) => setViewsConsistent(event.target.checked)} />我确认所有视角为同一人物、同一服装、同一姿势</label>}</>}
            <BodyMetricsPanel jobId={job?.id} />
            <div className="divider" /><div className="field-heading"><label className="field-label">资产处理</label><span>PIPELINE</span></div>
            <label className="select-row">几何目标面数<select aria-label="几何目标面数" value={faceCount} onChange={(event) => setFaceCount(Number(event.target.value))}><option value={30000}>30,000 · 轻量</option><option value={100000}>100,000 · 均衡</option><option value={500000}>500,000 · 精细</option><option value={1500000}>1,500,000 · 极致</option></select></label>
            <Toggle title="智能拓扑" description="重新组织网格，降低面数" checked={topology} onChange={setTopology} />
            {topology && <div className="inline-selects"><select aria-label="拓扑面数档位" value={faceLevel} onChange={(event) => setFaceLevel(event.target.value)}><option value="low">低面数</option><option value="medium">中面数</option><option value="high">高面数</option></select><select aria-label="拓扑面类型" value={polygon} onChange={(event) => setPolygon(event.target.value)}><option value="triangle">三角面</option><option value="quadrilateral">四边面混合</option></select></div>}
            <Toggle title="PBR 纹理" description="生成 2K 物理材质贴图" checked={texture} onChange={setTexture} />
            <Toggle title="自动绑骨" description={poseMode === 'custom' ? '动态自定义姿态不支持绑骨' : '适用于规整 A / T 姿态角色'} checked={rig} onChange={setRig} disabled={poseMode === 'custom'} />
            {rig && <label className="confirmation"><input type="checkbox" checked={neutral} onChange={(event) => setNeutral(event.target.checked)} />我将确认角色为规整 A/T 姿态，且无额外武器或复杂配件</label>}
            <Toggle title="额外导出 FBX" description="通过云端转换保留实际模型格式" checked={fbx} onChange={setFbx} />
          </div>
          <div className="generate-footer"><button className="generate-button" disabled={submitting} onClick={() => void generate()}>{submitting ? <LoaderCircle size={17} className="spin" /> : <Sparkles size={17} />} 开始生成<ArrowRight size={16} /></button>
            {showGenerateIssues && generateIssues.length > 0 && <div className="generate-issues" role="alert"><strong>还需要完成：</strong><ul>{generateIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
              {generateIssues.some((issue) => issue.includes('设置页')) && <button type="button" className="text-button" onClick={() => setTab('settings')}>前往设置 <ArrowRight size={13} /></button>}</div>}
            <small>{!front ? '上传角色图片，开启三维创作' : !caps?.geometry ? '图片可本地预处理，请在设置页填写 API 信息' : poseMode !== 'original' && !caps.pose ? '姿势编辑服务待配置' : '所选云端生成与处理步骤可能产生费用'}</small></div>
        </section>
        <section className="canvas-panel"><div className="canvas-heading"><div className="canvas-identity"><div className="canvas-title"><span className="live-dot" /><strong>{localModel?.name || job?.name || '三维预览'}</strong><span className="muted">/ {localModel ? '本地导入' : '工作场景'}</span></div>
          {job && !localModel && <dl className="asset-details" aria-label="资产生成参数">
            <div><dt>目标面数</dt><dd>{job.request.face_count?.toLocaleString('zh-CN') || '未记录'}</dd></div>
            <div><dt>姿势</dt><dd>{modes.find((mode) => mode.key === job.request.pose_mode)?.label || job.request.pose_mode}</dd></div>
            <div><dt>智能拓扑</dt><dd>{job.request.topology ? '开启' : '关闭'}</dd></div>
            <div><dt>PBR 纹理</dt><dd>{job.request.texture ? '开启' : '关闭'}</dd></div>
            <div><dt>自动绑骨</dt><dd>{job.request.rig ? '开启' : '关闭'}</dd></div>
            <div><dt>FBX 导出</dt><dd>{job.request.export_fbx ? '开启' : '关闭'}</dd></div>
            <div><dt>生成模型</dt><dd>混元生3D Pro · {job.models?.geometry || '版本未记录'}</dd></div>
            {job.request.pose_mode !== 'original' && <div><dt>姿势模型</dt><dd>{job.models?.pose || '版本未记录'}</dd></div>}
          </dl>}</div>
          <button className="text-button" onClick={() => importInput.current?.click()}><Upload size={14} /> 导入 GLB</button>
          <input ref={importInput} type="file" accept=".glb" hidden aria-label="导入 GLB 模型" onChange={(event) => {
            const file = event.target.files?.[0]; if (!file) return;
            if (!file.name.toLowerCase().endsWith('.glb') || file.size > 150 * 1024 * 1024) { setError('请导入不超过 150 MiB 的 GLB 文件'); return; }
            setLocalModel({ url: URL.createObjectURL(file), name: file.name }); event.target.value = '';
          }} /></div>
          <Viewer url={modelUrl} label={localModel?.name || (job ? job.name : '未命名场景')} />
          <div className="pipeline-strip"><span>处理流程</span><div>{(job ? [
            ...(job.request.pose_mode !== 'original' ? ['pose'] : []), 'geometry',
            ...(job.request.topology ? ['topology'] : []), ...(job.request.texture ? ['texture'] : []),
            ...(job.request.rig ? ['rig'] : []), ...(job.request.export_fbx ? ['export'] : []),
          ] : [...(poseMode !== 'original' ? ['pose'] : []), ...planned]).map((stage, index) => {
            const step = job?.steps.find((s) => s.name === stage);
            return <span key={stage} className={`pipeline-stage ${step?.status || ''}`}>{index > 0 && <ChevronRight size={12} />}{step?.status === 'done' ? <Check size={12} /> : <i />}{stageLabels[stage]}</span>;
          })}</div></div>
          {job?.state === 'awaiting_review' && <div className="review-card"><img src={fileUrl(job.pose_asset!)} alt="生成的姿势参考图" /><div><h3>确认这个姿势，再生成三维</h3><p>检查角色外观、四肢方向和完整性。确认后将调用 3D 生成服务。</p><div className="review-actions"><button className="button" disabled={submitting} onClick={() => void review(true)}><Check size={15} /> 确认并生成 3D</button><button className="text-button" disabled={submitting} onClick={() => void review(false)}>放弃此姿势</button></div></div></div>}
          {job?.error && <div className="job-error" role="alert">
            {job.state === 'failed' && job.artifacts.length > 0 && <p>已完成{completedStages(job)}，{stageLabels[job.steps.find((step) => step.status === 'failed')?.name || ''] || '后续步骤'}未完成。已有产物仍可预览、下载。</p>}
            <p>{explainJobError(job.error, job.steps.find((step) => step.status === 'failed')?.name)}</p>
          </div>}
          <div className="assets-section"><div className="section-heading"><h2><FileBox size={16} /> 生成产物</h2><span className={job ? `state ${jobState(job).className}` : 'muted'}>{job ? jobState(job).label : '尚未生成'}</span></div>
            {!job?.artifacts.length ? <div className="assets-empty"><Box size={21} strokeWidth={1.2} /><p>模型完成后，可在这里预览与下载各阶段产物。</p><span>GLB / OBJ / FBX · 以实际返回格式为准</span></div> : <div className="artifact-list">{job.artifacts.map((item) => <div className="artifact" key={item.asset_id}><span className="format-tag">{item.format}</span><span>{stageLabels[item.stage]}</span>{item.format === 'GLB' && <button className="text-button" onClick={() => { setLocalModel(null); setArtifact(item.asset_id); }}>预览</button>}<a href={`${fileUrl(item.asset_id)}?download=true`} download aria-label={`下载${stageLabels[item.stage]}${item.format}`}><ArrowDownToLine size={16} /></a></div>)}</div>}
          </div>
          {job?.state === 'succeeded' && job.artifacts.some((item) => item.format === 'GLB') &&
            <FaceRefinePanel jobId={job.id} configured={Boolean(caps?.faceverse)} onSettings={() => setTab('settings')} />}
        </section>
        <aside className="inspector"><div className="panel-heading"><h2>工作空间</h2><span>02</span></div>
          <div className="connection-card"><div className="card-icon"><Unplug size={20} strokeWidth={1.5} /></div><h3>{caps?.geometry ? '服务已配置' : '先创作，稍后连接'}</h3><p>使用国内模型服务，将图片转为可用的三维资产。</p><div className="service-line"><span>混元 · 3D 生成</span><b className={caps?.geometry ? 'ready' : ''}>{caps?.geometry ? '已配置' : '待配置'}</b></div><div className="service-line"><span>千问 · 姿势编辑</span><b className={caps?.pose ? 'ready' : ''}>{caps?.pose ? '已配置' : '待配置'}</b></div><div className="service-line"><span>本地 · 去背景</span><b className={caps?.segmentation ? 'ready' : ''}>{caps?.segmentation ? '已就绪' : '待安装'}</b></div><button className="text-button" onClick={() => setTab('settings')}>打开服务设置 <ArrowRight size={13} /></button></div>
          <div className="recent-heading"><h3>最近任务</h3><button className="text-button" onClick={() => setTab('history')}>全部 <ChevronRight size={12} /></button></div>
          {jobs.slice(0, 6).map((item) => <button key={item.id} className={`recent-job ${selected === item.id ? 'active' : ''}`} onClick={() => chooseJob(item)}><img src={fileUrl(item.request.front)} alt="" /><span><strong>{item.name}</strong><small className={`state ${jobState(item).className}`}>{jobState(item).label}</small></span><ChevronRight size={12} /></button>)}
          {!jobs.length && <div className="recent-empty"><Clock3 size={22} strokeWidth={1.3} /><span>还没有生成记录</span><small>每一步进度都会保存在这里</small></div>}
          {job && <div className="trace"><h3>任务信息</h3><code>{job.id}</code>{job.steps.filter((s) => s.provider_job_id).map((s) => <p key={s.name}>{stageLabels[s.name]}<code>{s.provider_job_id}</code></p>)}</div>}
          <div className="inspector-note"><span>创作提示</span><p>完整、清晰的角色轮廓，以及无遮挡的手脚，会让三维生成更稳定。</p></div>
        </aside>
      </main>}
      <footer className="statusbar"><span /><span>ITP STUDIO <i>v0.1</i></span></footer>
    </div>
    <dialog ref={imageDialog} className="image-preview-dialog" aria-label={imagePreview ? `预览${imagePreview.label}` : '图片预览'}
      onCancel={() => setImagePreview(null)} onClick={(event) => { if (event.target === imageDialog.current) setImagePreview(null); }}>
      {imagePreview && <><div className="image-preview-heading"><strong>{imagePreview.label}</strong><button aria-label="关闭图片预览" onClick={() => setImagePreview(null)}><X size={20} /></button></div>
        <img src={imagePreview.asset.url} alt={imagePreview.label} /></>}
    </dialog>
  </div>;
}
