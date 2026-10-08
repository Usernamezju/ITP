/** Merchant console client: accounts, garment import and look composition. */

import { api, post } from './api';
import { sessionToken } from './session';

// Backward-compatible name; there is only one credential store and JWT scheme.
export const merchantToken = sessionToken;

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = merchantToken.read();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

// --- reference data ----------------------------------------------------------

export type MeasurementOption = { key: string; label: string; max: number };
export type RangeOption = { key: string; label: string; min: number; max: number };

export type GarmentOptions = {
  categories: string[];
  styles: string[];
  seasons: string[];
  silhouettes: string[];
  stretches: string[];
  length_types: string[];
  statuses: string[];
  measurements: MeasurementOption[];
  fit_ranges: RangeOption[];
  body_profile: RangeOption[];
  limits: {
    purchase_url_max?: number; name_max: number; short_text_max: number; description_max: number;
    tips_max: number; tip_max: number; price_max_cents: number;
    weight_gsm_min: number; weight_gsm_max: number; image_max_mb: number;
    images_max: number; palette_max: number; look_items_max: number;
    look_name_max: number; look_story_max: number;
  };
};

export function fetchGarmentOptions(): Promise<GarmentOptions> {
  return api<GarmentOptions>('/api/garment-options');
}

// --- accounts ----------------------------------------------------------------

export type MerchantProfile = {
  merchant_id: string; name: string; display_name: string; contact: string;
  created: number; quota: number | null; garment_count: number; avatar_key?: string | null;
  upload_usage?: { used: number; limit: number | null; remaining: number | null; starts: number; ends: number; unlimited?: boolean };
  ai_usage?: { used: number; limit: number | null; remaining: number | null };
};

export type RegisterFields = {
  name: string; display_name: string; contact: string; password: string;
};

export function registerMerchant(fields: RegisterFields) {
  return api<{ merchant_id: string; name: string; display_name: string }>(
    '/api/merchant/register',
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields) },
  );
}

export function loginMerchant(name: string, password: string): Promise<string> {
  return api<{ access_token: string; token_type: string; expires_in: number }>(
    '/api/merchant/login',
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, password }) },
  ).then((data) => data.access_token);
}

export function fetchProfile(): Promise<MerchantProfile> {
  return api<MerchantProfile>('/api/merchant/me', { headers: authHeaders() });
}

export type ClickCounts = { today: number; month: number; total: number };
/** The three periods a merchant can rank products by. */
export type ClickRange = keyof ClickCounts;
export const clickRanges: { id: ClickRange; label: string; hint: string }[] = [
  { id: 'today', label: '今日点击', hint: '按北京时间当天统计' },
  { id: 'month', label: '本月点击', hint: '按北京时间当月统计' },
  { id: 'total', label: '累计点击', hint: '全部历史点击' },
];
export type MerchantAnalytics = {
  summary: ClickCounts; timezone: string; total: number;
  /** Echo of the ranking period the server applied, or null for the default list. */
  rank?: ClickRange | null;
  items: (MerchantGarment & { clicks: ClickCounts })[];
  trend: { date: string; clicks: number }[];
};

/** Ordered by total clicks unless a period is given to rank by. */
export function fetchAnalytics(offset = 0, rank?: ClickRange): Promise<MerchantAnalytics> {
  const query = new URLSearchParams({ limit: '20', offset: String(offset) });
  if (rank) query.set('rank', rank);
  return api<MerchantAnalytics>(`/api/merchant/analytics?${query}`, { headers: authHeaders() });
}

/**
 * Changing the password revokes every token issued before it, including the one
 * this request used, so the caller must sign in again afterwards.
 */
export function changePassword(currentPassword: string, newPassword: string) {
  return api<{ changed: boolean; tokens_revoked: boolean }>('/api/merchant/password',
    { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
}

// --- garments ----------------------------------------------------------------

// `available` is the server's word on whether the file behind the row is still
// there.  A picture that is not available is still listed so the shop can see
// and replace it, but cards skip it rather than show a broken frame.
export type GarmentImage = {
  id: string; url: string; position: number; created: number; available?: boolean;
};

export type GarmentMetrics = {
  category: string;
  name: string;
  sku?: string | null;
  brand?: string | null;
  price_cents?: number | null;
  purchase_url?: string | null;
  measurements: Record<string, number | null>;
  fit_ranges: Record<string, [number, number] | null>;
  attributes: {
    silhouette?: string | null; stretch?: string | null; weight_gsm?: number | null;
    color?: string | null; length_type?: string | null;
  };
  style?: string | null;
  season?: string | null;
  occasion?: string | null;
  description?: string | null;
  tips?: string[] | null;
  status: 'draft' | 'published';
};

export type MerchantGarment = {
  id: string; merchant_id: string; status: string; created: number; updated: number;
  metrics: GarmentMetrics; images: GarmentImage[];
};

export type MerchantLook = {
  id: string; merchant_id: string; name: string; story: string | null;
  style: string; season: string; occasion: string; palette: string[] | null;
  status: string; created: number; items: MerchantGarment[];
};

export type LookDraft = {
  name: string; story?: string | null; style: string; season: string;
  occasion: string; palette?: string[] | null; status: 'draft' | 'published';
  items: string[];
};

export function listGarments(status?: string): Promise<{ total: number; items: MerchantGarment[] }> {
  const query = status ? `?limit=100&status=${encodeURIComponent(status)}` : '?limit=100';
  return api<{ total: number; items: MerchantGarment[] }>(
    `/api/merchant/garments${query}`, { headers: authHeaders() },
  );
}

export function createGarment(metrics: GarmentMetrics, files: File[] = []) {
  const form = new FormData();
  form.append('payload', JSON.stringify(metrics));
  files.forEach((file) => form.append('images', file, file.name));
  return api<MerchantGarment>('/api/merchant/garments',
    { method: 'POST', headers: authHeaders(), body: form });
}

export function updateGarment(id: string, patch: Partial<GarmentMetrics>) {
  return api<MerchantGarment>(`/api/merchant/garments/${id}`,
    { method: 'PATCH', headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(patch) });
}

export function deleteGarment(id: string): Promise<void> {
  return api<void>(`/api/merchant/garments/${id}`,
    { method: 'DELETE', headers: authHeaders() });
}

export function addGarmentImages(id: string, files: File[]) {
  const form = new FormData();
  files.forEach((file) => form.append('images', file, file.name));
  return api<MerchantGarment>(`/api/merchant/garments/${id}/images`,
    { method: 'POST', headers: authHeaders(), body: form });
}

export function deleteGarmentImage(garmentId: string, imageId: string): Promise<void> {
  return api<void>(`/api/merchant/garments/${garmentId}/images/${imageId}`,
    { method: 'DELETE', headers: authHeaders() });
}

// --- looks -------------------------------------------------------------------

export function listLooks(): Promise<{ total: number; items: MerchantLook[] }> {
  return api<{ total: number; items: MerchantLook[] }>(
    '/api/merchant/looks?limit=100', { headers: authHeaders() },
  );
}

export function createLook(look: LookDraft) {
  return api<MerchantLook>('/api/merchant/looks',
    { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(look) });
}

export function updateLook(id: string, patch: Partial<LookDraft>) {
  return api<MerchantLook>(`/api/merchant/looks/${id}`,
    { method: 'PATCH', headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(patch) });
}

export function deleteLook(id: string): Promise<void> {
  return api<void>(`/api/merchant/looks/${id}`, { method: 'DELETE', headers: authHeaders() });
}

export type LinkImportFields = {
  name: string; category: string; color: string | null; color_name: string | null;
  style: string | null; season: string | null; occasion: string | null;
  silhouette: string | null; stretch: string | null; length_type: string | null;
  description: string | null; tags: string[]; uncertain: string[]; confidence: number | null;
};
export type LinkImport = {
  fields: LinkImportFields;
  image: { data_url: string; width: number; height: number; source_url: string };
  model: string;
};

/** Ask the server to read a shop link and describe the product on it. */
export function importProductLink(url: string): Promise<LinkImport> {
  return post<LinkImport>('/api/merchant/import-link', { url });
}

/** Turn the server's preview back into a file the upload flow already accepts. */
export async function previewFile(dataUrl: string, name: string): Promise<File> {
  const blob = await (await fetch(dataUrl)).blob();
  return new File([blob], name, { type: blob.type || 'image/jpeg' });
}
