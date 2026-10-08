import type { Plan } from './CommercePanel';
import { yuanText } from './money';
import './Benefits.css';

export function PlanCards({ plans, current = [], disabled, onPurchase }: {
  plans: Plan[]; current?: string[]; disabled: boolean; onPurchase: (plan: Plan) => void;
}) {
  return <div className="plan-sections">{['customer', 'merchant'].map((audience) => {
    const selected = plans.filter((p) => p.audience === audience && (p.purchasable || p.id === 'merchant_free'))
      .sort((a, b) => a.price_cents - b.price_cents);
    if (!selected.length) return null;
    return <section key={audience} aria-label={audience === 'customer' ? '顾客会员套餐' : '商家会员套餐'}>
      <h3>{audience === 'customer' ? '顾客会员' : '商家会员'}</h3>
      <div className="plan-cards">{selected.map((p) => <article key={p.id}
        className={`plan-card ${p.id === 'merchant_standard' ? 'recommended' : ''}`}>
        <div className="plan-badges">{current.includes(p.id) && <span>当前套餐</span>}
          {p.id === 'merchant_standard' && <span>推荐方案</span>}</div>
        <h4>{p.name}</h4><p className="plan-price">¥{yuanText(p.price_cents)}<small> / {p.period_months} 个月</small></p>
        <p>{p.id === 'merchant_free' ? '仅注册首月有效，到期保留已有商品' : '自生效时刻起按日历月计期'}</p>
        {audience === 'merchant' ? <ul><li>当期新增服装 {p.entitlements.garment_upload === true ? '不限量' : p.entitlements.garment_upload} 件</li>
          <li>AI 描述 {Number(p.entitlements.ai_description || 0)} 次 / 周期</li><li>编辑不扣次数，删除不返还额度</li></ul>
          : <ul><li>首次成功付费赠 1000 积分，每账号一次</li><li>每日签到 {Number(p.entitlements.daily_signin_points || 0)} 积分</li>
            <li>每日推荐 {Number(p.entitlements.daily_recommendation || 0)} 次</li><li>每月 {Number(p.entitlements.makeup_cards || 0)} 张补签卡，全勤奖 {Number(p.entitlements.full_attendance_points || 0)} 积分</li></ul>}
        {p.purchasable ? <button type="button" className="button" disabled={disabled} onClick={() => onPurchase(p)}>
          {current.includes(p.id) ? '续费' : '购买'}{p.name} · ¥{yuanText(p.price_cents)}</button> : <p>注册首月自动享有</p>}
      </article>)}</div>
    </section>;
  })}</div>;
}
