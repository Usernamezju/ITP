/** Customer data belongs to this browser, partitioned by account identity. */
import { sessionToken } from './session';
import type { Asset } from './api';
import { syncRecord } from './cloudStorage';

type LocalRecord = { key: string; namespace: string; id: string; value: unknown; blob?: Blob };
const urls = new Map<string, string>();
const values = new Map<string, unknown>();
let loadedNamespace = '';
let database: Promise<IDBDatabase> | undefined;

export function dataNamespace(): string {
  const token = sessionToken.read();
  if (!token) return 'anonymous';
  try {
    // A UI namespace, never authentication. The API verifies every JWT itself.
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const subject = JSON.parse(atob(encoded)).sub;
    return typeof subject === 'string' && subject.length < 128 ? `account:${subject}` : 'invalid';
  } catch { return 'invalid'; }
}

function db(): Promise<IDBDatabase> {
  if (!database) database = new Promise((resolve, reject) => {
    const request = indexedDB.open('itp-customer-data', 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore('records', { keyPath: 'key' });
      store.createIndex('namespace', 'namespace');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { database = undefined; reject(new Error('浏览器本地存储不可用，请启用站点存储权限')); };
  });
  return database;
}

async function records(namespace: string): Promise<LocalRecord[]> {
  const database = await db();
  return new Promise((resolve, reject) => {
    const request = database.transaction('records').objectStore('records').index('namespace').getAll(namespace);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('读取浏览器本地数据失败'));
  });
}

export async function hydrateLocalData(namespace = dataNamespace()): Promise<void> {
  const saved = await records(namespace);
  for (const url of urls.values()) URL.revokeObjectURL(url);
  urls.clear(); values.clear(); loadedNamespace = namespace;
  for (const item of saved) {
    values.set(item.id, item.value);
    if (item.blob) urls.set(item.id, URL.createObjectURL(item.blob));
  }
}

export function localValue<T>(id: string, fallback: T): T {
  if (loadedNamespace !== dataNamespace()) return fallback;
  return (values.get(id) as T | undefined) ?? fallback;
}

export function localFileUrl(id: string): string | undefined {
  return loadedNamespace === dataNamespace() ? urls.get(id) : undefined;
}

/** One saved asset, carrying the blob URL this page can render right now. */
export function localAsset(id: string): Asset | undefined {
  const asset = localValue<Asset | undefined>(id, undefined);
  if (!asset) return undefined;
  // The stored url is a server route nothing may render; the blob is the copy.
  return { ...asset, url: localFileUrl(id) ?? '' };
}

export async function saveLocal<T>(id: string, value: T, blob?: Blob, namespace = dataNamespace(), sync = true): Promise<T> {
  const database = await db();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction('records', 'readwrite');
    transaction.objectStore('records').put({ key: `${namespace}/${id}`, namespace, id, value, blob });
    transaction.oncomplete = () => resolve();
    transaction.onerror = transaction.onabort = () => reject(new Error('本地保存失败或空间不足，请先下载结果，不要关闭页面'));
  });
  if (namespace === loadedNamespace && namespace === dataNamespace()) {
    values.set(id, value);
    if (blob) {
      const old = urls.get(id); if (old) URL.revokeObjectURL(old);
      urls.set(id, URL.createObjectURL(blob));
    }
  }
  if (sync) await syncRecord(id, value, blob, namespace);
  return value;
}

/** Existing browser data is uploaded only after the caller's explicit confirmation. */
export async function migrateLocalData(): Promise<number> {
  const namespace = dataNamespace();
  const saved = await records(namespace);
  for (const item of saved) {
    if (namespace !== dataNamespace()) throw new Error('账号已切换，请重新确认迁移');
    await syncRecord(item.id, item.value, item.blob, namespace);
  }
  return saved.length;
}

export async function localBlob(id: string, namespace = dataNamespace()): Promise<Blob | undefined> {
  const database = await db();
  return new Promise((resolve, reject) => {
    const request = database.transaction('records').objectStore('records').get(`${namespace}/${id}`);
    request.onsuccess = () => resolve(request.result?.blob);
    request.onerror = () => reject(new Error('读取本地图片/模型失败'));
  });
}

export async function localRecords<T>(prefix: string, namespace = dataNamespace()): Promise<T[]> {
  return (await records(namespace)).filter((item) => item.id.startsWith(prefix)).map((item) => item.value as T);
}

/**
 * Every asset this browser holds, in the order it was saved.
 *
 * An asset is stored under its own id, so a record whose value carries the same
 * id and a kind is one; task and profile records are keyed by a prefix instead
 * and are skipped.
 */
export async function localAssets(kind?: string, namespace = dataNamespace()): Promise<Asset[]> {
  return (await records(namespace))
    .filter((item) => {
      const value = item.value as Asset | undefined;
      if (!value || item.id !== value.id || typeof value.kind !== 'string') return false;
      return !kind || value.kind === kind;
    })
    .map((item) => ({ ...(item.value as Asset), url: localFileUrl(item.id) ?? '' }));
}

export async function clearLocalData(): Promise<void> {
  const namespace = dataNamespace(); const database = await db(); const saved = await records(namespace);
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction('records', 'readwrite');
    for (const item of saved) transaction.objectStore('records').delete(item.key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = transaction.onabort = () => reject(new Error('删除本地数据失败'));
  });
  await hydrateLocalData(namespace);
}

export async function importLocalModel(file: File): Promise<Asset> {
  if (!file.name.toLowerCase().endsWith('.glb') || file.size > 150 * 1024 * 1024) {
    throw new Error('请导入不超过 150 MiB 的 GLB 文件');
  }
  const id = crypto.randomUUID().replace(/-/g, '');
  // Stored byte for byte: the server re-validates it on the way back in.
  const asset: Asset = { id, kind: 'model', url: '', size: file.size, format: 'GLB', name: file.name };
  await saveLocal(id, asset, file);
  return { ...asset, url: localFileUrl(id)! };
}

export async function importLocalImage(file: File, kind = 'image', removeBackground = false): Promise<Asset> {
  if (file.size > 10 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    throw new Error('图片仅支持 PNG/JPEG/WebP，不能超过 10 MiB');
  }
  const bitmap = await createImageBitmap(file);
  try {
    const minimum = kind === 'face_photo' ? 512 : 64;
    if (Math.min(bitmap.width, bitmap.height) < minimum || bitmap.width * bitmap.height > 25_000_000) {
      throw new Error(`图片短边至少 ${minimum} 像素，总像素不超过 2500 万`);
    }
    // Canvas removes EXIF and preserves original face resolution. Geometry
    // downscaling/background removal happens only when submitting a task.
    const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0);
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error('图片转换失败')), 'image/png'));
    const id = crypto.randomUUID().replace(/-/g, '');
    const asset: Asset = { id, kind, url: '', size: blob.size, width: bitmap.width, height: bitmap.height,
      background_removed: false, remove_background: removeBackground };
    await saveLocal(id, asset, blob);
    return { ...asset, url: localFileUrl(id)! };
  } finally { bitmap.close(); }
}
