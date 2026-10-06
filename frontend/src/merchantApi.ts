/** Merchant console client: accounts, garment import and look composition. */

import { api } from './api';

const TOKEN_KEY = 'itp.merchant.token';

/**
 * The login token lives in localStorage so a reload keeps the console open.
 * This is a loopback-only single-user tool: the token grants nothing beyond
 * what the machine's own user can already do, and it is never sent anywhere
 * except this local backend.
 */
export const merchantToken = {
  read(): string {
    try {
      return localStorage.getItem(TOKEN_KEY) || '';
    } catch {
      return '';
    }
  },
  write(token: string): void {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable: the session simply will not survive a reload */
    }
  },
};

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
    name_max: number; short_text_max: number; description_max: number;
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
  created: number; quota: number; garment_count: number;
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

export type GarmentImage = { id: string; url: string; position: number; created: number };

export type GarmentMetrics = {
  category: string;
  name: string;
  sku?: string | null;
  brand?: string | null;
  price_cents?: number | null;
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
