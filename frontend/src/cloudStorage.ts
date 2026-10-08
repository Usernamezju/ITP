import { api, fetchBlob, type Asset, type Job, type TryOnJob } from './api';
import { dataNamespace, hydrateLocalData, saveLocal } from './localData';
import { sessionToken } from './session';
import { saveJob, saveTryOn } from './transient';

export type StorageItem = { category: string; id: string; has_blob: boolean; updated: number; value: unknown };
export type StorageManifest = { used_bytes: number; quota_bytes: number; retention_days: number; items: StorageItem[] };
let enabled = false;
export const configureCloudStorage = (value: boolean) => { enabled = value; };
export const cloudStorageEnabled = () => enabled && !!sessionToken.read();

export async function syncRecord(id: string, value: unknown, blob: Blob | undefined, namespace: string) {
  if (!cloudStorageEnabled() || namespace !== dataNamespace()) return;
  const body = new FormData();
  body.set('value', JSON.stringify(value));
  if (blob) body.set('file', blob, (value as Asset)?.kind === 'model' ? 'asset.glb' : 'asset.png');
  try { await api(`/api/account/storage/records/${encodeURIComponent(id)}`, { method: 'POST', body }); }
  catch (err) { throw new Error(`已保留浏览器缓存，云端同步失败：${(err as Error).message}。请在账户私有空间中重试同步。`); }
}

export async function restoreCloudData(namespace = dataNamespace()) {
  if (!cloudStorageEnabled()) return;
  const manifest = await api<StorageManifest>('/api/account/storage');
  for (const item of manifest.items.filter((i) => ['record', 'asset'].includes(i.category))) {
    if (namespace !== dataNamespace()) return;
    const blob = item.has_blob ? await fetchBlob(`/api/account/storage/${item.category}/${encodeURIComponent(item.id)}/file`) : undefined;
    await saveLocal(item.id, item.value, blob, namespace, false);
  }
  for (const item of manifest.items) {
    if (namespace !== dataNamespace()) return;
    if (item.category === 'job') await saveJob(item.value as Job);
    if (item.category === 'tryon') await saveTryOn(item.value as TryOnJob);
  }
  if (namespace === dataNamespace()) await hydrateLocalData(namespace);
}
