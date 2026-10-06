/**
 * The bridge between this browser's own asset store and the server's temporary
 * workspace.
 *
 * Customer photos and models live in IndexedDB.  The server only ever sees a
 * copy made for one run and deletes it as soon as this browser has saved the
 * result — so every id this module returns is a *local* id, and every id it
 * sends is a temporary server id the server does not keep.
 */
import { api, fetchBlob, type Asset, type Job, type TryOnJob } from './api';
import { localAsset, localBlob, localValue, saveLocal } from './localData';

/** States the server has finished with: its files can be released. */
export const TERMINAL = new Set(['succeeded', 'failed', 'rejected', 'ready']);

/** Every temporary server copy this page made or received, by local original. */
const localIds = new Map<string, string>();

/** The local asset a temporary server asset came from, or was saved as. */
export function localIdOf(serverId?: string | null): string | undefined {
  return serverId ? localIds.get(serverId) : undefined;
}

function remember(serverId: string, localId: string): string {
  localIds.set(serverId, localId);
  return localId;
}

/** Hand the server one temporary copy of a locally stored asset. */
export async function uploadLocal(localId: string): Promise<Asset> {
  const asset = localAsset(localId);
  const blob = await localBlob(localId);
  if (!asset || !blob) throw new Error('本地文件不存在，请重新上传');
  const body = new FormData();
  const suffix = asset.kind === 'model' ? 'glb' : 'png';
  body.append('file', new File([blob], `${localId}.${suffix}`, { type: blob.type }),
    `${localId}.${suffix}`);
  const path = asset.kind === 'model' ? '/api/model-assets'
    : asset.kind === 'face_photo' ? '/api/face-photos' : '/api/assets';
  const query = asset.kind === 'image' && asset.remove_background ? '?remove_background=true' : '';
  const uploaded = await api<Asset>(`${path}${query}`, { method: 'POST', body });
  remember(uploaded.id, localId);
  return uploaded;
}

/** Upload several local assets in order, keeping every returned server id. */
export async function uploadEach(ids: string[]): Promise<Asset[]> {
  const uploaded: Asset[] = [];
  for (const id of ids) uploaded.push(await uploadLocal(id));
  return uploaded;
}

/** Save one server result in this browser and return its new local id. */
export async function localize(serverId: string, name?: string): Promise<string> {
  const cached = localIds.get(serverId);
  if (cached && localAsset(cached)) return cached;
  const asset = await api<Asset>(`/api/assets/${serverId}`);
  const blob = await fetchBlob(`/api/assets/${serverId}/file`);
  const id = crypto.randomUUID().replace(/-/g, '');
  await saveLocal(id, { ...asset, url: '', name } as Asset, blob);
  return remember(serverId, id);
}

/** Tell the server this browser has the results; it drops its own copies. */
export async function acknowledge(
  scope: 'jobs' | 'tryons' | 'face-refinements', id: string,
): Promise<void> {
  await api<void>(`/api/${scope}/${id}/acknowledge`, { method: 'POST' });
}

/** What this browser remembers about one modelling task after a reload. */
export type LocalJob = {
  id: string; name: string; state: string; created: number; error: string | null;
  request: Omit<Job['request'], 'front'> & { front: string };
  models?: Job['models'];
  pose_asset: string | null;
  steps: Job['steps'];
  artifacts: { id: string; stage: string; format: string }[];
  /** False while the server is still working on this task. */
  saved: boolean;
};

export const jobRecord = (jobId: string) => `job:${jobId}`;

/**
 * Keep every artifact and the pose reference of one task in this browser.
 *
 * Idempotent, so polling may call it on every tick: a file already saved is
 * handed back from the cache instead of being downloaded again.
 */
export async function saveJob(job: Job): Promise<LocalJob> {
  const previous = localValue<LocalJob | undefined>(jobRecord(job.id), undefined);
  const front = localIdOf(job.request.front) ?? previous?.request.front ?? '';
  const reference
    = localIdOf(job.request.pose_reference) ?? previous?.request.pose_reference ?? null;
  const artifacts: LocalJob['artifacts'] = [];
  for (const artifact of job.artifacts) {
    artifacts.push({ id: await localize(artifact.asset_id), stage: artifact.stage,
      format: artifact.format });
  }
  const pose = job.pose_asset ? await localize(job.pose_asset) : null;
  const saved: LocalJob = {
    ...job, request: { ...job.request, front, pose_reference: reference },
    artifacts, pose_asset: pose, saved: true,
  };
  await saveLocal(jobRecord(job.id), saved);
  return saved;
}

/** What this browser remembers about one try-on task after a reload. */
export type LocalTryOn = {
  id: string; name: string; state: TryOnJob['state']; model: string; provider: TryOnJob['provider'];
  active_view: string | null; error: string | null; created?: number;
  results: Record<string, string>;
  saved: boolean;
};

export const tryOnRecord = (id: string) => `tryon:${id}`;

/** Keep every generated view of one try-on in this browser. Idempotent. */
export async function saveTryOn(job: TryOnJob): Promise<LocalTryOn> {
  const results: Record<string, string> = {};
  for (const [view, assetId] of Object.entries(job.results || {})) {
    results[view] = await localize(assetId);
  }
  const saved: LocalTryOn = { ...job, results, saved: true };
  await saveLocal(tryOnRecord(job.id), saved);
  return saved;
}
