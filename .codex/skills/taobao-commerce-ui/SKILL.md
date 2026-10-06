---
name: taobao-commerce-ui
description: Apply a Taobao/JD-inspired Chinese e-commerce visual system to the ITP React frontend while preserving the existing layout, routes, accessibility and business workflows. Use when changing ITP colors, typography, cards, buttons, product/recommendation surfaces, merchant analytics, wallet or commerce UI.
---

## Goal

把 ITP 从当前偏绿色 AI/SaaS 工作台的视觉语言，调整为成熟中文电商平台风格。主要参考淘宝的品牌橙、商品视觉优先级与信息密度，辅以京东清晰的交易状态和数据呈现方式。

只借鉴视觉系统和交互原则，不复制淘宝、天猫、京东的 Logo、品牌素材、页面源码或完整布局。

## Project constraints

当前前端为 React 19 + TypeScript + Vite + 普通 CSS + `lucide-react`。

优先修改和复用：

- `frontend/src/styles.css`
- `frontend/src/theme.ts`
- `frontend/src/CustomerLayout.tsx`
- `frontend/src/OutfitsPage.css`
- `frontend/src/MerchantPage.css`
- `frontend/src/AccountPage.css`
- `frontend/src/TryOnPage.css`
- 与 Commerce、Payment、Merchant、Outfits 相关的现有组件

不要仅为了 UI 重构引入 Tailwind、Ant Design、Material UI 等大型组件库。

除非需求明确要求，否则不要大幅修改：

- 路由结构
- 左侧主导航
- 人体建模核心 Grid
- 虚拟试穿主流程
- 当前主要页面层级

本轮优先修改视觉语言，而不是重做产品信息架构。

## Design tokens

默认电商主题：

`--commerce-primary: #FF5000;`  
`--commerce-primary-hover: #E94700;`  
`--commerce-primary-soft: #FFF2EC;`  
`--commerce-primary-border: #FFD4C2;`

`--commerce-text: #1F1F1F;`  
`--commerce-text-secondary: #666666;`  
`--commerce-text-tertiary: #999999;`

`--commerce-bg: #F5F5F5;`  
`--commerce-surface: #FFFFFF;`  
`--commerce-surface-soft: #FAFAFA;`

`--commerce-border: #DDDDDD;`  
`--commerce-divider: #EEEEEE;`

`--commerce-success: #16A34A;`  
`--commerce-warning: #F59E0B;`  
`--commerce-danger: #E1251B;`

品牌橙主要用于：

- 主按钮
- 购买按钮
- 当前选择状态
- 商品价格
- 推荐重点标签
- 重要统计数字
- 少量关键 Icon

不要把导航、页面背景、所有卡片和所有文字同时改成橙色。

## Typography

字体栈：

`system-ui, -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`

建议字号：

- 页面主标题：20–24px
- 区块标题：16–18px
- 普通正文：14px
- 按钮：14px
- 辅助信息：12px
- 极次要说明：11–12px

字重主要使用：

- 400 普通正文
- 500 普通标题
- 600 重要标题/按钮/价格

减少当前大量 9px、10px 微小文字和过多英文大写字距。面向消费者的主要界面优先使用自然中文。

## Layout principle

保持现有整体布局，但提高商品视觉优先级。

推荐页面信息层级：

1. 商品/穿搭图片
2. 商品名称
3. 匹配理由
4. 标签
5. 价格
6. 购买/查看商品按钮
7. 算法评分、指标来源等解释性信息

不要让“算法分数、模型状态、内部技术字段”抢过商品本身的视觉层级。

## Buttons

主要 CTA：

- 橙色背景
- 白色文字
- 6–10px 圆角
- hover 略微加深
- disabled 明显降低对比度

适用于：

- 立即查看
- 查看商品
- 充值
- 创建订单
- 保存主要修改
- 核心生成动作

次级按钮：

- 白底
- 浅灰边框
- 深灰文字

危险操作：

- 使用 `--commerce-danger`
- 不要和品牌橙混淆

禁止所有按钮都使用高饱和颜色。

## Cards

商品卡片、统计卡片、钱包卡片统一：

- 白色背景
- 8–12px 圆角
- `1px solid #EEEEEE` 或接近色
- 尽量使用边框和背景区分层级
- 避免大面积浓重阴影

hover：

- 边框轻微变深
- 或轻微 `translateY(-1px)`
- 不要夸张动画

## Product cards

商品图片必须是第一视觉焦点。

要求：

- 使用稳定 aspect ratio
- `object-fit: contain` 或适合商品图的裁切规则
- 禁止拉伸
- 图片加载失败提供占位
- 商品名最多两行
- 推荐原因 1–2 行
- 价格醒目
- CTA 清楚
- 匹配度可以显示，但不要只展示数字分数

推荐标签示例：

- 高匹配
- 肩宽合适
- 通勤推荐
- 最近常看
- 春秋适合

标签采用浅色底，不要做成几十种随机颜色。

## Merchant analytics

商家后台新增点击统计时，优先展示三个 KPI：

- 今日点击
- 本月点击
- 累计点击

三个数字卡片放在明显位置。

下面再展示：

- 商品名称
- 商品图片
- 今日
- 本月
- 累计
- 购买链接
- 必要的管理操作

若实现趋势图，使用简洁折线/柱状表现，不使用夸张渐变背景。

商家后台视觉应更接近成熟电商后台，而不是 AI 实验面板。

## Recommendation UI

穿搭推荐页面应给人“逛商品/发现搭配”的感觉，而不是“阅读算法报告”。

用户第一眼应该知道：

- 推荐了什么
- 为什么适合我
- 大概多少钱
- 如何查看商品

可解释算法信息仍然保留，但放在次级区域、折叠区域或卡片下半部分。

## Wallet and payment

余额作为账户资金页面最重要数字。

推荐层级：

当前余额  
→ 充值按钮  
→ 充值金额 / 套餐  
→ 支付方式  
→ 当前订单状态  
→ 最近资金流水

订单状态必须同时使用图标/文字和语义色，不能只依赖颜色：

- 待支付
- 支付中
- 支付成功
- 支付失败
- 已退款
- 退款处理中

支付失败必须告诉用户下一步操作，而不是只显示红色错误。

## Inputs

输入框：

- 白色或轻灰背景
- 8px 左右圆角
- 清晰边框
- focus 使用橙色 outline/border

商品链接输入框必须明显标识：

“支持淘宝、天猫、京东、品牌官网等 HTTP/HTTPS 链接”。

错误输入直接在字段附近给出中文说明。

## Navigation

暂不大改现有左侧导航结构。

可以将原深绿色导航调整成：

- 白色
- 深灰
- 或非常深的中性色

激活状态使用：

- 浅橙背景
- 橙色 Icon
- 橙色文字或侧边强调线

不要把整个侧边栏改成高饱和橙色。

## Accessibility

必须保留或增强：

- `:focus-visible`
- 键盘导航
- 触屏点击区域
- 文本对比度
- disabled 状态
- loading 状态

状态不能只通过颜色表达。

尊重：

`prefers-reduced-motion`

## Mobile

手机端必须保证：

- 商品卡片不横向溢出
- CTA 可正常点击
- 图片不拉伸
- 统计卡片自动折行
- 表格在小屏可改为卡片或允许合理横向滚动
- 余额和充值区域不会被侧栏遮挡

## Do not

不要：

- 大面积使用渐变
- 大面积使用玻璃拟态
- 使用过量 shadow
- 每个区域使用不同主色
- 把商品价格做成灰色次要信息
- 把推荐算法调试信息放在商品信息之前
- 复制淘宝/京东 Logo
- 直接复制商业网站 DOM/CSS
- 因 UI 修改破坏现有业务逻辑
- 引入没有必要的大型 UI 依赖

## Definition of done

UI 修改完成时必须满足：

1. 页面整体明显由绿色 SaaS 风格转为白/浅灰 + 电商橙视觉。
2. 页面主要布局没有未经需求允许的大改。
3. 商品图片和交易信息视觉优先级明显提升。
4. 推荐、商家、钱包、充值、订单页面视觉语言统一。
5. 所有主 CTA 使用统一橙色体系。
6. 桌面和手机页面无明显 overflow。
7. 图片无拉伸。
8. 可交互控件有清晰 focus。
9. 原有业务功能和权限没有因为 UI 重构失效。
10. `npm run build --prefix frontend` 必须通过。
11. 相关 Playwright 测试通过。
12. 对修改后的关键页面进行人工浏览器检查。
