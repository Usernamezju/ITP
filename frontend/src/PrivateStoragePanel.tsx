import { useEffect, useState } from 'react';
import { api } from './api';
import { configureCloudStorage, restoreCloudData, type StorageManifest } from './cloudStorage';
import { clearLocalData, migrateLocalData } from './localData';

export function PrivateStoragePanel() {
  const [manifest, setManifest] = useState<StorageManifest>();
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false); const [confirm, setConfirm] = useState<'migrate' | 'delete' | null>(null);
  const refresh = async () => { const next = await api<StorageManifest>('/api/account/storage'); setManifest(next); configureCloudStorage(true); };
  useEffect(() => { void refresh().catch((err: Error) => setError(err.message)); }, []);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); await refresh(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); setConfirm(null); }
  }
  return <section className="account-card" aria-label="账户私有空间">
    <h2>账户私有空间</h2><p>照片、测量数据和任务结果仅当前账号可访问，登录后可在其他设备恢复。</p>
    {manifest && <p>已用 {(manifest.used_bytes / 1024 / 1024).toFixed(1)} / {manifest.quota_bytes / 1024 / 1024} MiB · {manifest.items.length} 条记录 · 最长保留 {manifest.retention_days} 天</p>}
    <p>新保存的数据自动同步。旧浏览器数据需确认后迁移；请先登录其原所属账号，匿名及其他账号的数据不会被迁移。</p>
    {error && <p role="alert">私有空间操作未完成：{error}</p>}{notice && <p role="status">{notice}</p>}
    <div className="account-profile-actions"><button className="button" disabled={busy || !manifest} onClick={() => setConfirm('migrate')}>迁移 / 重试同步本机数据</button>
      <button className="button account-outline" disabled={busy || !manifest} onClick={() => void run(async () => { await restoreCloudData(); setNotice('云端数据已恢复，请刷新工作台查看'); })}>恢复云端数据</button>
      <button className="text-button" disabled={busy || !manifest} onClick={() => setConfirm('delete')}>删除私有数据</button></div>
    {confirm && <div role="dialog" aria-modal="true" aria-label={confirm === 'migrate' ? '确认迁移本机数据' : '确认删除私有数据'}>
      <p>{confirm === 'migrate' ? '确认将当前账号在这个浏览器保存的数据上传到该账号的私有空间？' : '确认删除该账号的全部云端数据及当前浏览器缓存？请先下载需要保留的文件。已创建的备份将在保留期限后清除。'}</p>
      <button className="button" disabled={busy} onClick={() => void run(async () => {
        if (confirm === 'migrate') { const count = await migrateLocalData(); setNotice(`已同步 ${count} 条记录`); }
        else { await api('/api/account/storage', { method: 'DELETE' }); await clearLocalData(); setNotice('私有数据已删除'); }
      })}>{busy ? '正在处理…' : '确认'}</button><button className="text-button" disabled={busy} onClick={() => setConfirm(null)}>取消</button>
    </div>}
  </section>;
}
