import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Box, Check, ChevronDown, Clock3, Layers3, LoaderCircle, RefreshCw,
  RotateCcw, Ruler, Settings2, Sparkles, Unplug, X } from 'lucide-react';
import { api, type BodyAnalysis, type BodyField, type BodyValue, type Capabilities, type Job,
  type Outfit, type OutfitFilterOption, type OutfitImages, type OutfitResponse } from './api';
import { LookBoard } from './LookBoard';
import './OutfitsPage.css';

const imageProviderLabels: Record<string, string> = {
  so: '360 图片 · 免 key', unsplash: 'Unsplash', pixabay: 'Pixabay',
};

function imageProviderLabel(provider?: string): string {
  return (provider && imageProviderLabels[provider]) || '';
}

const bodyFieldLabels: { key: BodyField; label: string; unit: string }[] = [
  { key: 'height_cm', label: '身高', unit: 'cm' },
  { key: 'weight_kg', label: '体重', unit: 'kg' },
  { key: 'shoulder_cm', label: '肩宽', unit: 'cm' },
  { key: 'bust_cm', label: '胸围', unit: 'cm' },
  { key: 'waist_cm', label: '腰围', unit: 'cm' },
  { key: 'hip_cm', label: '臀围', unit: 'cm' },
];

const fitStateLabels: Record<string, string> = {
  fit: '合身', tight: '偏紧', loose: '偏松', unknown: '未标注',
};

/** What the person measured (or the model estimated), shown above the analysis. */
function BodySummary({ body }: { body?: Partial<Record<BodyField, BodyValue>> }) {
  if (!body) return null;
  const shown = bodyFieldLabels.filter((field) => body[field.key]?.value != null);
  if (!shown.length) return null;
  return <div className="outfits-body">
    <span className="outfits-body-heading"><Ruler size={12} /> 人体数据</span>
    {shown.map((field) => {
      const value = body[field.key] as BodyValue;
      return <span key={field.key} className={`outfits-body-chip ${value.source}`}>
        <b>{field.label}</b>{value.value}{field.unit}
        <i>{value.source === 'input' ? '已填' : '估算'}</i>
      </span>;
    })}
    <small>未填的项按模型比例推算，估算值仅用于尺码参考</small>
  </div>;
}

/** One selectable value of a recommendation filter, with its catalogue-wide count. */
function FilterGroup({ label, options, value, onChange }: {
  label: string; options: OutfitFilterOption[]; value: string; onChange: (next: string) => void;
}) {
  if (!options.length) return null;
  return <div className="outfits-filter">
    <div className="outfits-filter-heading"><span>{label}</span><small>{value || '全部'}</small></div>
    <div className="outfits-filter-chips">
      <button type="button" className={value === '' ? 'active' : ''} onClick={() => onChange('')}>全部</button>
      {options.map((option) => <button key={option.id} type="button"
        className={value === option.id ? 'active' : ''}
        onClick={() => onChange(value === option.id ? '' : option.id)}>
        {option.id}<b>{option.count}</b>
      </button>)}
    </div>
  </div>;
}

/** Mirrors the measured silhouette: one horizontal span per height slice, head at the top. */
function ProfileChart({ profile, labels }: { profile: number[]; labels: Record<string, string> }) {
  if (profile.length < 4) return null;
  const peak = Math.max(...profile, 0.001);
  const half = 42;
  const step = 188 / (profile.length - 1);
  const points = profile.map((value, index) => ({ x: half * (value / peak), y: 6 + index * step }));
  const left = points.map((point) => `${(48 - point.x).toFixed(1)},${point.y.toFixed(1)}`);
  const right = [...points].reverse().map((point) => `${(48 + point.x).toFixed(1)},${point.y.toFixed(1)}`);
  return <figure className="outfits-profile">
    <svg viewBox="0 0 96 200" role="img" aria-label="三维模型轮廓切片">
      <line x1="48" y1="4" x2="48" y2="196" className="outfits-profile-axis" />
      <polygon points={[...left, ...right].join(' ')} className="outfits-profile-shape" />
    </svg>
    <figcaption>
      <span>轮廓切片</span>
      <small>{Object.values(labels).slice(0, 3).join(' · ') || '已测量'}</small>
    </figcaption>
  </figure>;
}

function AnalysisPanel({ analysis, onModeling }: { analysis: BodyAnalysis; onModeling: () => void }) {
  if (!analysis.available) {
    const measured = Object.values(analysis.body || {}).some(
      (value) => value?.value != null && value.source === 'input');
    return <div className="outfits-analysis-empty">
      <span className="outfits-analysis-icon"><Box size={22} strokeWidth={1.3} /></span>
      <div><h3>尚未生成三维模型</h3><p>{measured
        ? '已按你填写的身高与三围匹配尺码；生成三维模型后会补充模型实测的肩宽、腰线与腿身比。'
        : '已按通用体型推荐。在「人体建模」页填写身高三围，或完成人体建模后可改用实测比例。'}</p></div>
      <button className="button small" type="button" onClick={onModeling}>前往人体建模 <ArrowRight size={13} /></button>
    </div>;
  }
  return <div className="outfits-analysis">
    <div className="outfits-analysis-main">
      <ProfileChart profile={analysis.profile || []} labels={analysis.labels} />
      <div className="outfits-analysis-data">
        <div className="outfits-labels">
          {Object.entries(analysis.labels).map(([key, value]) => <span key={key}>{value}</span>)}
        </div>
        <dl className="outfits-metrics">
          {analysis.metrics.map((metric) => <div key={metric.label}>
            <dt>{metric.label}</dt><dd>{metric.value}</dd><small>{metric.hint}</small>
          </div>)}
        </dl>
      </div>
    </div>
    {analysis.notes.length > 0 && <ul className="outfits-notes">
      {analysis.notes.map((note) => <li key={note}>{note}</li>)}
    </ul>}
    <p className="outfits-method">{analysis.method}</p>
  </div>;
}

/**
 * Runs image lookups strictly one at a time with a gap between them: the default
 * keyless source refuses a burst, and a refused page shows no pictures at all.
 */
const imageQueue: (() => void)[] = [];
let imageBusy = false;
const IMAGE_GAP_MS = 600;

function withImageSlot<T>(run: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const launch = () => {
      imageBusy = true;
      run()
        .then(resolve, reject)
        .finally(() => {
          setTimeout(() => {
            imageBusy = false;
            imageQueue.shift()?.();
          }, IMAGE_GAP_MS);
        });
    };
    if (imageBusy) imageQueue.push(launch);
    else launch();
  });
}

/** True once the element has come near the viewport, so offscreen cards stay quiet. */
function useInView<T extends Element>(rootMargin = '240px') {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node || seen) return;
    if (typeof IntersectionObserver === 'undefined') { setSeen(true); return; }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setSeen(true); observer.disconnect(); }
    }, { rootMargin });
    observer.observe(node);
    return () => observer.disconnect();
  }, [seen, rootMargin]);
  return { ref, seen };
}

function useOutfitImages(outfitId: string, limit: number, enabled = true) {
  const [result, setResult] = useState<OutfitImages | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    // A manual retry asks the server to bypass its own short negative cache.
    const refresh = attempt ? '&refresh=true' : '';
    void withImageSlot(() => api<OutfitImages>(`/api/outfits/${outfitId}/images?limit=${limit}${refresh}`))
      .then((data) => { if (!cancelled) setResult(data); })
      .catch(() => { if (!cancelled) setResult(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [outfitId, limit, enabled, attempt]);
  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  return { images: result, loading, reload };
}

/** One look: a merchant photo when it came from the database, else a searched one. */
function OutfitCard({ outfit, analyzed, onOpen }: {
  outfit: Outfit; analyzed: boolean; onOpen: () => void;
}) {
  const { ref, seen } = useInView<HTMLElement>();
  const product = outfit.image_urls?.length ? outfit.image_urls : [];
  // The catalogue image search only knows built-in look ids, so a merchant item
  // without uploads is shown as a colour sketch instead of a pointless request.
  const searchable = outfit.origin !== 'database';
  const { images, loading, reload } = useOutfitImages(outfit.id, 4, seen && searchable);
  const [index, setIndex] = useState(0);
  const list = product.length || !searchable ? [] : (images?.images || []);
  const current = list.length ? list[index % list.length] : undefined;
  const hero = product.length ? product[index % product.length] : current?.url;
  const switchable = product.length > 1 || list.length > 1;
  const failed = Boolean(searchable && !product.length && images && !list.length && !loading);
  return <article className="outfit-card" ref={ref}>
    <div className="outfit-card-board">
      <button type="button" className="outfit-card-open" onClick={onOpen} aria-label={`查看${outfit.name}详情`}>
        {hero
          ? <img src={hero} alt={current?.title || `${outfit.name} 参考图`} loading="lazy" />
          : <LookBoard palette={outfit.palette} style={outfit.style} season={outfit.season} label={outfit.name} />}
      </button>
      {switchable && <button type="button" className="outfit-cycle"
        aria-label={`换一张${outfit.name}的参考图`}
        onClick={() => setIndex((value) => (value + 1) % (product.length || list.length))}>
        <RefreshCw size={12} /></button>}
      {failed && <button type="button" className="outfit-retry" onClick={reload}
        aria-label={`重新获取${outfit.name}的参考图`}><RefreshCw size={11} /> 重试</button>}
      {analyzed && <b className="outfit-score">{Math.round(outfit.score * 100)}</b>}
    </div>
    <button type="button" className="outfit-card-body" onClick={onOpen}
      aria-label={`查看${outfit.name}介绍与单品`}>
      <span className="outfit-card-title"><strong>{outfit.name}</strong><ArrowRight size={13} /></span>
      <small className="outfit-card-tagline">{outfit.tagline}</small>
      <span className="outfit-card-tags">
        {outfit.origin === 'database' && <i className="outfit-card-origin">商家</i>}
        <i>{outfit.style}</i><i>{outfit.season}</i><i>{outfit.occasion}</i></span>
      <span className="outfit-palette">{outfit.palette.map((color) =>
        <i key={color} style={{ background: color }} />)}</span>
    </button>
  </article>;
}

/** The bigger, browsable picture set shown inside the detail dialog. */
function OutfitGallery({ outfit }: { outfit: Outfit }) {
  const product = outfit.image_urls?.length ? outfit.image_urls : [];
  const searchable = outfit.origin !== 'database';
  const { images, loading, reload } = useOutfitImages(outfit.id, 8, searchable);
  const [index, setIndex] = useState(0);
  const list = product.length || !searchable ? [] : (images?.images || []);
  const current = list[Math.min(index, Math.max(list.length - 1, 0))];
  const urls = product.length ? product : list.map((image) => image.url);
  const hero = product.length ? product[Math.min(index, product.length - 1)] : current?.url;

  if (!urls.length) {
    const note = !searchable
      ? '商家未上传商品图，先以配色示意。'
      : loading ? '正在检索图片…' : images?.error || '暂时取不到参考图，先以配色示意。';
    return <div className="outfit-gallery is-empty">
      <LookBoard palette={outfit.palette} style={outfit.style} season={outfit.season} label={outfit.name} />
      <div className="outfit-palette outfit-palette-large">{outfit.palette.map((color) =>
        <i key={color} style={{ background: color }} title={color} />)}</div>
      <small>{note}</small>
      {searchable && !loading && <button className="button small" type="button" onClick={reload}>
        重新检索 <RotateCcw size={13} /></button>}
    </div>;
  }
  return <div className="outfit-gallery">
    <figure className="outfit-gallery-main">
      <img src={hero} alt={current?.title || `${outfit.name} 商品图`} />
      <figcaption>
        {product.length
          ? <span>商家上传的商品图 <b>{index + 1}/{product.length}</b></span>
          : <>
            <span>{current.title || '参考图'}</span>
            <span className="outfit-gallery-links">
              {current.creator && <small>{current.creator}{current.license ? ` · ${current.license}` : ''}</small>}
              {!current.creator && current.site && <small>{current.site}</small>}
              <a href={current.source_url} target="_blank" rel="noreferrer">来源</a>
              <a href={current.original_url} target="_blank" rel="noreferrer">原图</a>
            </span>
          </>}
      </figcaption>
    </figure>
    {urls.length > 1 && <div className="outfit-gallery-strip">
      {urls.map((url, position) => <button key={url} type="button"
        className={position === index ? 'active' : ''} onClick={() => setIndex(position)}
        aria-label={`查看第 ${position + 1} 张参考图`}>
        <img src={url} alt="" loading="lazy" />
      </button>)}
    </div>}
    <p className="outfit-gallery-note">
      {product.length
        ? '图片由商家上传，版权归商家所有。'
        : `图片由${images?.provider_label || '图片检索'}检索，版权归原作者所有，已保留来源链接。`}
    </p>
  </div>;
}

export function OutfitsPage({ caps, jobs, onSettings, onModeling }: {
  caps: Capabilities | null; jobs: Job[]; onSettings: () => void; onModeling: () => void;
}) {
  const modelJobs = useMemo(
    () => jobs.filter((item) => item.artifacts.some((artifact) => artifact.format === 'GLB')),
    [jobs]);
  const [source, setSource] = useState('');
  const [style, setStyle] = useState('');
  const [season, setSeason] = useState('');
  const [occasion, setOccasion] = useState('');
  const [data, setData] = useState<OutfitResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<Outfit | null>(null);
  const [limit, setLimit] = useState(6);
  const [reload, setReload] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const picked = useRef(false);

  // Adopt the newest model once, then let the choice stay under user control.
  useEffect(() => {
    if (picked.current || !modelJobs.length) return;
    picked.current = true;
    setSource(modelJobs[0].id);
  }, [modelJobs]);

  const query = useMemo(() => {
    const search = new URLSearchParams();
    if (source) search.set('job_id', source);
    if (style) search.set('style', style);
    if (season) search.set('season', season);
    if (occasion) search.set('occasion', occasion);
    search.set('limit', String(limit));
    return search.toString();
  }, [source, style, season, occasion, limit]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError('');
    void api<OutfitResponse>(`/api/outfits?${query}`)
      .then((result) => { if (!cancelled) setData(result); })
      .catch((err) => { if (!cancelled) setError((err as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [query, reload]);

  useEffect(() => {
    if (detail && !dialog.current?.open) dialog.current?.showModal();
    if (!detail && dialog.current?.open) dialog.current.close();
  }, [detail]);

  const analysis = data?.analysis;
  const recommendations = data?.recommendations || [];
  const analyzed = Boolean(analysis?.available);

  return <main className="outfits-page outfits-workspace">
    <section className="outfits-control-panel" aria-label="推荐设置">
      <div className="panel-heading"><h2><Sparkles size={16} /> 推荐设置</h2><span>01</span></div>
      <div className="outfits-control-scroll">
        <label className="field-label" htmlFor="outfits-source">人体分析来源</label>
        <div className="outfits-select-wrap">
          <select id="outfits-source" className="text-input" value={source}
            onChange={(event) => setSource(event.target.value)}>
            <option value="">不使用三维模型 · 通用推荐</option>
            {modelJobs.map((item) => <option key={item.id} value={item.id}>
              {item.name} · {new Date(item.created * 1000).toLocaleDateString('zh-CN')}
            </option>)}
          </select>
          <ChevronDown size={15} aria-hidden="true" />
        </div>
        <p className="outfits-control-hint">
          {source ? '解析该任务最新的 GLB 产物，估算肩宽、腰线、胯宽与腿身比。'
            : '还没有三维模型也可以推荐；生成模型后推荐会更贴合你的比例。'}
        </p>
        <div className="divider" />
        <FilterGroup label="风格" options={data?.filters?.styles || []} value={style} onChange={setStyle} />
        <FilterGroup label="季节" options={data?.filters?.seasons || []} value={season} onChange={setSeason} />
        <FilterGroup label="场合" options={data?.filters?.occasions || []} value={occasion} onChange={setOccasion} />
      </div>
      <div className="outfits-control-footer">
        <button className="generate-button outfits-generate" type="button" disabled={loading}
          onClick={() => setLimit((value) => (value === 6 ? 24 : 6))}>
          {loading ? <LoaderCircle size={17} className="spin" /> : <Layers3 size={16} />}
          {limit === 6 ? '查看全部套装' : '只看精选 6 套'}<ArrowRight size={16} />
        </button>
        <small>本地精选目录 · 不调用云端服务</small>
      </div>
    </section>

    <section className="outfits-stage" aria-label="人体分析与推荐结果">
      <div className="outfits-stage-heading">
        <div><span className="live-dot" /><strong>{analyzed ? '三维模型分析' : '通用体型推荐'}</strong>
          <span className="muted">/ {analyzed ? '已测量比例' : '等待建模'}</span></div>
        <span className={`outfits-state ${analyzed ? 'ready' : 'empty'}`}>{analyzed ? '已分析' : '未生成'}</span>
      </div>
      <BodySummary body={analysis?.body} />
      {analysis
        ? <AnalysisPanel analysis={analysis} onModeling={onModeling} />
        : error
          ? <div className="outfits-analysis-empty is-error">
            <span className="outfits-analysis-icon"><Unplug size={22} strokeWidth={1.3} /></span>
            <div><h3>暂时读不到推荐</h3><p>{error}</p></div>
            <button className="button small" type="button" onClick={() => setReload((value) => value + 1)}>
              重试 <RotateCcw size={13} /></button>
          </div>
          : <div className="outfits-analysis-empty"><span className="outfits-analysis-icon">
            <LoaderCircle size={22} className="spin" /></span><div><h3>正在读取推荐</h3>
              <p>首次读取本地目录通常在一秒内完成。</p></div></div>}
      {error && analysis && <p className="outfits-error" role="alert">{error}</p>}

      <div className="outfits-results-heading">
        <h3>推荐套装 <span>{recommendations.length}</span></h3>
        <small>{analyzed ? '按模型比例排序 · 点击查看单品与建议' : '按通用体型排序 · 点击查看单品与建议'}</small>
      </div>
      {recommendations.length
        ? <div className={`outfits-grid ${loading ? 'loading' : ''}`}>
          {recommendations.map((outfit) => <OutfitCard key={outfit.id} outfit={outfit}
            analyzed={analyzed} onOpen={() => setDetail(outfit)} />)}
        </div>
        : !error ? <div className="outfits-empty"><Layers3 size={26} strokeWidth={1.2} />
          <span>没有符合条件的套装</span><small>试试把筛选改回「全部」</small></div> : null}
    </section>

    <aside className="outfits-inspector" aria-label="穿搭工作空间">
      <div className="panel-heading"><h2>工作空间</h2><span>02</span></div>
      <div className="outfits-service-card">
        <div className="outfits-service-icon"><Sparkles size={20} strokeWidth={1.5} /></div>
        <div className="service-line"><span>本地 · 穿搭目录</span><b className="ready">已就绪</b></div>
        <div className="service-line"><span>图片检索</span>
          <b className={caps?.outfit_images ? 'ready' : ''}>
            {imageProviderLabel(caps?.image_provider) || '待配置'}</b></div>
        <div className="service-line"><span>本地 · 去背景</span>
          <b className={caps?.segmentation ? 'ready' : ''}>{caps?.segmentation ? '已就绪' : '待安装'}</b></div>
        <div className="service-line"><span>混元 · 图生 3D</span>
          <b className={caps?.geometry ? 'ready' : ''}>{caps?.geometry ? '已配置' : '待配置'}</b></div>
        <button className="text-button" type="button" onClick={onSettings}>
          <Settings2 size={13} /> 打开服务设置 <ArrowRight size={13} /></button>
      </div>
      <div className="outfits-inspector-heading"><h3>可用于分析的模型</h3><span>{modelJobs.length}</span></div>
      {modelJobs.length ? <div className="outfits-model-list">
        {modelJobs.slice(0, 6).map((item) => <button key={item.id} type="button"
          className={source === item.id ? 'active' : ''} onClick={() => setSource(item.id)}>
          <span>{source === item.id ? <Check size={13} /> : <Clock3 size={13} />}</span>
          <b>{item.name}</b>
          <small>{new Date(item.created * 1000).toLocaleDateString('zh-CN')}</small>
        </button>)}
      </div> : <div className="outfits-inspector-empty"><Box size={22} strokeWidth={1.3} />
        <span>还没有三维模型</span><small>生成后可基于实测比例推荐</small></div>}
      <div className="inspector-note"><span>推荐说明</span>
        <p>推荐基于本地精选目录与模型比例估算，仅供搭配参考；穿搭图片按单品与风格从图片检索服务获取并缓存在本机，版权归原作者所有。免 key 的 360 图片在短时间密集请求后可能暂时拒绝服务，此时卡片显示配色示意并给出重试；需要更稳定或授权更清晰的来源，可在设置页切换到 Unsplash 或 Pixabay。</p></div>
    </aside>

    <dialog ref={dialog} className="outfit-dialog" aria-label={detail ? `${detail.name} 穿搭详情` : '穿搭详情'}
      onCancel={() => setDetail(null)}
      onClick={(event) => { if (event.target === dialog.current) setDetail(null); }}>
      {detail && <>
        <div className="outfit-dialog-heading">
          <div><strong>{detail.name}</strong><small>{detail.tagline}</small></div>
          <button type="button" aria-label="关闭穿搭详情" onClick={() => setDetail(null)}><X size={20} /></button>
        </div>
        <div className="outfit-dialog-body">
          <div className="outfit-dialog-board">
            <OutfitGallery outfit={detail} />
          </div>
          <div className="outfit-dialog-info">
            <div className="outfit-dialog-meta">
              <span>{detail.style}</span><span>{detail.season}</span><span>{detail.occasion}</span>
              {analyzed && <span className="outfit-dialog-score">匹配度 {Math.round(detail.score * 100)}</span>}
            </div>
            <p className="outfit-dialog-story">{detail.story}</p>
            <div className="outfit-dialog-reason"><Sparkles size={13} /><p>{detail.reason}</p></div>
            {detail.fit && <div className="outfit-fit">
              <h3><Ruler size={12} /> 尺码匹配 <span>{Math.round(detail.fit.score * 100)}</span></h3>
              <ul className="outfit-fit-list">
                {[...detail.fit.dimensions].sort((left, right) => right.weight - left.weight)
                  .map((dimension) => <li key={dimension.key} className={dimension.state}>
                  <span className="outfit-fit-label">{dimension.label}</span>
                  <span className="outfit-fit-body">{dimension.body_value ?? '—'}
                    {dimension.body_source === 'estimated' && <i>估算</i>}</span>
                  <span className="outfit-fit-range">{dimension.range
                    ? `${dimension.range[0]}–${dimension.range[1]}` : '未标注'}</span>
                  <b className={`outfit-fit-state ${dimension.state}`}>{fitStateLabels[dimension.state]}</b>
                  <small>{dimension.detail}</small>
                </li>)}
              </ul>
              {detail.fit.warnings.length > 0 && <ul className="outfit-fit-warn">
                {detail.fit.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>}
              {detail.fit.reasons.length > 0 && <ul className="outfit-fit-reasons">
                {detail.fit.reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>}
              <p className="outfit-fit-confidence">匹配可信度 {Math.round(detail.fit.confidence * 100)}%
                {detail.fit.confidence < 0.6 && ' · 数据不全，建议补充身高与三围'}</p>
            </div>}
            <h3>套装单品 <span>{detail.items.length}</span></h3>
            <ul className="outfit-item-list">
              {detail.items.map((item, index) => <li key={`${item.category}-${index}`}>
                <span className="outfit-item-color" style={{ background: item.color }} />
                <div><strong>{item.name}</strong><small>{item.category} · {item.note}</small></div>
              </li>)}
            </ul>
            <h3>穿着建议</h3>
            <ul className="outfit-tips">{detail.tips.map((tip) => <li key={tip}>{tip}</li>)}</ul>
            <p className="outfit-avoid"><b>不适合</b>{detail.avoid}</p>
          </div>
        </div>
      </>}
    </dialog>
  </main>;
}
