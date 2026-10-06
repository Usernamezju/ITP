/** Local category counts only: no URLs, product IDs, timestamps or event logs. */
import type { Outfit } from './api';
import { dataNamespace, localValue, saveLocal } from './localData';

export type HistoryPreferences = { styles?: Record<string, number>; categories?: Record<string, number> };
const KEY = 'recommendation-preferences-v1';
const FAMILIES = {
  styles: ['通勤', '休闲', '街头', '运动', '度假', '复古', '极简', '学院'],
  categories: ['上装', '下装', '外套', '鞋履', '配饰'],
};
const EVENT_WEIGHTS = { view: 1, click: 3 };
const MAX_COUNT = 20;
let pending = Promise.resolve();

export function historyPreferences(): HistoryPreferences {
  const saved = localValue<HistoryPreferences>(KEY, {});
  const result: HistoryPreferences = {};
  for (const family of ['styles', 'categories'] as const) {
    const counts: Record<string, number> = {};
    for (const key of FAMILIES[family]) {
      const count = saved?.[family]?.[key];
      if (typeof count === 'number' && Number.isFinite(count) && count > 0) {
        counts[key] = Math.min(MAX_COUNT, Math.floor(count));
      }
    }
    if (Object.keys(counts).length) result[family] = counts;
  }
  return result;
}

export function rememberPreference(outfit: Outfit, event: keyof typeof EVENT_WEIGHTS): void {
  const namespace = dataNamespace();
  pending = pending.then(async () => {
    if (namespace !== dataNamespace()) return;
    const preferences = historyPreferences();
    const values = { styles: [outfit.style], categories: [...new Set(outfit.items.map((item) => item.category))] };
    for (const family of ['styles', 'categories'] as const) {
      const counts = { ...preferences[family] };
      for (const value of values[family]) {
        if (FAMILIES[family].includes(value)) counts[value] = (counts[value] || 0) + EVENT_WEIGHTS[event];
      }
      if (Object.values(counts).some((count) => count > MAX_COUNT)) {
        for (const key of Object.keys(counts)) counts[key] = Math.max(1, Math.ceil(counts[key] / 2));
      }
      if (Object.keys(counts).length) preferences[family] = counts;
    }
    await saveLocal(KEY, preferences, undefined, namespace);
  }).catch(() => { console.warn('浏览器推荐偏好保存失败，继续使用通用推荐'); });
}

export async function clearPreferences(): Promise<void> {
  const namespace = dataNamespace();
  await pending;
  await saveLocal(KEY, {}, undefined, namespace);
}
