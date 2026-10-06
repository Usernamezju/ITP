import { useEffect, useState } from 'react';
import { Download, LoaderCircle, ScanFace } from 'lucide-react';
import { api, post, type Asset, type FaceRefinement } from './api';
import { importLocalImage, localFileUrl } from './localData';
import { acknowledge, localize, uploadLocal } from './transient';
import './FaceRefinePanel.css';

/**
 * Refine the face of a model this browser already holds.
 *
 * Both inputs are temporary: the model comes out of IndexedDB, the photo is
 * picked here and kept locally too.  The refined model is saved back into the
 * browser and the server's copies are dropped as soon as that succeeds.
 */
export function FaceRefinePanel({ model, name, configured }: {
  model: string; name: string; configured: boolean;
}) {
  const [photo, setPhoto] = useState<Asset | null>(null);
  const [task, setTask] = useState<FaceRefinement | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { setPhoto(null); setTask(null); setResult(null); setError(''); }, [model]);

  useEffect(() => {
    if (!task || !['queued', 'submitting'].includes(task.state)) return;
    const timer = setInterval(() => {
      void api<FaceRefinement>(`/api/face-refinements/${task.id}`).then(setTask)
        .catch((err) => setError((err as Error).message));
    }, 3000);
    return () => clearInterval(timer);
  }, [task?.id, task?.state]);

  useEffect(() => {
    if (!task || task.state !== 'ready' || !task.result_asset || result) return;
    let active = true;
    // Keep the refined model here, then let the server drop all three files.
    void localize(task.result_asset, `${name}-脸部精修.glb`)
      .then(async (saved) => {
        if (!active) return;
        setResult(saved);
        await acknowledge('face-refinements', task.id);
      })
      .catch((err) => { if (active) setError((err as Error).message); });
    return () => { active = false; };
  }, [task?.id, task?.state, task?.result_asset, result, name]);

  async function upload(file?: File) {
    if (!file) return;
    setUploading(true); setError('');
    try { setPhoto(await importLocalImage(file, 'face_photo')); }
    catch (err) { setError((err as Error).message); }
    finally { setUploading(false); }
  }

  async function submit() {
    if (!photo || submitting) return;
    setSubmitting(true); setError('');
    try {
      const [uploadedModel, uploadedPhoto] = await Promise.all([
        uploadLocal(model), uploadLocal(photo.id),
      ]);
      setTask(await post<FaceRefinement>('/api/face-refinements',
        { mesh: uploadedModel.id, face_photo: uploadedPhoto.id }));
    } catch (err) { setError((err as Error).message); }
    finally { setSubmitting(false); }
  }

  const running = task?.state === 'queued' || task?.state === 'submitting';
  const href = result ? localFileUrl(result) : undefined;

  return <section className="face-refine-panel"><h3><ScanFace size={19} /> 脸部精细建模</h3>
    <p>3D 已完成。选择原始高清正面人物照片，进行人脸重建与无缝融合；原模型始终保留在本机。</p>
    {!configured && <p className="face-refine-note">脸部精修服务暂不可用；不影响原模型预览与下载。</p>}
    <label className="face-refine-upload">{uploading ? <LoaderCircle size={17} className="spin" /> : <ScanFace size={17} />}
      {photo ? `${photo.width} × ${photo.height} 高清照片已就绪（点击更换）` : '选择原始高清正面照片'}
      <input type="file" accept="image/png,image/jpeg,image/webp" disabled={uploading}
        onChange={(event) => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
    </label>
    <button className="button" onClick={() => void submit()} disabled={!configured || !photo || uploading || submitting || running}>
      {submitting ? <LoaderCircle size={16} className="spin" /> : <ScanFace size={16} />} 开始脸部精修
    </button>
    {task && <div className="face-refine-status" role="status">{task.state === 'ready' ? (href
      ? <>精修完成，已保存到本机。<a href={href} download={`${name}-脸部精修.glb`}><Download size={15} /> 下载精修 GLB</a></>
      : '精修完成，正在保存到本机…')
      : task.state === 'failed' ? `精修失败：${task.error || '请检查服务器日志'}；原模型未受影响。`
      : '正在等待远程 FaceVerse 服务器返回结果…'}</div>}
    {error && <p role="alert" className="settings-error">{error}</p>}
  </section>;
}
