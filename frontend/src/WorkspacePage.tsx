import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, Box, Check, ChevronRight, Clock3, FileBox, ImagePlus,
  LoaderCircle, Plus, SlidersHorizontal, Sparkles, Upload, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { post, type Asset, type Job, type PoseMode } from './api';
import { BodyMetricsPanel } from './BodyMetricsPanel';
import { useCustomer } from './customerState';
import { explainJobError } from './errors';
import { FaceRefinePanel } from './FaceRefinePanel';
import { completedStages, jobState, stageLabels } from './jobView';
import { importLocalImage, importLocalModel, localFileUrl } from './localData';
import { yuanText } from './money';
import { saveJob, uploadEach } from './transient';
import { Viewer } from './Viewer';

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
    setLoading(true); onBusy(1);
    try {
      // The picture stays in this browser; only a task submission uploads it.
      onChange(await importLocalImage(file, 'image', background));
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

/** The modelling workbench: settings, canvas and task details for one asset. */
export default function WorkspacePage() {
  const {
    caps, jobs, setJobs, selected, account, modelPrice, setError,
    name, setName, front, setFront, reference, setReference, views, setViews,
    viewsConsistent, setViewsConsistent, poseMode, changePose,
    background, setBackground, topology, setTopology, texture, setTexture,
    rig, setRig, neutral, setNeutral, fbx, setFbx,
    faceCount, setFaceCount, faceLevel, setFaceLevel, polygon, setPolygon,
    uploadCount, setUploadCount, localModel, setLocalModel, artifact, setArtifact,
    ready, addJob, chooseJob, previewImage,
  } = useCustomer();
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const [showGenerateIssues, setShowGenerateIssues] = useState(false);
  const [imagePreview, setImagePreview] = useState<{ asset: Asset; label: string } | null>(null);
  const importInput = useRef<HTMLInputElement>(null);
  const imageDialog = useRef<HTMLDialogElement>(null);
  const job = jobs.find((item) => item.id === selected);

  useEffect(() => {
    if (imagePreview && !imageDialog.current?.open) imageDialog.current?.showModal();
    if (!imagePreview && imageDialog.current?.open) imageDialog.current.close();
  }, [imagePreview]);

  const generateIssues = [
    ...(!account.user ? ['请先登录账号，再使用云端建模'] : []),
    ...(!front ? ['请上传角色图片'] : []),
    ...(uploadCount ? ['请等待图片上传完成'] : []),
    ...(!caps ? ['正在连接服务，请稍候'] : !caps.geometry ? ['人体建模服务暂不可用，请稍后再试'] : []),
    ...(poseMode !== 'original' && caps && !caps.pose ? ['姿势编辑服务暂不可用，可选择原始姿势'] : []),
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
      // Upload the local pictures for this run only; the server deletes them
      // again as soon as this browser has saved the results.
      const filled = Object.entries(views).filter(([, value]) => value) as [string, Asset][];
      const [uploadedFront, ...rest] = await uploadEach([
        front.id, ...filled.map(([, value]) => value.id),
        ...(poseMode === 'custom' && reference ? [reference.id] : []),
      ]);
      const uploadedViews = Object.fromEntries(filled.map(([key], index) => [key, rest[index].id]));
      const uploadedReference = poseMode === 'custom' && reference ? rest[filled.length]?.id : null;
      const created = await post<Job>('/api/jobs', {
        name: name.trim() || '未命名资产', front: uploadedFront.id,
        views: uploadedViews,
        views_consistent_confirmed: viewsConsistent,
        pose_mode: poseMode, pose_reference: uploadedReference ?? null,
        topology, polygon_type: polygon, face_level: faceLevel, face_count: faceCount,
        texture, rig, neutral_pose_confirmed: neutral, export_fbx: fbx,
      });
      const mirror = await saveJob(created);
      addJob(mirror);
      chooseJob(mirror);
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }
  async function review(approve: boolean) {
    if (!job) return;
    setSubmitting(true);
    try {
      const mirror = await saveJob(await post<Job>(`/api/jobs/${job.id}/review`, { approve }));
      setJobs((list) => list.map((item) => item.id === mirror.id ? mirror : item));
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }
  const generatedGlb = job?.artifacts.filter((item) => item.format === 'GLB').at(-1)?.id;
  const modelUrl = localModel?.url ?? localFileUrl(artifact ?? generatedGlb ?? '') ?? null;
  const planned = ['geometry', ...(topology ? ['topology'] : []), ...(texture ? ['texture'] : []),
    ...(rig ? ['rig'] : []), ...(fbx ? ['export'] : [])];

  return <>
    <main className="studio-grid">
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
          <BodyMetricsPanel ready={ready} />
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
          {modelPrice !== null && <small>¥{yuanText(modelPrice)} / 次，从钱包扣除；任务失败自动退款。</small>}
          {showGenerateIssues && generateIssues.length > 0 && <div className="generate-issues" role="alert"><strong>还需要完成：</strong><ul>{generateIssues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
            </div>}
          <small>{!front ? '上传角色图片，开启三维创作' : !caps?.geometry ? '人体建模暂不可用，仍可上传图片或导入 GLB' : poseMode !== 'original' && !caps.pose ? '姿势编辑暂不可用，可选择原始姿势' : '所选云端生成与处理步骤可能产生费用'}</small></div>
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
          const file = event.target.files?.[0]; event.target.value = '';
          if (!file) return;
          // A picked model is the customer's own asset and is kept locally,
          // like the pictures: the server only sees it during a run.
          void importLocalModel(file)
            .then((asset) => setLocalModel({ url: asset.url, name: asset.name || '本地模型' }))
            .catch((err) => setError((err as Error).message));
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
        {job?.state === 'awaiting_review' && <div className="review-card"><img src={previewImage(job.pose_asset)} alt="生成的姿势参考图" /><div><h3>确认这个姿势，再生成三维</h3><p>检查角色外观、四肢方向和完整性。确认后将调用 3D 生成服务。</p><div className="review-actions"><button className="button" disabled={submitting} onClick={() => void review(true)}><Check size={15} /> 确认并生成 3D</button><button className="text-button" disabled={submitting} onClick={() => void review(false)}>放弃此姿势</button></div></div></div>}
        {job?.error && <div className="job-error" role="alert">
          {job.state === 'failed' && job.artifacts.length > 0 && <p>已完成{completedStages(job)}，{stageLabels[job.steps.find((step) => step.status === 'failed')?.name || ''] || '后续步骤'}未完成。已有产物仍可预览、下载。</p>}
          <p>{explainJobError(job.error, job.steps.find((step) => step.status === 'failed')?.name)}</p>
        </div>}
        <div className="assets-section"><div className="section-heading"><h2><FileBox size={16} /> 生成产物</h2><span className={job ? `state ${jobState(job).className}` : 'muted'}>{job ? jobState(job).label : '尚未生成'}</span></div>
          {!job?.artifacts.length ? <div className="assets-empty"><Box size={21} strokeWidth={1.2} /><p>模型完成后，可在这里预览与下载各阶段产物。</p><span>GLB / OBJ / FBX · 以实际返回格式为准</span></div> : <div className="artifact-list">{job.artifacts.map((item) => <div className="artifact" key={item.id}><span className="format-tag">{item.format}</span><span>{stageLabels[item.stage]}</span>{item.format === 'GLB' && <button className="text-button" onClick={() => { setLocalModel(null); setArtifact(item.id); }}>预览</button>}<a href={localFileUrl(item.id)} download={`${job.name}-${item.stage}.${item.format.toLowerCase()}`} aria-label={`下载${stageLabels[item.stage]}${item.format}`}><ArrowDownToLine size={16} /></a></div>)}</div>}
        </div>
        {job?.state === 'succeeded' && job.artifacts.some((item) => item.format === 'GLB') &&
          <FaceRefinePanel model={generatedGlb!} name={job.name} configured={Boolean(caps?.faceverse)} />}
      </section>
      <aside className="inspector">
        <div className="recent-heading"><h3>最近任务</h3><button className="text-button" onClick={() => navigate('/history')}>全部 <ChevronRight size={12} /></button></div>
        {jobs.slice(0, 6).map((item) => <button key={item.id} className={`recent-job ${selected === item.id ? 'active' : ''}`} onClick={() => chooseJob(item)}><img src={previewImage(item.request.front)} alt="" /><span><strong>{item.name}</strong><small className={`state ${jobState(item).className}`}>{jobState(item).label}</small></span><ChevronRight size={12} /></button>)}
        {!jobs.length && <div className="recent-empty"><Clock3 size={22} strokeWidth={1.3} /><span>还没有生成记录</span><small>每一步进度都会保存在这里</small></div>}
        {job && <div className="trace"><h3>任务信息</h3><code>{job.id}</code>{job.steps.filter((s) => s.provider_job_id).map((s) => <p key={s.name}>{stageLabels[s.name]}<code>{s.provider_job_id}</code></p>)}</div>}
        <div className="inspector-note"><span>创作提示</span><p>完整、清晰的角色轮廓，以及无遮挡的手脚，会让三维生成更稳定。</p></div>
      </aside>
    </main>
    <dialog ref={imageDialog} className="image-preview-dialog" aria-label={imagePreview ? `预览${imagePreview.label}` : '图片预览'}
      onCancel={() => setImagePreview(null)} onClick={(event) => { if (event.target === imageDialog.current) setImagePreview(null); }}>
      {imagePreview && <><div className="image-preview-heading"><strong>{imagePreview.label}</strong><button aria-label="关闭图片预览" onClick={() => setImagePreview(null)}><X size={20} /></button></div>
        <img src={imagePreview.asset.url} alt={imagePreview.label} /></>}
    </dialog>
  </>;
}
