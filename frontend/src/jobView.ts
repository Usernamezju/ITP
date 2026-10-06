import type { Job } from './api';
import type { LocalJob } from './transient';

export const stageLabels: Record<string, string> = {
  pose: '姿势编辑', geometry: '几何生成', topology: '智能拓扑', texture: 'PBR 纹理',
  rig: '自动绑骨', export: 'FBX 导出', face_refine: '脸部精修',
};
const stateLabels: Record<string, string> = {
  queued: '等待处理', running: '正在生成', awaiting_review: '等待确认姿势',
  succeeded: '生成完成', failed: '生成失败', rejected: '姿势图已放弃',
};

/** The badge text and colour class for one task, shared by workspace and history. */
export function jobState(job: Job | LocalJob): { label: string; className: string } {
  if (job.state === 'failed' && job.artifacts.length > 0) {
    return { label: '部分完成', className: 'partial' };
  }
  return { label: stateLabels[job.state] || job.state, className: job.state };
}

/** The stages a partly finished task did reach, for the failure explanation. */
export function completedStages(job: LocalJob): string {
  return [...new Set(job.artifacts.map((item) => stageLabels[item.stage] || item.stage))].join('、');
}
