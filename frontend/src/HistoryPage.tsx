import { ArrowRight, ChevronRight, FolderOpen } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useCustomer } from './customerState';
import { jobState } from './jobView';
import type { LocalJob } from './transient';

/** This browser's own task list; opening one returns to the workbench. */
export default function HistoryPage() {
  const { jobs, chooseJob, previewImage } = useCustomer();
  const navigate = useNavigate();
  const active = jobs.filter((item) => ['queued', 'running', 'awaiting_review'].includes(item.state)).length;

  function open(item: LocalJob) {
    chooseJob(item);
    navigate('/');
  }

  return <section className="history-page">
    <div className="section-heading"><h2>任务记录 <span>{jobs.length}</span></h2><small>{active} 个待处理任务</small></div>
    {!jobs.length ? <div className="history-empty"><FolderOpen size={42} strokeWidth={1} /><h3>第一件作品，从这里开始</h3><p>你的生成任务与中间产物会保存在本地。</p><button className="button" onClick={() => navigate('/')}>前往工作台 <ArrowRight size={16} /></button></div> :
      <div className="history-grid">{jobs.map((item) => <button className="history-card" key={item.id} onClick={() => open(item)}>
        <img src={previewImage(item.pose_asset || item.request.front)} alt={item.name} /><div><strong>{item.name}</strong><span className={`state ${jobState(item).className}`}>{jobState(item).label}</span><small>{new Date(item.created * 1000).toLocaleString('zh-CN')}</small></div><ChevronRight size={17} />
      </button>)}</div>}
  </section>;
}
