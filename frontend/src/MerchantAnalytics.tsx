import { ChevronRight } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { clickRanges, fetchAnalytics, type MerchantAnalytics as Analytics } from './merchantApi';

export function MerchantAnalytics({ revision }: { revision: string }) {
  const navigate = useNavigate();
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError('');
    void fetchAnalytics(offset).then((result) => { if (alive) setData(result); })
      .catch((err: Error) => { if (alive) setError(err.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [revision, offset, refresh]);
  return <section className="merchant-analytics" aria-label="商品点击数据概览" aria-busy={loading}>
    <div className="section-heading"><h2>数据概览</h2>
      <button type="button" className="text-button" disabled={loading}
        onClick={() => setRefresh((value) => value + 1)}>刷新点击数据</button></div>
    {error && <p role="alert">点击数据读取失败：{error}，请重试。</p>}
    {!data ? <p role="status">{loading ? '正在读取点击数据…' : '暂时无法读取统计'}</p> : <>
      <div className="merchant-kpis">{clickRanges.map((range) =>
        <button type="button" className="merchant-kpi" key={range.id}
          aria-label={`${range.label} ${data.summary[range.id].toLocaleString('zh-CN')}，查看该维度的商品点击排行`}
          onClick={() => navigate(`/merchant/analytics/${range.id}`)}>
          <span>{range.label}</span>
          <strong>{data.summary[range.id].toLocaleString('zh-CN')}</strong>
          <small>{range.hint} <ChevronRight size={12} aria-hidden="true" /></small>
        </button>)}</div>
      <small>按北京时间统计购买入口点击次数，不代表成交或独立访客；点击任一指标查看该维度的商品排行。</small>
      <details><summary>查看商品点击明细与近 14 天趋势</summary>
        <div className="merchant-analytics-table"><table><caption>商品点击明细</caption>
          <thead><tr><th>商品</th><th>今日</th><th>本月</th><th>累计</th><th>购买链接</th></tr></thead>
          <tbody>{data.items.map((item) => <tr key={item.id}>
            <td>{item.images[0] && <img width="48" height="48" src={item.images[0].url} alt="" />}
              {item.metrics.name}</td><td>{item.clicks.today}</td><td>{item.clicks.month}</td><td>{item.clicks.total}</td>
            <td>{item.metrics.purchase_url ? <a href={item.metrics.purchase_url} target="_blank"
              rel="noopener noreferrer">查看购买链接</a> : '未设置'}</td>
          </tr>)}</tbody></table></div>
        {!data.items.length && <p>暂无商品点击明细</p>}
        <div className="merchant-pagination">
          <button type="button" className="text-button" disabled={loading || offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - 20))}>上一页</button>
          <span>共 {data.total} 件商品</span>
          <button type="button" className="text-button" disabled={loading || offset + 20 >= data.total}
            onClick={() => setOffset((value) => value + 20)}>下一页</button>
        </div>
        <div className="merchant-trend" aria-label="近14天点击趋势">
          {data.trend.map((day) => <div key={day.date} title={`${day.date}：${day.clicks} 次`}>
            <span>{day.clicks}</span><i style={{ height: `${Math.max(2, day.clicks / Math.max(1,
              ...data.trend.map((item) => item.clicks)) * 70)}px` }} />
            <small>{day.date.slice(5)}</small></div>)}
        </div>
      </details>
    </>}
  </section>;
}
