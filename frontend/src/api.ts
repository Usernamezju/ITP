import { sessionToken } from './session';

export type Asset = {
  id: string; url: string; kind: string; width?: number; height?: number;
  size: number; format?: string; background_removed?: boolean; remove_background?: boolean;
};
export type TryOnProvider = 'seedream' | 'flux' | 'flux_max' | 'flux_klein' | 'flux_klein_9b' | 'gpt_image';
export type Capabilities = {
  geometry: boolean; pose: boolean; segmentation: boolean;
  tryon: boolean; tryon_model: string;
  tryon_providers: Record<TryOnProvider, boolean>;
  faceverse: boolean; faceverse_model: string;
  outfit_images: boolean; image_provider: string;
  provider: string; pose_provider: string; model: string; pose_model: string;
};
export type FaceRefinement = {
  id: string; state: 'queued' | 'submitting' | 'ready' | 'failed';
  result_asset: string | null; error: string | null; report: Record<string, unknown> | null;
};
export type TryOnJob = {
  id: string; name: string; state: 'queued' | 'running' | 'submitting' | 'ready' | 'failed';
  model: string; active_view: string | null; results: Record<string, string>; error: string | null;
  provider: TryOnProvider;
};
export type BodyField = 'height_cm' | 'weight_kg' | 'shoulder_cm' | 'bust_cm' | 'waist_cm' | 'hip_cm';
export type BodyValue = { value: number | null; source: 'input' | 'estimated' | 'missing' };
/** One compared dimension: the person's value against a garment's size range. */
export type FitDimension = {
  key: string; label: string; weight: number; score: number;
  state: 'fit' | 'tight' | 'loose' | 'unknown';
  body_value: number | null; body_source: 'input' | 'estimated' | 'missing';
  range: [number, number] | null; detail: string; delta_cm: number;
};
export type FitScore = {
  score: number; fit_score: number; preference_score: number; confidence: number;
  dimensions: FitDimension[]; reasons: string[]; warnings: string[]; suggestions: string[];
};
export type OutfitItem = { category: string; name: string; color: string; note: string };
export type Outfit = {
  id: string; name: string; tagline: string; story: string;
  style: string; season: string; occasion: string;
  palette: string[]; items: OutfitItem[]; tips: string[]; avoid: string;
  reason: string; score: number; matched: string[];
  /** Present once recommendations are scored against garment size ranges. */
  fit?: FitScore;
  origin?: 'catalogue' | 'database';
  /** Merchant-uploaded product photos; these win over searched reference images. */
  image_url?: string | null;
  image_urls?: string[];
};
export type BodyAnalysis = {
  available: boolean; method: string; labels: Record<string, string>;
  metrics: { label: string; value: string; hint: string }[];
  profile: number[] | null; notes: string[]; tags: string[];
  /** The measurements collected on the modelling page, with where each came from. */
  body?: Partial<Record<BodyField, BodyValue>>;
};
export type OutfitImage = {
  id: string; url: string; original_url: string; source_url: string; site: string;
  title: string; width: number; height: number; creator: string | null; license: string | null;
};
export type OutfitImages = {
  outfit_id: string; query: string; provider: string; provider_label: string;
  cached: boolean; error: string | null; images: OutfitImage[];
};
export type OutfitFilterOption = { id: string; count: number };
export type OutfitResponse = {
  source: 'model' | 'default';
  analysis: BodyAnalysis;
  filters: { styles: OutfitFilterOption[]; seasons: OutfitFilterOption[]; occasions: OutfitFilterOption[] };
  recommendations: Outfit[];
};
export type PoseMode = 'original' | 'custom' | 'a-pose' | 't-pose';
export type Job = {
  id: string; name: string; state: string; created: number; error: string | null;
  request: { front: string; pose_mode: PoseMode; topology: boolean; texture: boolean;
    rig: boolean; export_fbx: boolean; face_count?: number };
  models?: { geometry?: string; pose?: string };
  pose_asset: string | null;
  steps: { name: string; status: string; provider_job_id?: string; request_id?: string }[];
  artifacts: { asset_id: string; stage: string; format: string; index: number }[];
};

/** An HTTP failure that keeps its status, so callers can react to 401 etc. */
export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = sessionToken.read();
  if (/^\/api\/(auth\/|account\/|merchant\/|jobs(?:\/|$)|tryons\/[^/]+\/continue$)/.test(url)
    && token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail :
      Array.isArray(data.detail) ? data.detail.map((d: { msg: string }) => d.msg).join('；') :
      `请求失败（${response.status}）`;
    throw new ApiError(detail, response.status);
  }
  // Deleting a garment answers 204 with an empty body.
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function post<T>(url: string, body: unknown): Promise<T> {
  const key = `${url}:${JSON.stringify(body)}`;
  const charged = url === '/api/jobs' || /^\/api\/tryons\/[^/]+\/continue$/.test(url);
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (charged) {
    if (!pendingCharges.has(key)) pendingCharges.set(key, crypto.randomUUID());
    headers['Idempotency-Key'] = pendingCharges.get(key)!;
  }
  return api<T>(url, { method: 'POST', headers, body: JSON.stringify(body) }).then((result) => {
    pendingCharges.delete(key); return result;
  }).catch((error) => {
    if (error instanceof ApiError && error.status >= 400 && error.status < 500) pendingCharges.delete(key);
    throw error;
  });
}

const pendingCharges = new Map<string, string>();

export const fileUrl = (id: string) => `/api/assets/${id}/file`;
