import { useState } from 'react';
import { api, ApiError, type OutfitItem } from './api';

export function safePurchaseUrl(value?: string | null): string | null {
  if (!value || !/^https?:\/\//i.test(value) || /[\s\\]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && url.hostname && !url.username && !url.password
      ? url.href : null;
  } catch { return null; }
}

/** Every consumer purchase entry records first; failure can still open a safe link. */
export function ProductPurchase({ item, onPreference }: {
  item: OutfitItem; onPreference?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [manual, setManual] = useState<string | null>(null);
  const fallback = safePurchaseUrl(item.purchase_url);
  if (!item.garment_id || !fallback) return null;

  async function visit() {
    if (busy) return;
    // Reserve a tab during the user gesture, before awaiting the click request.
    const tab = window.open('about:blank', '_blank');
    if (tab) {
      tab.opener = null;
      const policy = tab.document.createElement('meta');
      policy.name = 'referrer'; policy.content = 'no-referrer';
      tab.document.head.append(policy);
      tab.document.title = '正在打开商品…';
    }
    setBusy(true); setNotice(''); setManual(null); onPreference?.();
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 4000);
    let destination = fallback;
    try {
      const recorded = await api<{ purchase_url: string }>(`/api/garments/${item.garment_id}/clicks`,
        { method: 'POST', signal: controller.signal });
      destination = safePurchaseUrl(recorded.purchase_url);
      if (!destination) throw new Error('无效商品链接');
    } catch (error) {
      console.warn('商品点击记录未完成');
      if (error instanceof ApiError && error.status === 404) {
        destination = null; setNotice('该商品已下架或移除购买链接，请刷新推荐。');
      } else {
        setNotice('点击统计暂未完成，仍可查看商品。');
      }
    } finally { window.clearTimeout(timer); setBusy(false); }
    if (!destination) { tab?.close(); return; }
    if (tab && !tab.closed) {
      // noreferrer also applies to the navigation after the asynchronous request.
      const link = tab.document.createElement('a');
      link.href = destination; link.rel = 'noopener noreferrer'; link.referrerPolicy = 'no-referrer';
      tab.document.body.append(link); link.click();
    } else {
      setManual(destination); setNotice('浏览器拦截了新标签页，请点击下方链接继续。');
    }
  }
  return <div className="outfit-buy">
    <button type="button" className="button" disabled={busy} onClick={() => void visit()}
      aria-label={`查看商品：${item.name}`}>{busy ? '正在打开…' : '查看商品'}</button>
    {notice && <small className="outfit-click-notice" role="status">{notice}</small>}
    {manual && <a href={manual} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
      在新标签页查看商品</a>}
  </div>;
}
