# ITP · Image to Pose & 3D

国内服务优先的角色姿势控制与 3D 资产工作台。参考 Meshy Custom Pose 视频，支持角色图、姿势参考图、多视角、几何生成、拓扑、PBR、绑骨与资产导出。

## 当前可用

统一顾客/商家账号已复用原商家认证：右上角头像支持注册登录、账号菜单、
个人资料、改密与退出，商家后台由服务端角色校验保护。旧商家账号与商品
保留，见 [账号模块](docs/modules/ACCOUNTS.md)。钱包与周期权益已实现，见
[资金模块](docs/modules/COMMERCE.md)。[支付订单](docs/modules/PAYMENTS.md)
已支持支付宝/微信官方验签协议及本地测试，真实商户交易尚未验收。
顾客照片、模型与人体数据只留在浏览器 IndexedDB，服务端仅在任务期间持有临时副本，
见 [顾客本地数据与临时计算](docs/modules/PRIVACY.md)；余额、订单与权益等持久记录
不含顾客素材。当前仍不是完整多租户商业发布版，状态见 [实施计划](docs/PLAN.md)。

本地图片上传、可选 CPU 去背景、多视角输入、姿势设置、任务流程（任务镜像保存在本机浏览器）、GLB 预览与历史列表。云端适配器覆盖千问姿势编辑、混元几何、智能拓扑、PBR 纹理、自动绑骨与 FBX 转换，且等待真实服务配置和样例验收。穿搭推荐解析图生 3D 产出的 GLB，估算肩宽、腰线、胯宽与腿身比，再从本地精选目录按比例给出成套穿搭；尚未生成模型时按通用体型推荐。每套穿搭会从图片检索服务取真实穿搭图片并缓存在本机，默认使用免 key 的 360 图片，运维可通过服务端环境变量切换到 Unsplash 或 Pixabay，取不到图片时回落到配色示意。

穿搭推荐同时支持**按尺码指标匹配**：「人体建模」页可以选填身高、体重、肩宽、胸围、腰围、臀围（只保存在本机浏览器；不填也能用，未填项由模型比例推算并标注「估算」）；服装侧由商家录入，指标包含尺寸、适配区间与版型/弹性/克重/主色等属性。推荐结果逐维度给出「你的数值 / 该款区间 / 合身状态 / 中文理由」，而不是只有标签和分数。商家用左侧导航的**「商家后台」**页面注册登录、上传商品图并填写指标；同一套契约也开放为 HTTP 接口，供第三方系统接入。详见 [指标与匹配规范](docs/modules/METRICS.md) 与 [商家接口](docs/modules/MERCHANT.md)。

商家还能为每件商品填一个可选的**购买链接**，顾客在推荐页点「查看商品」会先记录点击、再用安全的新标签页打开该链接；商家后台的**数据概览**显示今日、本月、累计点击与逐商品明细、近 14 天趋势。推荐排序在原尺码适配层之上叠加风格、季节、场景与**本机浏览器**里的行为偏好，每条结果都给出中文依据。整套界面对齐中文电商习惯（白/浅灰底 + 淘宝系橙 `#FF5000` 作为强调色），详见 [穿搭推荐](docs/modules/OUTFITS.md) 与 [商家接口](docs/modules/MERCHANT.md)。

顾客与商家客户端不提供 API 密钥、模型服务地址或访问令牌的配置功能和页面。平台运维参照 [`.env.example`](.env.example) 在服务端配置 `.env` 或进程环境变量，变更后重启后端；客户端仅通过 `/api/capabilities` 获取可用功能。未配置的服务显示“暂不可用”，仍可使用上传、可用的去背景和本地 GLB 导入。

## 本地运行

在本项目根目录执行：

```bash
uv sync --locked --index-url https://pypi.tuna.tsinghua.edu.cn/simple
npm ci --prefix frontend --registry=https://registry.npmmirror.com
npm run build --prefix frontend
uv run --no-sync uvicorn itp.api:create_app --factory --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/`。小型去背景权重可通过 `uv run --no-sync python scripts/download_models.py` 单独安装；当前工作区已安装在被 Git 忽略的 `models/u2netp.onnx`。运行时不会下载权重。开发模式可同时在 `frontend/` 执行 `npm run dev`，打开 `http://127.0.0.1:5173/`。

准备接入云端时，由平台运维在服务端配置环境变量，具体字段见 [API 配置文档](docs/modules/PROVIDERS.md)。不要把密钥提交到 Git。本地启动默认只监听 `127.0.0.1`；公网部署见 [AutoDL 部署说明](deploy/autodl/README.md)。

## 电商化升级要点

本轮把站点从“AI 工作台”调整为中文电商外观，并把**部署层的站点密码**换成了 ITP 自己的账号体系。

**为什么移除部署层 Basic Auth。** 旧配置在 `deploy/autodl/nginx-itp.conf` 的 `server` 级设置了 `auth_basic "ITP Studio"` 与 `auth_basic_user_file /etc/nginx/itp.htpasswd`，访问任何页面都先弹浏览器原生密码框。整站共享一个口令既无法区分顾客与商家，也无法做归属、额度和审计，还要为每个新用户发一次口令。移除后首页、`/merchant`、`/account`、`/admin` 直接打开，流程变成「直接访问网站 → 用户按业务需要注册/登录 ITP 账号」，同时清掉了为绕过该密码而加的冗余 `auth_basic off`。

**ITP 自身认证完整保留。** 客户/商家/Admin 的注册登录、JWT 鉴权、角色权限、账户资料、钱包、余额、充值、会员、订单和商家数据隔离都没有删除：`accounts.py`、`merchant_auth.py`、账号页面和 `/api/*` 的鉴权依赖仍在，未登录者读不到钱包、流水、订单或任何商家数据。`/api/body-profile` 这类遗留共享测量接口在公网继续返回 404。

**商品购买链接与点击统计。** 商品新增可选 `purchase_url`（只接受 `http://` / `https://`，拒绝 `javascript:` 等危险协议、内嵌账号密码和空白）；顾客点购买入口时前端**先调用** `POST /api/garments/{id}/clicks` 落盘，再用 `noopener,noreferrer` 打开新标签页，统计失败只提示不中断跳转。`garment_clicks` 表记录 `id`、`garment_id`、`merchant_id`、`created`，并按商品/商家与时间建索引；商家通过 `GET /api/merchant/analytics` 读取**自己的**今日/本月/累计点击、逐商品明细与近 14 天趋势，越权访问被拒绝。

**推荐算法与权重。** `size_match.py` 的尺码适配仍是核心层，`hybrid_recommendation.py` 在其上叠加风格、季节、场景、历史与探索，权重集中在 `RANKING_WEIGHTS`：尺码 0.72、风格 0.08、季节 0.05、场景 0.05、历史 0.08、探索 0.02；无历史、无筛选时 `R = S`，原尺码分数与排序完全保留。完整公式、居中调整与可靠性平滑见 [穿搭推荐](docs/modules/OUTFITS.md)。

**行为隐私边界。** 浏览详情计 1 分、点击购买入口计 3 分，只在本机浏览器按**风格与品类**累计计数（单族超过 20 时整族减半），请求时上传压缩后的计数向量；顾客照片、人体数据、商品 ID、URL、时间戳和原始事件序列都不上传，服务端不建立顾客行为画像。点击统计是商品流量数据，与本地偏好计数分开，也不含账号 ID。

**钱包、充值、支付与退款。** `/account` 账户概览采用余额/会员/调用价格三张指标卡，个人资料与充值、订单与资金流水分栏（手机纵向堆叠）；改密和完整权益可展开。账户页把余额放在第一视觉位置、充值为主要 CTA，金额沿用整数分、订单幂等、余额非负校验和失败任务自动退款；订单处理中可重新查询状态，但前端不提供“标记支付成功”的入口。详见 [资金模块](docs/modules/COMMERCE.md) 与 [支付订单](docs/modules/PAYMENTS.md)。

**数据库迁移方式。** 全部为启动时幂等执行的新增式迁移（新增可空列、`CREATE TABLE IF NOT EXISTS` 与索引），不改写已有列，已有商家、商品、账号、钱包、订单、会员和任务数据保持原值；升级前仍建议用 SQLite 在线备份 API 备份。

## 验证

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest
npm run build --prefix frontend
```

本机 ROS 环境会自动加载一个缺少依赖的无关 pytest 插件，因此这里关闭第三方插件自动发现。测试使用替身云响应检查协议和流程；**尚未进行收费 API 的真实图生 3D 与视频效果质量验收**。

浏览器验收需先启动本地后端、安装 Playwright Chromium，然后在 `frontend/` 运行：

```bash
uv run --no-sync uvicorn itp.api:create_app --factory --host 127.0.0.1 --port 8000
npm run test:e2e --prefix frontend
```

它覆盖桌面和手机布局的离线上传、姿势切换、本地 GLB 预览和可用状态；另验证客户端不请求配置接口、不展示凭据输入。本轮新增 `frontend/tests/commerce-upgrade.spec.ts`，在真实后端上走完“公开页面无 Basic Auth → 商家建商品并填购买链接 → 顾客记录点击再跳转 → 统计失败仍可打开 → 商家只看自己的点击 → 钱包页”，并把桌面与手机截图写到 `/home/fjp/temp/itp-commerce-qa/`。

本轮相关后端测试：`tests/test_deployment.py`（站点无 Basic Auth、遗留接口 404）、`tests/test_product_links.py`（危险链接拒绝与新增式迁移）、`tests/test_product_clicks.py`（点击累计、跨商家隔离、并发与重启）、`tests/test_hybrid_recommendation.py`、`tests/test_outfit_service.py`、`tests/test_recommendation_history_api.py`（冷启动、历史影响、极端偏好、不完整人体数据、商家无商品、匿名不落盘）、`tests/test_size_match.py`（原尺码算法回归）与 `tests/test_commerce.py`、`tests/test_payments.py`（未登录 401、越权隔离、充值、订单查询、流水与自动退款）。

人工验收：用 1440×900 与 412×915 视口逐个打开首页、`/outfits`、`/merchant`、`/account`、`/admin`，确认无横向溢出、图片不拉伸、商品图/名称/理由/价格/购买入口层级清晰、算法解释可展开、商家三个 KPI 与明细可读、余额与充值可见，并用 Tab 检查焦点。部署验收见 [AutoDL 部署说明](deploy/autodl/README.md)。

## 文档导航

- [需求与验收](docs/REQUIREMENTS.md)
- [系统架构](docs/ARCHITECTURE.md)
- [技术调研与来源](docs/RESEARCH.md)
- [实施计划](docs/PLAN.md)
- [本地 REST API](docs/API.md)
- [Web 工作台](docs/modules/WEB.md)
- [六视图图生 3D](docs/modules/SIX_VIEWS.md)
- [SeedDream 虚拟试穿](docs/modules/TRYON.md)
- [穿搭推荐](docs/modules/OUTFITS.md)
- [指标与匹配规范](docs/modules/METRICS.md)
- [商家接口](docs/modules/MERCHANT.md)
- [统一用户账号](docs/modules/ACCOUNTS.md)
- [钱包、会员与周期额度](docs/modules/COMMERCE.md)
- [统一支付订单](docs/modules/PAYMENTS.md)
- [FaceVerse 远程脸部精修](docs/modules/FACE_REFINEMENT.md)
- [模型与处理模块](docs/modules/GEOMETRY.md)
- [开发规范](CONTRIBUTING.md)

代码与开发注释使用英文，面向用户的界面和技术文档使用中文。模型、密钥、上传图片和生成资产不进入 Git。
