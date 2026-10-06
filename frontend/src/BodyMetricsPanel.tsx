import { useEffect, useState } from 'react';
import { Check, LoaderCircle, Ruler } from 'lucide-react';
import { api } from './api';
import './BodyMetricsPanel.css';

/**
 * Optional body measurements, collected on the modelling page.  They are what
 * turns the model's proportions into centimetres, so the outfit page can match
 * real garment size ranges instead of guessing from silhouette bands alone.
 */

type FieldKey = 'height_cm' | 'weight_kg' | 'shoulder_cm' | 'bust_cm' | 'waist_cm' | 'hip_cm';
type BodyProfile = Partial<Record<FieldKey, number | null>> & { job_id?: string | null };

const fields: { key: FieldKey; label: string; unit: string; min: number; max: number; step: number }[] = [
  { key: 'height_cm', label: '身高', unit: 'cm', min: 120, max: 220, step: 1 },
  { key: 'weight_kg', label: '体重', unit: 'kg', min: 30, max: 200, step: 0.5 },
  { key: 'shoulder_cm', label: '肩宽', unit: 'cm', min: 25, max: 70, step: 0.5 },
  { key: 'bust_cm', label: '胸围', unit: 'cm', min: 60, max: 160, step: 0.5 },
  { key: 'waist_cm', label: '腰围', unit: 'cm', min: 45, max: 150, step: 0.5 },
  { key: 'hip_cm', label: '臀围', unit: 'cm', min: 60, max: 170, step: 0.5 },
];

const emptyDraft = () => Object.fromEntries(fields.map((field) => [field.key, ''])) as Record<FieldKey, string>;

export function BodyMetricsPanel({ jobId, onSaved }: { jobId?: string; onSaved?: () => void }) {
  const [draft, setDraft] = useState<Record<FieldKey, string>>(emptyDraft);
  const [stored, setStored] = useState<Record<FieldKey, string>>(emptyDraft);
  const [status, setStatus] = useState<'idle' | 'loading' | 'saving' | 'saved'>('loading');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setStatus('loading'); setError('');
    const query = jobId ? `?job_id=${encodeURIComponent(jobId)}` : '';
    void api<BodyProfile>(`/api/body-profile${query}`)
      .then((profile) => {
        if (!active) return;
        const next = emptyDraft();
        for (const field of fields) {
          const value = profile[field.key];
          next[field.key] = typeof value === 'number' ? String(value) : '';
        }
        setDraft(next); setStored(next); setStatus('idle');
      })
      .catch(() => { if (active) { setStatus('idle'); } });
    return () => { active = false; };
  }, [jobId]);

  function edit(key: FieldKey, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
    setStatus('idle'); setError('');
  }

  function invalid(): string {
    for (const field of fields) {
      const raw = draft[field.key].trim();
      if (!raw) continue;
      const value = Number(raw);
      if (!Number.isFinite(value)) return `${field.label}请填写数字`;
      if (value < field.min || value > field.max) {
        return `${field.label}应在 ${field.min}–${field.max}${field.unit} 之间`;
      }
    }
    return '';
  }

  async function save() {
    const problem = invalid();
    if (problem) { setError(problem); return; }
    setStatus('saving'); setError('');
    const payload: Record<string, number | null | string> = {};
    for (const field of fields) {
      const raw = draft[field.key].trim();
      payload[field.key] = raw ? Number(raw) : null;
    }
    if (jobId) payload.job_id = jobId;
    try {
      const saved = await api<BodyProfile>('/api/body-profile', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const next = emptyDraft();
      for (const field of fields) {
        const value = saved[field.key];
        next[field.key] = typeof value === 'number' ? String(value) : '';
      }
      setDraft(next); setStored(next); setStatus('saved'); onSaved?.();
    } catch (err) {
      setError((err as Error).message); setStatus('idle');
    }
  }

  const dirty = fields.some((field) => draft[field.key] !== stored[field.key]);
  const filled = fields.filter((field) => draft[field.key].trim()).length;

  return <section className="body-metrics" aria-label="人体数据">
    <div className="field-heading">
      <label className="field-label"><Ruler size={12} /> 人体数据</label>
      <span>选填 · {filled}/6</span>
    </div>
    <div className="body-metrics-grid">
      {fields.map((field) => <label key={field.key} className="body-metrics-field">
        <span>{field.label}</span>
        <input className="text-input" type="number" inputMode="decimal" min={field.min} max={field.max}
          step={field.step} placeholder="—" value={draft[field.key]}
          aria-label={`${field.label}（${field.unit}）`}
          onChange={(event) => edit(field.key, event.target.value)} />
        <i>{field.unit}</i>
      </label>)}
    </div>
    <p className="hint">填了身高才能把模型比例换算成厘米；没填的项会用模型比例推算，并在推荐页标注「估算」。</p>
    <div className="body-metrics-actions">
      <button className="text-button" type="button" onClick={() => void save()}
        disabled={status === 'saving' || status === 'loading' || !dirty}>
        {status === 'saving' ? <LoaderCircle size={13} className="spin" /> : <Ruler size={13} />}
        {status === 'saving' ? '保存中' : '保存人体数据'}
      </button>
      {status === 'saved' && <span className="body-metrics-ok"><Check size={13} /> 已保存，可用于尺码推荐</span>}
      {error && <span className="body-metrics-error" role="alert">{error}</span>}
    </div>
  </section>;
}
