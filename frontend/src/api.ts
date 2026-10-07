import { sessionToken } from './session';

export type Asset = {
  id: string; url: string; kind: string; width?: number; height?: number;
  size: number; format?: string; background_removed?: boolean; remove_background?: boolean;
  /** The file name the customer picked, for imported models. */
  name?: string;
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
export type OutfitItem = { category: string; name: string; color: string; note: string;
  garment_id?: string; merchant_id?: string; purchase_url?: string | null; price_cents?: number | null };
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
  price_cents?: number | null;
  ranking?: { score: number; base_score: number; components: Record<string, number>;
    weights: Record<string, number>; reasons: string[]; cold_start: boolean; context_in_base: boolean };
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
  request: { front: string; views?: Record<string, string>; pose_reference?: string | null;
    pose_mode: PoseMode; topology: boolean; texture: boolean;
    rig: boolean; export_fbx: boolean; face_count?: number };
  models?: { geometry?: string; pose?: string };
  pose_asset: string | null;
  steps: { name: string; status: string; provider_job_id?: string; request_id?: string }[];
  artifacts: { asset_id: string; stage: string; format: string; index: number }[];
};

/** One model service as the admin console reports it: configured, and which model. */
export type AdminService = { ready: boolean; model: string };
export type AdminFaceVerse = {
  configured: boolean; reachable: boolean; model: string;
  status: string | null; cuda: string | null; gpu: string | null;
};
export type AdminStatus = {
  version: string;
  services: {
    geometry: boolean; pose: boolean; segmentation: boolean;
    /** The active outfit photo source, e.g. `360图片`. */
    outfit_images: string;
    faceverse: AdminFaceVerse;
    tryon: Record<TryOnProvider, AdminService>;
    flux_klein: AdminService;
    flux_klein_9b: AdminService;
  };
  payments: { id: string; name: string; ready: boolean }[];
};
/** Provider configuration with secrets reduced to `*_set` booleans. */
export type AdminProviderSettings = {
  tencent_endpoint: string; tencent_region: string; tencent_model: string;
  tencent_secret_id_set: boolean; tencent_secret_key_set: boolean;
  pose_endpoint: string; pose_model: string; pose_api_key_set: boolean;
  seedream_endpoint: string; seedream_model: string; seedream_api_key_set: boolean;
  flux_endpoint: string; flux_model: string; flux_api_key_set: boolean;
  flux_max_endpoint: string; flux_max_model: string; flux_max_api_key_set: boolean;
  flux_klein_endpoint: string; flux_klein_model: string; flux_klein_api_key_set: boolean;
  flux_klein_9b_endpoint: string; flux_klein_9b_model: string; flux_klein_9b_api_key_set: boolean;
  gpt_image_endpoint: string; gpt_image_model: string; gpt_image_api_key_set: boolean;
  faceverse_endpoint: string; faceverse_model: string; faceverse_api_key_set: boolean;
  image_provider: string; unsplash_access_key_set: boolean; pixabay_api_key_set: boolean;
};
export type AdminPaymentChannel = { id: string; ready: boolean; reason: string };
export type AdminPaymentDocument = {
  settings: {
    alipay: { app_id_set: boolean; seller_id_set: boolean;
      private_key_set: boolean; public_key_set: boolean };
    wechat: { app_id_set: boolean; mch_id_set: boolean; merchant_serial_set: boolean;
      private_key_set: boolean; api_v3_key_set: boolean; platform_key_ids: string[] };
  };
  status: {
    notify_origin: string; channels: AdminPaymentChannel[];
    callbacks: { alipay: string; wechat: string };
  };
  checks?: { channel: string; ok: boolean; message: string }[];
};
/** Only the fields the operator is changing; an empty string clears a value. */
export type AdminPaymentUpdate = Partial<Record<
  'alipay_app_id' | 'alipay_seller_id' | 'alipay_private_key' | 'alipay_public_key'
  | 'wechat_app_id' | 'wechat_mch_id' | 'wechat_merchant_serial' | 'wechat_private_key'
  | 'wechat_api_v3_key' | 'wechat_platform_key_id' | 'wechat_platform_public_key'
  | 'wechat_platform_key_remove', string>>;
export type AdminAccount = {
  id: string; name: string; display_name: string; contact: string;
  role: 'customer' | 'merchant' | 'admin'; created: number;
  disabled: boolean; quota: number; garment_count: number;
};
export type AdminCharge = { count: number; amount_cents: number };
export type AdminLedgerRow = {
  id: string; user_id: string; account_name: string | null;
  delta_cents: number; balance_cents: number; kind: string;
  reference: string | null; created: number;
};
export type AdminUsage = {
  model_charges: Record<'reserved' | 'completed' | 'refunded', AdminCharge>;
  wallets: { count: number; total_balance_cents: number };
  recent_ledger: AdminLedgerRow[];
  garments: { total: number; draft?: number; published?: number };
  looks: { total: number; draft?: number; published?: number };
  orders: Record<'created' | 'submitting' | 'pending' | 'paid' | 'uncertain', AdminCharge>;
};
export type AdminOrder = {
  id: string; kind: string; provider: string; amount_cents: number; currency: string;
  state: string; created: number; updated: number; expires: number | null;
  paid_at: number | null; description: string; plan_id: string | null;
  user_id: string; account_name: string | null;
};
/** A generation job the server still holds; the customer's copy is the record. */
export type AdminJob = {
  id: string; owner_id: string; state: string; created: number; updated: number;
  steps: { name: string; status: string }[];
};
export type AdminTransientJob = {
  id: string; owner_id: string; state: string; created: number;
  model: string | null; provider: string | null;
};
export type AdminJobs = {
  jobs: AdminJob[]; tryons: AdminTransientJob[];
  face_refinements: AdminTransientJob[]; note: string;
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

/** Routes that need the signed-in account; everything else answers without it. */
const AUTH_ROUTES = new RegExp('^/api/(' + [
  'auth/', 'account/', 'merchant/', 'admin(?:/|$)', 'jobs(?:/|$)', 'tryons(?:/|$)',
  'assets(?:/|$)', 'face-photos', 'model-assets', 'face-refinements', 'outfits/recommend',
].join('|') + ')');

export function authHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = sessionToken.read();
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
  return headers;
}

export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = AUTH_ROUTES.test(url) ? authHeaders(init?.headers) : new Headers(init?.headers);
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
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (REPEATABLE.has(url)) {
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

// A retried request keeps its key so the wallet is charged at most once.
const REPEATABLE = new Set(['/api/jobs', '/api/tryons']);
const pendingCharges = new Map<string, string>();

/** The bytes of one server asset; only the signed-in owner may read them. */
export async function fetchBlob(url: string): Promise<Blob> {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) throw new ApiError(`读取服务器文件失败（${response.status}）`, response.status);
  return response.blob();
}

/** A local blob URL for one server asset, kept alive for this page. */
