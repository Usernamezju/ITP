import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle, Check, ImagePlus, KeyRound, LoaderCircle, LogOut, Package, Plus, Ruler,
  Store, Trash2, Upload,
} from 'lucide-react';
import { ApiError } from './api';
import {
  GarmentMetrics, GarmentOptions, LookDraft, MerchantGarment, MerchantLook,
  MerchantProfile, addGarmentImages, changePassword, createGarment, createLook,
  deleteGarment, deleteGarmentImage, deleteLook, fetchGarmentOptions, fetchProfile,
  listGarments, listLooks, loginMerchant, merchantToken, registerMerchant,
  updateGarment, updateLook,
} from './merchantApi';
import './MerchantPage.css';

/**
 * The merchant console.  A shop signs in, imports garments by filling the same
 * metrics the recommendation scores against, uploads its own product photos and
 * composes looks.  Every field, bound and dropdown comes from
 * ``/api/garment-options``, which is generated from the backend's validators, so
 * the form cannot accept something the API would reject.
 */

const COLOR_PATTERN = /^#[0-9a-f]{6}$/;

type Draft = {
  category: string; name: string; brand: string; sku: string; price: string;
  style: string; season: string; occasion: string; status: 'draft' | 'published';
  description: string; tips: string; color: string; silhouette: string;
  stretch: string; length_type: string; weight: string;
  measurements: Record<string, string>;
  ranges: Record<string, { min: string; max: string }>;
};

function emptyDraft(options: GarmentOptions): Draft {
  return {
    category: options.categories[0] || '', name: '', brand: '', sku: '', price: '',
    style: options.styles[0] || '', season: options.seasons[0] || '',
    occasion: '', status: 'draft', description: '', tips: '',
    color: '#cccccc', silhouette: options.silhouettes[0] || '',
    stretch: options.stretches[0] || '', length_type: options.length_types[0] || '',
    weight: '',
    measurements: Object.fromEntries(options.measurements.map((item) => [item.key, ''])),
    ranges: Object.fromEntries(
      options.fit_ranges.map((item) => [item.key, { min: '', max: '' }]),
    ),
  };
}

function draftFrom(garment: MerchantGarment, options: GarmentOptions): Draft {
  const draft = emptyDraft(options);
  const metrics = garment.metrics;
  draft.category = metrics.category || draft.category;
  draft.name = metrics.name || '';
  draft.brand = metrics.brand || '';
  draft.sku = metrics.sku || '';
  draft.price = metrics.price_cents == null ? '' : String(metrics.price_cents / 100);
  draft.style = metrics.style || draft.style;
  draft.season = metrics.season || draft.season;
  draft.occasion = metrics.occasion || '';
  draft.status = metrics.status === 'published' ? 'published' : 'draft';
  draft.description = metrics.description || '';
  draft.tips = (metrics.tips || []).join('\n');
  draft.color = metrics.attributes?.color || draft.color;
  draft.silhouette = metrics.attributes?.silhouette || draft.silhouette;
  draft.stretch = metrics.attributes?.stretch || draft.stretch;
  draft.length_type = metrics.attributes?.length_type || draft.length_type;
  draft.weight = metrics.attributes?.weight_gsm == null
    ? '' : String(metrics.attributes.weight_gsm);
  for (const item of options.measurements) {
    const value = metrics.measurements?.[item.key];
    draft.measurements[item.key] = value == null ? '' : String(value);
  }
  for (const item of options.fit_ranges) {
    const value = metrics.fit_ranges?.[item.key];
    draft.ranges[item.key] = value
      ? { min: String(value[0]), max: String(value[1]) } : { min: '', max: '' };
  }
  return draft;
}

function numberFrom(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** Everything the API would reject, reported before it is ever sent. */
function validate(draft: Draft, options: GarmentOptions): Record<string, string> {
  const limits = options.limits;
  const errors: Record<string, string> = {};
  if (!draft.name.trim()) errors.name = '请填写商品名称';
  else if (draft.name.trim().length > limits.name_max) {
    errors.name = `名称不能超过 ${limits.name_max} 个字符`;
  }
  if (draft.price.trim()) {
    const cents = Math.round(Number(draft.price) * 100);
    if (!Number.isFinite(cents) || cents < 0) errors.price = '价格必须是不小于 0 的数字';
    else if (cents > limits.price_max_cents) errors.price = '价格超出可接受范围';
  }
  if (draft.description.length > limits.description_max) {
    errors.description = `描述不能超过 ${limits.description_max} 个字符`;
  }
  if (draft.color && !COLOR_PATTERN.test(draft.color)) errors.color = '主色需为 #rrggbb';
  if (draft.weight.trim()) {
    const value = numberFrom(draft.weight);
    if (value == null || !Number.isInteger(value)
      || value < limits.weight_gsm_min || value > limits.weight_gsm_max) {
      errors.weight = `克重需为 ${limits.weight_gsm_min}-${limits.weight_gsm_max} 的整数`;
    }
  }
  for (const item of options.measurements) {
    const text = draft.measurements[item.key];
    if (!text.trim()) continue;
    const value = numberFrom(text);
    if (value == null || value <= 0 || value > item.max) {
      errors[`m.${item.key}`] = `${item.label}需为 0-${item.max} 之间的厘米数`;
    }
  }
  for (const item of options.fit_ranges) {
    const { min, max } = draft.ranges[item.key];
    const hasMin = Boolean(min.trim());
    const hasMax = Boolean(max.trim());
    if (!hasMin && !hasMax) continue;
    if (hasMin !== hasMax) {
      errors[`r.${item.key}`] = `${item.label}需要同时填写下限与上限`;
      continue;
    }
    const low = numberFrom(min);
    const high = numberFrom(max);
    if (low == null || high == null || low > high) {
      errors[`r.${item.key}`] = `${item.label}的下限不能大于上限`;
    } else if (low < item.min || high > item.max) {
      errors[`r.${item.key}`] = `${item.label}需在 ${item.min}-${item.max}cm 之间`;
    }
  }
  const tips = draft.tips.split('\n').map((line) => line.trim()).filter(Boolean);
  if (tips.length > limits.tips_max) errors.tips = `最多 ${limits.tips_max} 条建议`;
  else if (tips.some((tip) => tip.length > limits.tip_max)) {
    errors.tips = `每条建议不超过 ${limits.tip_max} 个字符`;
  }
  return errors;
}

function metricsFrom(draft: Draft, options: GarmentOptions): GarmentMetrics {
  const tips = draft.tips.split('\n').map((line) => line.trim()).filter(Boolean);
  return {
    category: draft.category,
    name: draft.name.trim(),
    brand: draft.brand.trim() || null,
    sku: draft.sku.trim() || null,
    price_cents: draft.price.trim() ? Math.round(Number(draft.price) * 100) : null,
    style: draft.style || null,
    season: draft.season || null,
    occasion: draft.occasion.trim() || null,
    description: draft.description.trim() || null,
    tips: tips.length ? tips : null,
    status: draft.status,
    measurements: Object.fromEntries(options.measurements.map((item) => [
      item.key, numberFrom(draft.measurements[item.key]),
    ])),
    fit_ranges: Object.fromEntries(options.fit_ranges.map((item) => {
      const { min, max } = draft.ranges[item.key];
      const low = numberFrom(min);
      const high = numberFrom(max);
      return [item.key, low == null || high == null ? null : [low, high]];
    })),
    attributes: {
      silhouette: draft.silhouette || null,
      stretch: draft.stretch || null,
      length_type: draft.length_type || null,
      weight_gsm: numberFrom(draft.weight),
      color: draft.color || null,
    },
  };
}

function rangeSummary(garment: MerchantGarment, options: GarmentOptions): string {
  const parts = options.fit_ranges
    .map((item) => {
      const value = garment.metrics.fit_ranges?.[item.key];
      return value ? `${item.label} ${value[0]}–${value[1]}` : '';
    })
    .filter(Boolean);
  return parts.length ? parts.join(' · ') : '尚未填写适配区间';
}

function priceLabel(garment: MerchantGarment): string {
  const cents = garment.metrics.price_cents;
  return cents == null ? '未标价' : `¥${(cents / 100).toFixed(2)}`;
}

// --- sign in -----------------------------------------------------------------

function AuthPanel({ onSignedIn, onError }: {
  onSignedIn: (profile: MerchantProfile) => void; onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<'register' | 'login'>('register');
  const [fields, setFields] = useState({ name: '', display_name: '', contact: '', password: '' });
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    onError('');
    try {
      if (mode === 'register') {
        await registerMerchant(fields);
      }
      const token = await loginMerchant(fields.name, fields.password);
      merchantToken.write(token);
      onSignedIn(await fetchProfile());
    } catch (error) {
      onError(error instanceof Error ? error.message : '操作失败');
    } finally {
      setBusy(false);
    }
  };

  return <div className="merchant-auth">
    <form className="merchant-auth-card" onSubmit={submit}>
      <div className="merchant-auth-heading">
        <Store size={20} strokeWidth={1.5} />
        <div><strong>{mode === 'register' ? '注册商家账号' : '商家登录'}</strong>
          <small>商品先存为草稿，确认无误后再发布到推荐页</small></div>
      </div>
      <div className="merchant-auth-tabs">
        <button type="button" className={mode === 'register' ? 'active' : ''}
          onClick={() => setMode('register')}>注册</button>
        <button type="button" className={mode === 'login' ? 'active' : ''}
          onClick={() => setMode('login')}>登录</button>
      </div>
      <label className="field-label" htmlFor="merchant-name">账号</label>
      <input id="merchant-name" className="text-input" required autoComplete="username"
        placeholder="3-32 位字母、数字、下划线或短横线" value={fields.name}
        onChange={(event) => setFields({ ...fields, name: event.target.value })} />
      {mode === 'register' && <>
        <label className="field-label" htmlFor="merchant-display">商家名称</label>
        <input id="merchant-display" className="text-input" required
          placeholder="展示在推荐页上的名称" value={fields.display_name}
          onChange={(event) => setFields({ ...fields, display_name: event.target.value })} />
        <label className="field-label" htmlFor="merchant-contact">联系方式</label>
        <input id="merchant-contact" className="text-input" placeholder="选填，例如邮箱"
          value={fields.contact}
          onChange={(event) => setFields({ ...fields, contact: event.target.value })} />
      </>}
      <label className="field-label" htmlFor="merchant-password">密码</label>
      <input id="merchant-password" className="text-input" type="password" required
        autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
        placeholder="8-128 位" value={fields.password}
        onChange={(event) => setFields({ ...fields, password: event.target.value })} />
      <button className="button" type="submit" disabled={busy}>
        {busy ? <><LoaderCircle size={15} className="spin" /> 处理中…</>
          : mode === 'register' ? '注册并登录' : '登录'}
      </button>
      <p className="merchant-auth-note">
        账号只保护你自己的商品数据：本机部署下页面本身不对公网开放，
        但部署到服务器时请先为后台加上访问控制。
      </p>
    </form>
  </div>;
}

// --- garment editor ----------------------------------------------------------

function GarmentEditor({ options, garment, onSaved, onCancel, onError }: {
  options: GarmentOptions; garment: MerchantGarment | null;
  onSaved: () => void; onCancel: () => void; onError: (message: string) => void;
}) {
  const [draft, setDraft] = useState<Draft>(
    () => (garment ? draftFrom(garment, options) : emptyDraft(options)));
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [touched, setTouched] = useState(false);
  const errors = useMemo(() => validate(draft, options), [draft, options]);
  const limits = options.limits;

  const update = (patch: Partial<Draft>) => setDraft((old) => ({ ...old, ...patch }));

  const pickFiles = (chosen: FileList | null) => {
    if (!chosen) return;
    const incoming = Array.from(chosen);
    const tooBig = incoming.filter((file) => file.size > limits.image_max_mb * 1024 * 1024);
    if (tooBig.length) {
      onError(`图片需小于 ${limits.image_max_mb}MB：${tooBig.map((f) => f.name).join('、')}`);
    }
    const room = limits.images_max - (garment?.images.length || 0) - files.length;
    const accepted = incoming.filter((file) => !tooBig.includes(file)).slice(0, Math.max(room, 0));
    if (accepted.length < incoming.length - tooBig.length) {
      onError(`最多上传 ${limits.images_max} 张图片`);
    }
    setFiles((old) => [...old, ...accepted]);
  };

  const save = async (status: 'draft' | 'published') => {
    setTouched(true);
    if (Object.keys(errors).length) {
      onError('还有字段未填对，请看表单里的提示');
      return;
    }
    setSaving(true);
    onError('');
    try {
      const metrics = metricsFrom({ ...draft, status }, options);
      if (garment) {
        await updateGarment(garment.id, metrics);
        if (files.length) await addGarmentImages(garment.id, files);
      } else {
        await createGarment(metrics, files);
      }
      onSaved();
    } catch (error) {
      onError(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const removeImage = async (imageId: string) => {
    if (!garment) return;
    try {
      await deleteGarmentImage(garment.id, imageId);
      onSaved();
    } catch (error) {
      onError(error instanceof Error ? error.message : '删除图片失败');
    }
  };

  const field = (key: string) => (touched ? errors[key] : '') || '';

  return <div className="merchant-editor">
    <div className="section-heading">
      <h2>{garment ? '编辑商品' : '新建商品'} <span>{draft.status === 'published' ? '发布' : '草稿'}</span></h2>
      <small>指标填得越完整，推荐时的尺码匹配越准</small>
    </div>

    <div className="merchant-form-grid">
      <section className="merchant-form-block">
        <h3><Package size={13} /> 基本信息</h3>
        <label className="field-label" htmlFor="g-name">商品名称</label>
        <input id="g-name" className={`text-input ${field('name') ? 'invalid' : ''}`}
          maxLength={limits.name_max} value={draft.name}
          onChange={(event) => update({ name: event.target.value })} />
        {field('name') && <p className="field-error">{field('name')}</p>}
        <div className="merchant-row">
          <label className="select-row">品类
            <select aria-label="品类" value={draft.category}
              onChange={(event) => update({ category: event.target.value })}>
              {options.categories.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
          <label className="select-row">风格
            <select aria-label="风格" value={draft.style}
              onChange={(event) => update({ style: event.target.value })}>
              {options.styles.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
        </div>
        <div className="merchant-row">
          <label className="select-row">季节
            <select aria-label="季节" value={draft.season}
              onChange={(event) => update({ season: event.target.value })}>
              {options.seasons.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
          <label className="field-label" htmlFor="g-occasion">场合
            <input id="g-occasion" className="text-input" placeholder="例如 通勤办公"
              maxLength={limits.short_text_max} value={draft.occasion}
              onChange={(event) => update({ occasion: event.target.value })} /></label>
        </div>
        <div className="merchant-row">
          <label className="field-label" htmlFor="g-brand">品牌
            <input id="g-brand" className="text-input" maxLength={limits.short_text_max}
              value={draft.brand}
              onChange={(event) => update({ brand: event.target.value })} /></label>
          <label className="field-label" htmlFor="g-sku">货号
            <input id="g-sku" className="text-input" maxLength={limits.short_text_max}
              value={draft.sku}
              onChange={(event) => update({ sku: event.target.value })} /></label>
        </div>
        <label className="field-label" htmlFor="g-price">价格（元）</label>
        <input id="g-price" className={`text-input ${field('price') ? 'invalid' : ''}`}
          inputMode="decimal" placeholder="选填，例如 269" value={draft.price}
          onChange={(event) => update({ price: event.target.value })} />
        {field('price') && <p className="field-error">{field('price')}</p>}
        <label className="field-label" htmlFor="g-description">商品描述</label>
        <textarea id="g-description" className={`text-input ${field('description') ? 'invalid' : ''}`}
          rows={3} maxLength={limits.description_max} value={draft.description}
          onChange={(event) => update({ description: event.target.value })} />
        {field('description') && <p className="field-error">{field('description')}</p>}
        <label className="field-label" htmlFor="g-tips">穿着建议（每行一条）</label>
        <textarea id="g-tips" className={`text-input ${field('tips') ? 'invalid' : ''}`}
          rows={2} value={draft.tips}
          onChange={(event) => update({ tips: event.target.value })} />
        {field('tips') && <p className="field-error">{field('tips')}</p>}
      </section>

      <section className="merchant-form-block">
        <h3><Ruler size={13} /> 尺寸（厘米，平铺实测）</h3>
        <div className="merchant-measure-grid">
          {options.measurements.map((item) => <label key={item.key} className="field-label">
            {item.label}
            <input className={`text-input ${field(`m.${item.key}`) ? 'invalid' : ''}`}
              aria-label={`${item.label}（cm）`} inputMode="decimal" placeholder="选填"
              value={draft.measurements[item.key]}
              onChange={(event) => update({
                measurements: { ...draft.measurements, [item.key]: event.target.value },
              })} />
            {field(`m.${item.key}`) && <em className="field-error">{field(`m.${item.key}`)}</em>}
          </label>)}
        </div>

        <h3><Ruler size={13} /> 适配区间（适合什么样的人穿）</h3>
        <p className="merchant-hint">推荐算法主要看这一组：命中区间即为合身。</p>
        {options.fit_ranges.map((item) => <div key={item.key} className="merchant-range-row">
          <span>{item.label}</span>
          <input className={`text-input ${field(`r.${item.key}`) ? 'invalid' : ''}`}
            aria-label={`${item.label}下限`} inputMode="decimal"
            placeholder={String(item.min)} value={draft.ranges[item.key].min}
            onChange={(event) => update({
              ranges: {
                ...draft.ranges,
                [item.key]: { ...draft.ranges[item.key], min: event.target.value },
              },
            })} />
          <i>–</i>
          <input className={`text-input ${field(`r.${item.key}`) ? 'invalid' : ''}`}
            aria-label={`${item.label}上限`} inputMode="decimal"
            placeholder={String(item.max)} value={draft.ranges[item.key].max}
            onChange={(event) => update({
              ranges: {
                ...draft.ranges,
                [item.key]: { ...draft.ranges[item.key], max: event.target.value },
              },
            })} />
          {field(`r.${item.key}`) && <em className="field-error">{field(`r.${item.key}`)}</em>}
        </div>)}
      </section>

      <section className="merchant-form-block">
        <h3><Check size={13} /> 版型与属性</h3>
        <div className="merchant-row">
          <label className="select-row">版型
            <select aria-label="版型" value={draft.silhouette}
              onChange={(event) => update({ silhouette: event.target.value })}>
              {options.silhouettes.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
          <label className="select-row">弹性
            <select aria-label="弹性" value={draft.stretch}
              onChange={(event) => update({ stretch: event.target.value })}>
              {options.stretches.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
        </div>
        <div className="merchant-row">
          <label className="select-row">长度类型
            <select aria-label="长度类型" value={draft.length_type}
              onChange={(event) => update({ length_type: event.target.value })}>
              {options.length_types.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
          <label className="field-label" htmlFor="g-weight">克重（g/m²）
            <input id="g-weight" className={`text-input ${field('weight') ? 'invalid' : ''}`}
              inputMode="numeric" value={draft.weight}
              onChange={(event) => update({ weight: event.target.value })} /></label>
        </div>
        {field('weight') && <p className="field-error">{field('weight')}</p>}
        <label className="field-label" htmlFor="g-color">主色
          <input id="g-color" type="color" className="merchant-color"
            value={COLOR_PATTERN.test(draft.color) ? draft.color : '#cccccc'}
            onChange={(event) => update({ color: event.target.value })} /></label>
        {field('color') && <p className="field-error">{field('color')}</p>}
        <p className="merchant-hint">用于按色系匹配与撞色提醒，取大面积那一种即可。</p>

        <h3><ImagePlus size={13} /> 商品图（最多 {limits.images_max} 张）</h3>
        {garment && garment.images.length > 0 && <div className="merchant-thumbs">
          {garment.images.map((image) => <figure key={image.id}>
            <img src={image.url} alt="已上传的商品图" />
            <button type="button" aria-label="删除这张商品图"
              onClick={() => void removeImage(image.id)}><Trash2 size={12} /></button>
          </figure>)}
        </div>}
        <label className="merchant-drop">
          <Upload size={15} /> 选择图片
          <input type="file" accept="image/png,image/jpeg,image/webp" multiple hidden
            aria-label="选择商品图片"
            onChange={(event) => { pickFiles(event.target.files); event.target.value = ''; }} />
        </label>
        {files.length > 0 && <ul className="merchant-pending">
          {files.map((file, index) => <li key={`${file.name}-${index}`}>
            {file.name}
            <button type="button" aria-label={`移除${file.name}`}
              onClick={() => setFiles((old) => old.filter((_item, at) => at !== index))}>×</button>
          </li>)}
        </ul>}
        <p className="merchant-hint">PNG / JPEG / WebP，单张小于 {limits.image_max_mb}MB。</p>
      </section>
    </div>

    <div className="merchant-actions">
      <button className="button small" type="button" onClick={onCancel} disabled={saving}>取消</button>
      <button className="button small" type="button" disabled={saving}
        onClick={() => void save('draft')}>保存为草稿</button>
      <button className="button" type="button" disabled={saving}
        onClick={() => void save('published')}>
        {saving ? <><LoaderCircle size={15} className="spin" /> 保存中…</> : '保存并发布'}</button>
    </div>
  </div>;
}

// --- look composer -----------------------------------------------------------

function LookEditor({ options, garments, look, onSaved, onCancel, onError }: {
  options: GarmentOptions; garments: MerchantGarment[]; look: MerchantLook | null;
  onSaved: () => void; onCancel: () => void; onError: (message: string) => void;
}) {
  const published = garments.filter((item) => item.status === 'published');
  const [draft, setDraft] = useState<LookDraft>(() => ({
    name: look?.name || '',
    story: look?.story || '',
    style: look?.style || options.styles[0] || '',
    season: look?.season || options.seasons[0] || '',
    occasion: look?.occasion || '',
    palette: look?.palette || [],
    status: look?.status === 'published' ? 'published' : 'draft',
    items: look ? look.items.map((item) => item.id) : [],
  }));
  const [saving, setSaving] = useState(false);
  const limits = options.limits;
  const memberColors = draft.items
    .map((id) => published.find((item) => item.id === id))
    .map((item) => item?.metrics.attributes?.color)
    .filter((color): color is string => Boolean(color));
  const palette = draft.palette?.length ? draft.palette : memberColors.slice(0, 4);

  const toggle = (id: string) => setDraft((old) => ({
    ...old,
    items: old.items.includes(id)
      ? old.items.filter((item) => item !== id)
      : old.items.length >= limits.look_items_max ? old.items : [...old.items, id],
  }));

  const save = async (status: 'draft' | 'published') => {
    if (!draft.name.trim()) { onError('请填写套装名称'); return; }
    if (!draft.items.length) { onError('至少选择一件已发布的单品'); return; }
    setSaving(true);
    onError('');
    try {
      const payload: LookDraft = {
        ...draft, name: draft.name.trim(), story: draft.story?.trim() || null,
        occasion: draft.occasion.trim(), palette, status,
      };
      if (look) await updateLook(look.id, payload);
      else await createLook(payload);
      onSaved();
    } catch (error) {
      onError(error instanceof Error ? error.message : '保存套装失败');
    } finally {
      setSaving(false);
    }
  };

  return <div className="merchant-editor">
    <div className="section-heading">
      <h2>{look ? '编辑套装' : '新建套装'}</h2>
      <small>套装按成员单品的分类加权得分，适配区间取交集</small>
    </div>
    <div className="merchant-form-grid">
      <section className="merchant-form-block">
        <h3><Store size={13} /> 套装信息</h3>
        <label className="field-label" htmlFor="l-name">套装名称</label>
        <input id="l-name" className="text-input" maxLength={limits.look_name_max}
          value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        <label className="field-label" htmlFor="l-story">搭配故事</label>
        <textarea id="l-story" className="text-input" rows={3} maxLength={limits.look_story_max}
          placeholder="一句话说清这套怎么穿、为什么好看"
          value={draft.story || ''}
          onChange={(event) => setDraft({ ...draft, story: event.target.value })} />
        <div className="merchant-row">
          <label className="select-row">风格
            <select aria-label="套装风格" value={draft.style}
              onChange={(event) => setDraft({ ...draft, style: event.target.value })}>
              {options.styles.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
          <label className="select-row">季节
            <select aria-label="套装季节" value={draft.season}
              onChange={(event) => setDraft({ ...draft, season: event.target.value })}>
              {options.seasons.map((item) => <option key={item} value={item}>{item}</option>)}
            </select></label>
        </div>
        <label className="field-label" htmlFor="l-occasion">场合
          <input id="l-occasion" className="text-input" maxLength={limits.short_text_max}
            value={draft.occasion}
            onChange={(event) => setDraft({ ...draft, occasion: event.target.value })} /></label>
        <div className="merchant-palette">
          {palette.map((color) => <i key={color} style={{ background: color }} title={color} />)}
        </div>
      </section>

      <section className="merchant-form-block">
        <h3><Package size={13} /> 选择单品（{draft.items.length}/{limits.look_items_max}）</h3>
        {!published.length && <p className="merchant-hint">
          还没有已发布的单品：先把商品发布，才能组合成套装。</p>}
        <ul className="merchant-picker">
          {published.map((item) => <li key={item.id}>
            <label>
              <input type="checkbox" checked={draft.items.includes(item.id)}
                onChange={() => toggle(item.id)} />
              <span className="merchant-picker-thumb">
                {item.images[0]
                  ? <img src={item.images[0].url} alt="" />
                  : <i style={{ background: item.metrics.attributes?.color || '#cccccc' }} />}
              </span>
              <span className="merchant-picker-text">
                <strong>{item.metrics.name}</strong>
                <small>{item.metrics.category} · {rangeSummary(item, options)}</small>
              </span>
            </label>
          </li>)}
        </ul>
      </section>
    </div>
    <div className="merchant-actions">
      <button className="button small" type="button" onClick={onCancel} disabled={saving}>取消</button>
      <button className="button small" type="button" disabled={saving}
        onClick={() => void save('draft')}>保存为草稿</button>
      <button className="button" type="button" disabled={saving}
        onClick={() => void save('published')}>
        {saving ? <><LoaderCircle size={15} className="spin" /> 保存中…</> : '保存并发布'}</button>
    </div>
  </div>;
}

// --- account -----------------------------------------------------------------

function AccountPanel({ profile, onChanged, onCancel, onError }: {
  profile: MerchantProfile; onChanged: (message: string) => void;
  onCancel: () => void; onError: (message: string) => void;
}) {
  const [fields, setFields] = useState({ current: '', next: '', again: '' });
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);

  const problem = !fields.next
    ? '' : fields.next.length < 8 ? '新密码至少 8 位'
      : fields.next.length > 128 ? '新密码不能超过 128 位'
        : fields.next === fields.current ? '新密码不能与当前密码相同'
          : fields.again && fields.again !== fields.next ? '两次输入的新密码不一致' : '';

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!fields.current) { onError('请输入当前密码'); return; }
    // The banner stays generic; the reason sits next to the field.
    if (problem || !fields.again) { onError('还有字段未填对，请看表单里的提示'); return; }
    setBusy(true);
    onError('');
    try {
      await changePassword(fields.current, fields.next);
      onChanged('密码已修改，旧登录状态已失效，请用新密码重新登录');
    } catch (error) {
      onError(error instanceof Error ? error.message : '修改密码失败');
    } finally {
      setBusy(false);
    }
  };

  return <form className="merchant-account" onSubmit={submit}>
    <div className="section-heading">
      <h2>账号设置</h2>
      <small>修改本账号的登录密码</small>
    </div>
    <section className="merchant-form-block">
      <h3><KeyRound size={13} /> 修改密码</h3>
      <p className="merchant-hint">
        账号 <strong>{profile.name}</strong> · {profile.display_name}。
        修改成功后所有已签发的登录令牌立即失效，需要用新密码重新登录一次。
      </p>
      <label className="field-label" htmlFor="pw-current">当前密码</label>
      <input id="pw-current" className="text-input" type="password" autoComplete="current-password"
        value={fields.current}
        onChange={(event) => setFields({ ...fields, current: event.target.value })} />
      <label className="field-label" htmlFor="pw-next">新密码</label>
      <input id="pw-next" className={`text-input ${touched && problem ? 'invalid' : ''}`}
        type="password" autoComplete="new-password" placeholder="8-128 位"
        value={fields.next} onChange={(event) => setFields({ ...fields, next: event.target.value })} />
      {touched && problem && <p className="field-error">{problem}</p>}
      <label className="field-label" htmlFor="pw-again">确认新密码</label>
      <input id="pw-again" className="text-input" type="password" autoComplete="new-password"
        value={fields.again} onChange={(event) => setFields({ ...fields, again: event.target.value })} />
      <div className="merchant-actions">
        <button className="button small" type="button" onClick={onCancel} disabled={busy}>返回</button>
        <button className="button" type="submit" disabled={busy}>
          {busy ? <><LoaderCircle size={15} className="spin" /> 提交中…</> : '修改密码'}</button>
      </div>
      <p className="merchant-hint">
        如果连当前密码也忘了：在本机运行
        <code> scripts/reset_merchant_password.py --name {profile.name} </code>
        直接重置（需要能访问数据目录）。
      </p>
    </section>
  </form>;
}

// --- the console -------------------------------------------------------------

export function MerchantPage() {
  const [options, setOptions] = useState<GarmentOptions | null>(null);
  const [profile, setProfile] = useState<MerchantProfile | null>(null);
  const [checking, setChecking] = useState(true);
  const [garments, setGarments] = useState<MerchantGarment[]>([]);
  const [looks, setLooks] = useState<MerchantLook[]>([]);
  const [view, setView] = useState<'goods' | 'garment' | 'looks' | 'look' | 'account'>('goods');
  const [editing, setEditing] = useState<MerchantGarment | null>(null);
  const [editingLook, setEditingLook] = useState<MerchantLook | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    void fetchGarmentOptions().then(setOptions)
      .catch((error: Error) => setNotice(error.message));
  }, []);

  const logout = useCallback((message = '') => {
    merchantToken.write('');
    setProfile(null);
    setGarments([]);
    setLooks([]);
    setView('goods');
    setNotice(message);
  }, []);

  const reload = useCallback(async () => {
    const [goods, looks, me] = await Promise.all([listGarments(), listLooks(), fetchProfile()]);
    setGarments(goods.items);
    setLooks(looks.items);
    setProfile(me);
  }, []);

  useEffect(() => {
    if (!merchantToken.read()) { setChecking(false); return; }
    void reload()
      .catch(() => logout('登录状态已失效，请重新登录'))
      .finally(() => setChecking(false));
  }, [logout, reload]);

  const guard = useCallback(async (run: () => Promise<void>) => {
    try {
      await run();
    } catch (error) {
      const message = error instanceof Error ? error.message : '操作失败';
      if (error instanceof ApiError && error.status === 401) logout(`登录状态已失效：${message}`);
      else setNotice(message);
    }
  }, [logout]);

  if (!options || checking) {
    return <section className="merchant-page">
      <div className="merchant-loading"><LoaderCircle size={18} className="spin" /> 正在载入商家后台…</div>
    </section>;
  }

  if (!profile) {
    return <section className="merchant-page">
      {notice && <div className="error-banner" role="alert">{notice}
        <button aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}
      <AuthPanel onSignedIn={(loaded) => { setProfile(loaded); setNotice('');
        void guard(reload); }} onError={setNotice} />
    </section>;
  }

  const used = profile.garment_count;

  return <section className="merchant-page">
    <div className="merchant-bar">
      <div className="merchant-identity">
        <Store size={17} strokeWidth={1.5} />
        <div><strong>{profile.display_name}</strong>
          <small>@{profile.name}{profile.contact ? ` · ${profile.contact}` : ''}</small></div>
      </div>
      <div className="merchant-quota">
        <span>商品 {used}/{profile.quota}</span>
        <span className="merchant-quota-bar"><i style={{
          width: `${Math.min(100, Math.round((used / Math.max(profile.quota, 1)) * 100))}%`,
        }} /></span>
      </div>
      <button className="text-button" type="button" onClick={() => setView('account')}>
        <KeyRound size={14} /> 账号设置</button>
      <button className="text-button" type="button" onClick={() => logout('已退出登录')}>
        <LogOut size={14} /> 退出登录</button>
    </div>

    {notice && <div className="error-banner" role="alert">{notice}
      <button aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}

    {view === 'account' ? <AccountPanel profile={profile} onError={setNotice}
      onCancel={() => setView('goods')}
      onChanged={(message) => logout(message)} />
      : view === 'garment' ? <GarmentEditor options={options} garment={editing}
      onError={setNotice}
      onCancel={() => { setView('goods'); setEditing(null); }}
      onSaved={() => { setView('goods'); setEditing(null); setNotice('');
        void guard(reload); }} />
      : view === 'look' ? <LookEditor options={options} garments={garments} look={editingLook}
        onError={setNotice}
        onCancel={() => { setView('looks'); setEditingLook(null); }}
        onSaved={() => { setView('looks'); setEditingLook(null); setNotice('');
          void guard(reload); }} />
        : <>
          <div className="section-heading">
            <h2>{view === 'goods' ? '我的商品' : '我的套装'} <span>
              {view === 'goods' ? garments.length : looks.length}</span></h2>
            <div className="merchant-heading-actions">
              <button className={`text-button ${view === 'goods' ? 'selected' : ''}`} type="button"
                onClick={() => setView('goods')}>商品</button>
              <button className={`text-button ${view === 'looks' ? 'selected' : ''}`} type="button"
                onClick={() => setView('looks')}>套装</button>
              <button className="button small" type="button"
                onClick={() => {
                  if (view === 'goods') { setEditing(null); setView('garment'); }
                  else { setEditingLook(null); setView('look'); }
                }}><Plus size={14} /> {view === 'goods' ? '新建商品' : '新建套装'}</button>
            </div>
          </div>

          {view === 'goods' ? (!garments.length
            ? <div className="merchant-empty"><Package size={30} strokeWidth={1.1} />
              <h3>还没有商品</h3><p>导入第一件商品后，它就会出现在「穿搭推荐」的候选里。</p>
              <button className="button" type="button"
                onClick={() => { setEditing(null); setView('garment'); }}>
                新建商品 <Plus size={15} /></button></div>
            : <ul className="merchant-goods">
              {garments.map((item) => <li key={item.id}>
                <span className="merchant-good-thumb">
                  {item.images[0]
                    ? <img src={item.images[0].url} alt={`${item.metrics.name} 商品图`} />
                    : <i style={{ background: item.metrics.attributes?.color || '#cccccc' }} />}
                </span>
                <div className="merchant-good-text">
                  <strong>{item.metrics.name}</strong>
                  <small>{item.metrics.category} · {priceLabel(item)} · {item.images.length} 张图</small>
                  <em>{rangeSummary(item, options)}</em>
                </div>
                <span className={`merchant-status ${item.status}`}>
                  {item.status === 'published' ? '已发布' : '草稿'}</span>
                <div className="merchant-good-actions">
                  <button className="text-button" type="button"
                    onClick={() => { setEditing(item); setView('garment'); }}>编辑</button>
                  <button className="text-button" type="button" onClick={() => void guard(async () => {
                    await updateGarment(item.id,
                      { status: item.status === 'published' ? 'draft' : 'published' });
                    await reload();
                  })}>{item.status === 'published' ? '转为草稿' : '发布'}</button>
                  <button className="text-button danger" type="button" onClick={() => void guard(async () => {
                    if (!window.confirm(`删除「${item.metrics.name}」？同时会把它从所有套装里移除。`)) return;
                    await deleteGarment(item.id);
                    await reload();
                  })}><Trash2 size={12} /> 删除</button>
                </div>
              </li>)}
            </ul>)
            : (!looks.length
              ? <div className="merchant-empty"><Store size={30} strokeWidth={1.1} />
                <h3>还没有套装</h3><p>把已发布的单品组合成一套，顾客在推荐页就会看到它。</p>
                <button className="button" type="button"
                  onClick={() => { setEditingLook(null); setView('look'); }}>
                  新建套装 <Plus size={15} /></button></div>
              : <ul className="merchant-goods">
                {looks.map((item) => <li key={item.id}>
                  <span className="merchant-look-strip">
                    {item.items.slice(0, 4).map((member) => <i key={member.id} style={{
                      background: member.images[0] ? undefined : (member.metrics.attributes?.color || '#ccc'),
                    }}>{member.images[0] &&
                      <img src={member.images[0].url} alt="" />}</i>)}
                  </span>
                  <div className="merchant-good-text">
                    <strong>{item.name}</strong>
                    <small>{item.items.length} 件 · {item.style} · {item.season}
                      {item.occasion ? ` · ${item.occasion}` : ''}</small>
                    <em>{item.story || '未填写搭配故事'}</em>
                  </div>
                  <span className={`merchant-status ${item.status}`}>
                    {item.status === 'published' ? '已发布' : '草稿'}</span>
                  <div className="merchant-good-actions">
                    <button className="text-button" type="button"
                      onClick={() => { setEditingLook(item); setView('look'); }}>编辑</button>
                    <button className="text-button" type="button" onClick={() => void guard(async () => {
                      await updateLook(item.id,
                        { status: item.status === 'published' ? 'draft' : 'published' });
                      await reload();
                    })}>{item.status === 'published' ? '转为草稿' : '发布'}</button>
                    <button className="text-button danger" type="button" onClick={() => void guard(async () => {
                      if (!window.confirm(`删除套装「${item.name}」？`)) return;
                      await deleteLook(item.id);
                      await reload();
                    })}><Trash2 size={12} /> 删除</button>
                  </div>
                </li>)}
              </ul>)}
        </>}

    <p className="merchant-footnote">
      <AlertCircle size={12} /> 已发布的商品会进入「穿搭推荐」的候选集，并按你填写的适配区间做尺码匹配；
      草稿不会出现在推荐里。指标取值与校验规则见 <code>docs/modules/METRICS.md</code>。
    </p>
  </section>;
}
