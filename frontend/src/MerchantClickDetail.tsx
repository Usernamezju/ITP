import { ArrowLeft, MousePointerClick, Package } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { ApiError } from './api';
import {
  clickRanges, fetchAnalytics, type ClickRange, type MerchantAnalytics as Analytics,
} from './merchantApi';

/** Products per page of the ranking; the analytics route caps `limit` at 100. */
const PAGE = 20;

/** The period in the URL, or undefined for an address that is not a ranking. */
function rangeOf(value: string | undefined): ClickRange | undefined {
  return clickRanges.find((item) => item.id === value)?.id;
}

/**
 * `/merchant/analytics/{今日|本月|累计}`: one product ranking per period, with
 * its product photo, name and click count.  The console's three data cards open
 * this page; the same cards switch the period from here.
 */
export default function MerchantClickDetailPage() {
  const { range } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState('');
  /** A 401/403 means the visitor is not this shop's signed-in merchant. */
  const [denied, setDenied] = useState(false);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const period = rangeOf(range);

  useEffect(() => { setOffset(0); }, [range]);
  useEffect(() => {
    if (!period) return;
    let alive = true;
    setLoading(true); setError('');
    void fetchAnalytics(offset, period).then((result) => {
      if (!alive) return;
      setData(result);
      // A shortened list (a deleted product, say) must not leave the reader on
      // a page beyond the end.
      if (!result.items.length && result.total > 0 && offset > 0) {
        setOffset(Math.max(0, Math.floor((result.total - 1) / PAGE) * PAGE));
      }
    }).catch((err: Error) => {
      if (!alive) return;
      setData(null);
      setError(err.message);
      setDenied(err instanceof ApiError && (err.status === 401 || err.status === 403));
    }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [period, offset, refresh]);

  if (!period) return <Navigate to="/merchant/analytics/total" replace />;
  const label = clickRanges.find((item) => item.id === period)!;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const page = Math.floor(offset / PAGE) + 1;

  return <section className="merchant-page" aria-label={`${label.label}商品排行`}>
    <div className="merchant-bar merchant-detail-bar">
      <Link className="button small" to="/merchant"><ArrowLeft size={14} /> 返回数据概览</Link>
      <div className="merchant-identity">
        <MousePointerClick size={18} />
        <div><strong>{label.label} · 商品排行</strong>
          <small>{label.hint}，按点击量从高到低排序</small></div>
      </div>
      <button className="text-button" type="button" disabled={loading}
        onClick={() => setRefresh((value) => value + 1)}>刷新点击数据</button>
    </div>

    <section className="merchant-analytics" aria-busy={loading}>
      {error ? (denied
        ? <div className="merchant-empty"><Package size={30} strokeWidth={1.1} />
          <h3>需要商家身份</h3><p>{error}</p>
          <Link className="button" to="/merchant">前往商家后台登录</Link></div>
        : <p role="alert">点击数据读取失败：{error}，请重试。</p>)
        : !data ? <p role="status">{loading ? '正在读取点击数据…' : '暂时无法读取统计'}</p> : <>
          <div className="merchant-kpis">{clickRanges.map((option) =>
            <button type="button" key={option.id}
              className={`merchant-kpi ${option.id === period ? 'active' : ''}`}
              aria-current={option.id === period ? 'page' : undefined}
              aria-label={`${option.label} ${data.summary[option.id].toLocaleString('zh-CN')}`}
              disabled={loading}
              onClick={() => navigate(`/merchant/analytics/${option.id}`)}>
              <span>{option.label}</span>
              <strong>{data.summary[option.id].toLocaleString('zh-CN')}</strong>
              <small>{option.hint}</small>
            </button>)}</div>
          <small>按北京时间统计购买入口点击次数，不代表成交或独立访客。</small>

          {!data.items.length
            ? <div className="merchant-empty">
              <Package size={30} strokeWidth={1.1} />
              <h3>{data.total ? '这一页没有商品' : '还没有商品'}</h3>
              <p>{data.total ? '请返回上一页查看排行。'
                : '导入商品并设置购买链接后，顾客的点击会立即出现在这里。'}</p>
              {!data.total && <button className="button" type="button"
                onClick={() => navigate('/merchant')}>去新建商品</button>}
            </div>
            : <>
              <ul className="merchant-click-list">
                {data.items.map((item, index) => <li key={item.id}>
                  <span className="merchant-click-rank">{offset + index + 1}</span>
                  <span className="merchant-good-thumb">
                    {item.images[0]
                      ? <img src={item.images[0].url} alt={`${item.metrics.name} 商品图`} />
                      : <i style={{ background: item.metrics.attributes?.color || '#cccccc' }} />}
                  </span>
                  <div className="merchant-good-text">
                    <strong>{item.metrics.name}</strong>
                    <small>{item.metrics.category} · {item.status === 'published' ? '已发布' : '草稿'}
                      {item.metrics.purchase_url ? '' : ' · 未设置购买链接'}</small>
                  </div>
                  <div className="merchant-click-count">
                    <strong>{item.clicks[period].toLocaleString('zh-CN')}</strong>
                    <small>{label.label}</small>
                  </div>
                  <div className="merchant-click-other">
                    <span>今日 {item.clicks.today.toLocaleString('zh-CN')}</span>
                    <span>本月 {item.clicks.month.toLocaleString('zh-CN')}</span>
                    <span>累计 {item.clicks.total.toLocaleString('zh-CN')}</span>
                  </div>
                </li>)}
              </ul>
              {!data.summary[period] && <p className="merchant-hint">
                该维度下还没有点击记录：上架商品并填写购买链接后，顾客点「查看商品」才会计数。</p>}
            </>}

          <div className="merchant-pagination">
            <button type="button" className="text-button" disabled={loading || offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - PAGE))}>上一页</button>
            <span>第 {page} / {pages} 页 · 共 {data.total} 件商品</span>
            <button type="button" className="text-button"
              disabled={loading || offset + PAGE >= data.total}
              onClick={() => setOffset((value) => value + PAGE)}>下一页</button>
          </div>
        </>}
    </section>
  </section>;
}
