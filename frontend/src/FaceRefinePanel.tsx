import { useEffect, useState } from 'react';
import { Download, LoaderCircle, ScanFace } from 'lucide-react';
import { api, fileUrl, post, type Asset, type FaceRefinement } from './api';
import './FaceRefinePanel.css';

export function FaceRefinePanel({ jobId, configured }: {
  jobId: string; configured: boolean;
}) {
  const [photo, setPhoto] = useState<Asset | null>(null);
  const [task, setTask] = useState<FaceRefinement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setPhoto(null); setTask(null); setError('');
    void api<FaceRefinement[]>(`/api/jobs/${jobId}/face-refinement`)
      .then((items) => { if (items.length) setTask(items[0]); })
      .catch((err) => setError((err as Error).message));
  }, [jobId]);
  useEffect(() => {
    if (!task || !['queued', 'submitting'].includes(task.state)) return;
    const timer = setInterval(() => {
      void api<FaceRefinement[]>(`/api/jobs/${jobId}/face-refinement`)
        .then((items) => { if (items.length) setTask(items[0]); })
        .catch((err) => setError((err as Error).message));
    }, 3000);
    return () => clearInterval(timer);
  }, [jobId, task?.id, task?.state]);

  async function upload(file?: File) {
    if (!file) return;
    setUploading(true); setError('');
    try {
      const form = new FormData(); form.append('file', file);
      setPhoto(await api<Asset>('/api/face-photos', { method: 'POST', body: form }));
    } catch (err) { setError((err as Error).message); }
    finally { setUploading(false); }
  }
  async function submit() {
    if (!photo || submitting) return;
    setSubmitting(true); setError('');
    try { setTask(await post<FaceRefinement>(`/api/jobs/${jobId}/face-refinement`, { face_photo: photo.id })); }
    catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }

  return <section className="face-refine-panel"><h3><ScanFace size={19} /> 脸部精细建模</h3>
    <p>3D 已完成。上传原始高清正面人物照片，进行人脸重建与无缝融合；原模型始终保留。</p>
    {!configured && <p className="face-refine-note">脸部精修服务暂不可用；不影响原模型预览与下载。</p>}
    <label className="face-refine-upload">{uploading ? <LoaderCircle size={17} className="spin" /> : <ScanFace size={17} />}
      {photo ? `${photo.width} × ${photo.height} 高清照片已上传（点击更换）` : '上传原始高清正面照片'}
      <input type="file" accept="image/png,image/jpeg,image/webp" disabled={uploading}
        onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
    </label>
    <button className="button" onClick={() => void submit()} disabled={!configured || !photo || uploading || submitting || task?.state === 'queued' || task?.state === 'submitting'}>
      {submitting ? <LoaderCircle size={16} className="spin" /> : <ScanFace size={16} />} 开始脸部精修
    </button>
    {task && <div className="face-refine-status" role="status">{task.state === 'ready' ? <>
      精修完成。<a href={`${fileUrl(task.result_asset!)}?download=true`}><Download size={15} /> 下载精修 GLB</a>
    </> : task.state === 'failed' ? `精修失败：${task.error || '请检查服务器日志'}；原模型未受影响。` : '正在等待远程 FaceVerse 服务器返回结果…'}</div>}
    {error && <p role="alert" className="settings-error">{error}</p>}
  </section>;
}
