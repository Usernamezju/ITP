import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, ChevronDown, ChevronRight, Clock3, Download, ImagePlus, LoaderCircle, Shirt, Sparkles, X, ZoomIn } from 'lucide-react';
import { api, post, type Asset, type Capabilities, type Job, type TryOnJob, type TryOnProvider } from './api';
import { importLocalImage, localAsset, localFileUrl, localRecords } from './localData';
import { sessionToken } from './session';
import { TERMINAL, acknowledge, saveJob, saveTryOn, uploadEach, type LocalJob, type LocalTryOn } from './transient';
import './TryOnPage.css';
import './TryOnWorkspace.css';

const views = [
  ['front', '正面'], ['back', '背面'], ['left', '左侧'], ['right', '右侧'],
  ['left_front', '左前 45°'], ['right_front', '右前 45°'],
] as const;
type Provider = TryOnProvider;
const models: { id: Provider; label: string }[] = [
  { id: 'seedream', label: 'SeedDream 5.0' }, { id: 'flux', label: 'FLUX.2 Pro' },
  { id: 'flux_max', label: 'FLUX.2 Max' },
  { id: 'flux_klein', label: 'FLUX.2 Klein 4B' },
  { id: 'flux_klein_9b', label: 'FLUX.2 Klein 9B' },
  { id: 'gpt_image', label: 'GPT Image 2' },
];

function ImageInput({ label, index, asset, onChange, onBusy, onPreview }: {
  label: string; index: number; asset?: Asset; onChange: (value?: Asset) => void; onBusy: (busy: boolean) => void;
  onPreview: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function upload(file?: File) {
    if (!file) return;
    setBusy(true); onBusy(true); setError('');
    try {
      // Kept in this browser; only starting a try-on uploads a temporary copy.
      onChange(await importLocalImage(file, 'image'));
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusy(false); }
  }
  return <div className={`tryon-input ${asset ? 'filled' : ''}`}><label>{asset && <img src={asset.url} alt={label} />}
    {!asset && <span className="tryon-input-empty">{busy ? <LoaderCircle className="spin" size={19} /> : <ImagePlus size={19} strokeWidth={1.5} />}<b>{label}</b><small>点击上传</small></span>}
    <input type="file" accept="image/png,image/jpeg,image/webp" aria-label={`上传${label}`}
      disabled={busy} onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
  </label><span className="tryon-input-index">{String(index + 1).padStart(2, '0')}</span>
    {asset && <button className="tryon-input-zoom" type="button" aria-label={`放大查看${label}`} onClick={onPreview}><ZoomIn size={13} /></button>}
    {asset && <button className="tryon-input-remove" type="button" aria-label={`移除${label}`} onClick={() => onChange()}><X size={12} /></button>}
    {error && <small className="tryon-input-error" role="alert">{error}</small>}</div>;
}

/** The pictures chosen for one try-on, rebuilt from this browser's own store. */
function restoreSelection(key: string): Record<string, Asset> {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || '{}') as Record<string, string>;
    return Object.fromEntries(Object.entries(saved)
      .map(([view, id]) => [view, localAsset(id)] as const)
      .filter((entry): entry is [string, Asset] => Boolean(entry[1])));
  } catch { return {}; }
}

export function TryOnPage({ caps, ready, onContinue }: {
  caps: Capabilities | null; ready: boolean; onContinue: (job: LocalJob) => void;
}) {
  const [person, setPerson] = useState<Record<string, Asset>>({});
  const [garment, setGarment] = useState<Record<string, Asset>>({});
  const [name, setName] = useState('');
  const [busyCount, setBusyCount] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [current, setCurrent] = useState<LocalTryOn | null>(null);
  const [error, setError] = useState('');
  const [selectedView, setSelectedView] = useState<(typeof views)[number][0]>('front');
  const [provider, setProvider] = useState<Provider>('seedream');
  const [kleinHealth, setKleinHealth] = useState<{ provider: Provider; ready: boolean } | null>(null);
  const [preview, setPreview] = useState<{ url: string; label: string } | null>(null);
  const previewDialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (preview && !previewDialog.current?.open) previewDialog.current?.showModal();
    if (!preview && previewDialog.current?.open) previewDialog.current.close();
  }, [preview]);

  useEffect(() => {
    if (!ready) return;
    setPerson(restoreSelection('itp-tryon-person'));
    setGarment(restoreSelection('itp-tryon-garment'));
  }, [ready]);
  useEffect(() => {
    // Only this browser's own ids are written, and only once the local store
    // has been read back — otherwise the first render would erase the choice.
    if (!ready) return;
    sessionStorage.setItem('itp-tryon-person',
      JSON.stringify(Object.fromEntries(Object.entries(person).map(([view, asset]) => [view, asset.id]))));
  }, [person, ready]);
  useEffect(() => {
    if (!ready) return;
    sessionStorage.setItem('itp-tryon-garment',
      JSON.stringify(Object.fromEntries(Object.entries(garment).map(([view, asset]) => [view, asset.id]))));
  }, [garment, ready]);

  useEffect(() => {
    if (!ready) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const saved = await localRecords<LocalTryOn>('tryon:');
        const merged = new Map(saved.map((item) => [item.id, item]));
        if (sessionToken.read()) {
          // Save the generated views here, then let the server delete its own.
          for (const live of await api<TryOnJob[]>('/api/tryons')) {
            const mirror = await saveTryOn(live);
            merged.set(mirror.id, mirror);
            if (TERMINAL.has(live.state)) await acknowledge('tryons', live.id);
          }
        }
        if (stopped) return;
        const list = [...merged.values()].reverse();
        setCurrent((existing) => {
          const newest = list.at(-1) ?? null;
          return existing ? (merged.get(existing.id) ?? existing) : newest;
        });
      } catch (err) { if (!stopped) setError((err as Error).message); }
      finally { if (!stopped) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, [ready]);

  useEffect(() => {
    if (!['flux_klein', 'flux_klein_9b'].includes(provider) || !caps?.tryon_providers?.[provider]) {
      setKleinHealth(null);
      return;
    }
    let active = true;
    setKleinHealth(null);
    const healthPath = provider === 'flux_klein_9b' ? 'flux-klein-9b' : 'flux-klein';
    function refresh() {
      void api<{ ready: boolean }>(`/api/tryon-providers/${healthPath}/health`)
        .then((result) => { if (active) setKleinHealth({ provider, ready: result.ready }); })
        .catch(() => { if (active) setKleinHealth({ provider, ready: false }); });
    }
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => { active = false; clearInterval(timer); };
  }, [provider, caps?.tryon_providers?.flux_klein, caps?.tryon_providers?.flux_klein_9b]);

  const personCount = views.filter(([view]) => person[view]).length;
  const garmentCount = views.filter(([view]) => garment[view]).length;
  const complete = personCount > 0 && garmentCount > 0;
  const isKlein = provider === 'flux_klein' || provider === 'flux_klein_9b';
  const kleinReady = kleinHealth?.provider === provider && kleinHealth.ready;
  const selectedReady = Boolean(caps?.tryon_providers?.[provider]) && (!isKlein || kleinReady);
  const selectedConfigured = Boolean(caps?.tryon_providers?.[provider]);
  const providerStatus = !selectedReady ? '暂不可用' : '可用';
  const resultCount = current ? Object.keys(current.results).length : 0;
  const selectedLabel = views.find(([view]) => view === selectedView)?.[1] || '正面';
  const activeResult = current?.results[selectedView];
  const running = Boolean(current && ['queued', 'running', 'submitting'].includes(current.state));
  async function generate() {
    if (!complete || busyCount || submitting) return;
    setSubmitting(true); setError('');
    try {
      // Only a temporary copy of the chosen pictures goes to the server; this
      // browser is where they live.
      const chosen = [
        ...views.filter(([view]) => person[view]).map(([view]) => person[view].id),
        ...views.filter(([view]) => garment[view]).map(([view]) => garment[view].id),
      ];
      const uploaded = await uploadEach(chosen);
      const personCount = views.filter(([view]) => person[view]).length;
      const job = await post<TryOnJob>('/api/tryons', {
        name: name.trim() || '虚拟试穿', provider,
        person: Object.fromEntries(views.filter(([view]) => person[view])
          .map(([view], index) => [view, uploaded[index].id])),
        garment: Object.fromEntries(views.filter(([view]) => garment[view])
          .map(([view], index) => [view, uploaded[personCount + index].id])),
      });
      setCurrent(await saveTryOn(job));
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }
  async function continue3D() {
    if (!current || !Object.keys(current.results).length) return;
    setSubmitting(true); setError('');
    try {
      // The generated views are already here; modelling re-uploads them for
      // one run instead of the server remembering where they came from.
      const entries = Object.entries(current.results);
      const uploaded = await uploadEach(entries.map(([, id]) => id));
      const ids = Object.fromEntries(entries.map(([view], index) => [view, uploaded[index].id]));
      const front = views.find(([view]) => view === 'front' && ids[view])?.[0] ?? entries[0][0];
      const created = await post<Job>('/api/jobs', {
        name: current.name || '试穿建模',
        front: ids[front],
        views: Object.fromEntries(entries.filter(([view]) => view !== front)
          .map(([view]) => [view, ids[view]])),
        views_consistent_confirmed: true,
      });
      onContinue(await saveJob(created));
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }

  return <main className="tryon-page tryon-workspace">
    <section className="tryon-control-panel" aria-label="试穿素材与设置">
      <div className="panel-heading"><h2><Shirt size={16} /> 试穿设置</h2><span>01</span></div>
      <div className="tryon-control-scroll">
        <label className="field-label" htmlFor="tryon-name">任务名称</label>
        <input id="tryon-name" className="text-input" value={name} maxLength={80}
          placeholder="例如：夏季外套试穿" onChange={(event) => setName(event.target.value)} />
        <div className="tryon-mobile-provider"><label className="field-label" htmlFor="tryon-provider">生图模型</label>
          <div className="tryon-select-wrap"><select id="tryon-provider" className="text-input tryon-provider-select" value={provider}
            onChange={(event) => setProvider(event.target.value as Provider)}>
            {models.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select><ChevronDown size={15} /></div></div>
        <p className="tryon-control-hint">人物和服装各上传 1–6 张；参考角度越充分，生成视角越稳定。</p>
        <div className="tryon-groups">{([['人物参考', person, setPerson, personCount], ['服装参考', garment, setGarment, garmentCount]] as const).map(([title, items, setter, count], groupIndex) =>
          <section key={title} className="tryon-upload-group"><div className="tryon-group-heading"><div><span className="tryon-step">0{groupIndex + 1}</span><h3>{title}</h3></div><small>{count} / 6 已上传</small></div>
            <div className="tryon-input-grid">{views.map(([view, label], index) =>
              <ImageInput key={view} label={label} index={index} asset={items[view]}
                onChange={(asset) => setter((old) => { const next = { ...old }; if (asset) next[view] = asset; else delete next[view]; return next; })}
                onBusy={(value) => setBusyCount((n) => n + (value ? 1 : -1))}
                onPreview={() => setPreview({ url: items[view].url, label: `${title} · ${label}` })} />)}</div></section>)}</div>
      </div>
      <div className="tryon-control-footer"><button className="generate-button tryon-generate" disabled={!complete || busyCount > 0 || submitting || !selectedReady}
        onClick={() => void generate()}>{submitting ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}生成六视图试穿<ArrowRight size={16} /></button>
        <small>{!selectedConfigured || !selectedReady ? '当前生图服务暂不可用，请选择其他可用模型或稍后再试' : !complete ? '人物与服装各上传至少一张图片后可开始' : isKlein ? '模型将分六次生成' : '六次云端生成可能产生费用'}</small></div>
    </section>
    <section className="tryon-stage" aria-label="试穿预览与结果">
      <div className="tryon-stage-heading"><div><span className="live-dot" /><strong>{current?.name || name || '试穿预览'}</strong><span className="muted">/ 多视角工作场景</span></div>
        <span className={`tryon-state ${current?.state || 'empty'}`}>{current ? current.state === 'ready' ? '已完成' : current.state === 'failed' ? '生成失败' : running ? '正在生成' : '待处理' : '尚未生成'}</span></div>
      <div className={`tryon-viewport ${activeResult ? 'has-result' : ''}`}>
        {activeResult ? <button className="tryon-stage-preview" type="button" aria-label={`放大查看${selectedLabel}结果`} onClick={() => setPreview({ url: localFileUrl(activeResult)!, label: `换装结果 · ${selectedLabel}` })}><img src={localFileUrl(activeResult)} alt={`换装后${selectedLabel}`} /><ZoomIn size={17} /></button> : <div className="tryon-viewport-empty"><span className="tryon-viewport-mark"><Shirt size={36} strokeWidth={1.1} /></span><h2>让想象，穿在身上</h2><p>上传人物和服装图片，预览结果将在这里呈现</p></div>}
        <div className="tryon-viewport-bottom"><span>ClothiNation · 六视图换装</span>{activeResult && <a href={localFileUrl(activeResult)} download={`${current?.name || '试穿'}-${selectedLabel}.png`} aria-label={`保存${selectedLabel}结果`}><Download size={14} /> 保存当前视角</a>}</div>
      </div>
      <div className="tryon-pipeline"><span>生成流程</span><div><b className={complete ? 'done' : ''}>上传素材</b><ChevronRight size={13} /><b className={running || resultCount ? 'done' : ''}>六视图换装</b><ChevronRight size={13} /><b className={current?.state === 'ready' ? 'done' : ''}>保存 / 继续 3D</b></div></div>
      <section className="tryon-gallery"><div className="tryon-gallery-heading"><h3>换装结果 <span>{resultCount} / 6</span></h3><small>点击缩略图切换主预览</small></div>
        <div className="tryon-result-grid">{views.map(([view, label], index) => <div className={`tryon-result ${selectedView === view ? 'selected' : ''}`} key={view}>
          <button type="button" aria-label={`预览${label}结果`} onClick={() => setSelectedView(view)}><span className="tryon-result-image">{current?.results[view] ? <img src={localFileUrl(current.results[view])} alt={`换装后${label}`} /> : <ImagePlus size={21} strokeWidth={1.2} />}</span><span className="tryon-result-caption"><b>{String(index + 1).padStart(2, '0')}</b> {label}{current?.results[view] && <Check size={13} />}</span></button>
          {current?.results[view] && <a href={localFileUrl(current.results[view])} download={`${current?.name || '试穿'}-${label}.png`} aria-label={`保存${label}`} title={`保存${label}`}><Download size={14} /></a>}
        </div>)}</div></section>
      {current?.state === 'ready' && <div className="tryon-next"><div><strong>六视图已生成</strong><p>检查身份、脸部、体型、服装和视角一致性。可逐张保存图片，或将结果直接送入 3D 建模。</p></div><button className="button" onClick={() => void continue3D()} disabled={submitting || !caps?.geometry}>继续生成 3D 模型 <ArrowRight size={16} /></button>
        {!caps?.geometry && <small>人体建模暂不可用，可先保存试穿图片。</small>}</div>}
      {current?.state === 'failed' && <p className="tryon-error" role="alert">{current.error}</p>}
      {error && <p className="tryon-error" role="alert">{error}</p>}
    </section>
    <aside className="tryon-inspector" aria-label="试穿工作空间"><div className="panel-heading"><h2>工作空间</h2><span>02</span></div>
      <div className="tryon-service-card"><div className="tryon-service-icon"><Sparkles size={20} strokeWidth={1.5} /></div>
        <div className="tryon-service-picker"><label htmlFor="tryon-provider-service">生图模型</label>
          <div className="tryon-select-wrap"><select id="tryon-provider-service" value={provider}
            onChange={(event) => setProvider(event.target.value as Provider)}>
            {models.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select><ChevronDown size={15} aria-hidden="true" /></div></div>
        <div className="service-line"><span>当前生图服务</span><b className={selectedReady ? 'ready' : ''}>{providerStatus}</b></div>
        <div className="service-line"><span>混元 · 图生 3D</span><b className={caps?.geometry ? 'ready' : ''}>{caps?.geometry ? '可用' : '暂不可用'}</b></div></div>
      <div className="tryon-inspector-heading"><h3>当前任务</h3><span>{current ? `${resultCount}/6` : '未开始'}</span></div>
      {current ? <div className="tryon-progress-list">{views.map(([view, label]) => <button key={view} type="button" className={selectedView === view ? 'active' : ''} onClick={() => setSelectedView(view)}><span className={current.results[view] ? 'complete' : current.active_view === view && running ? 'working' : ''}>{current.results[view] ? <Check size={13} /> : current.active_view === view && running ? <LoaderCircle className="spin" size={13} /> : <Clock3 size={13} />}</span><b>{label}</b><small>{current.results[view] ? '已完成' : current.active_view === view && running ? '生成中' : '待生成'}</small></button>)}</div> : <div className="tryon-inspector-empty"><Clock3 size={22} strokeWidth={1.3} /><span>还没有生成记录</span><small>完成上传后在左侧开始生成</small></div>}
    </aside>
    <dialog ref={previewDialog} className="image-preview-dialog" aria-label={preview ? `预览${preview.label}` : '图片预览'}
      onCancel={() => setPreview(null)} onClick={(event) => { if (event.target === previewDialog.current) setPreview(null); }}>
      {preview && <><div className="image-preview-heading"><strong>{preview.label}</strong><button type="button" aria-label="关闭图片预览" onClick={() => setPreview(null)}><X size={20} /></button></div>
        <img src={preview.url} alt={preview.label} /></>}
    </dialog>
  </main>;
}
