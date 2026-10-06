/** Customer data belongs to this browser, partitioned by account identity. */
import { sessionToken } from './session';
import type { Asset } from './api';

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

export async function saveLocal<T>(id: string, value: T, blob?: Blob, namespace = dataNamespace()): Promise<T> {
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
  return value;
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
