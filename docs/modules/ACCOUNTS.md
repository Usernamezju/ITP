# 统一用户账号

顾客与商家共享同一账号、密码规则、scrypt 实现、JWT 签名密钥及浏览器会话。
不新增平行认证体系。页面右上角的圆形头像进入登录/注册，登录后可打开账号
菜单、个人资料、修改密码与退出登录。模型 API Key/token 与用户登录令牌是
不同概念：前者仅由服务端运维管理，客户端没有配置入口。

## 账户概览页面

`/account` 登录后采用三层卡片布局：顶部余额、会员状态和调用价格；中间
个人资料与充值/会员；下方我的订单与最近资金流水。桌面分栏，手机按顺序
堆叠，路由、左侧导航和 JWT 会话保持原样。修改密码通过资料卡中的按钮
展开，提交后仍吊销所有旧登录状态并要求重新登录。

余额、套餐价格、会员生效区间与调用单价均读取服务端，加载或查询失败时
显示占位和重试提示，不伪造零余额。会员卡只统计当前已生效且未到期的订阅；
点击详情可展开完整权益和未来续费日期。充值按钮聚焦金额输入，订单与流水
继续分页查询。没有配置支付渠道时显示说明并禁用订单创建，不承诺未实现的
线下充值。此次页面调整复用 `CommercePanel` / `PaymentPanel`，无需数据迁移。

## 存储与迁移

复用 `MerchantStore` 和 `merchants.sqlite3` 中原有 `merchants` 凭据表，增加
`role`（`customer` / `merchant` / `admin`）列，旧行默认为商家。历史表名保留，
以免复制账号、修改商品外键或丢失 scrypt 哈希。迁移只增加列及
`auth_revocations` 表，可重复启动；旧账号 ID、哈希和商品不变。顾客的 legacy
quota 为 0。

`admin` 角色只能由服务器本地的 `scripts/create_admin.py` 创建：注册接口的
role 字段仍是 `customer`/`merchant` 二选一，提交 `admin` 返回 422。早期
数据库的角色 CHECK 只允许前两个角色，SQLite 不能直接修改 CHECK，因此首次
以新版启动时会在一笔事务内重建该表，重建前先用 SQLite 备份 API 写出
`merchants.sqlite3.bak`；迁移幂等，账号 ID、哈希、余额和商品归属均原样保留。

`auth_revocations` 只保存已吊销令牌的 SHA-256、账号 ID、到期时间，不保存
原始 JWT。注销仅吊销当前令牌；改密或运维重置使所有旧密码指纹失效。
`encode_token` 加入随机 `jti`，同一秒的不同登录也能独立注销。鉴权从数据库
查询当前角色，不相信客户端提交的角色声明；个人资料接口不能修改身份。
缺失密码指纹的更早期令牌必须重新登录。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | name、display_name、contact（选填）、password、role（默认 customer） |
| POST | `/api/auth/login` | 返回 access_token、token_type、expires_in |
| POST | `/api/auth/logout` | Bearer；吊销当前会话 |
| GET | `/api/account/me` | Bearer；个人资料，不含哈希与平台凭据 |
| PATCH | `/api/account/me` | Bearer；仅 display_name/contact 部分更新，拒绝 null |
| POST | `/api/account/password` | Bearer；current_password/new_password，成功后重新登录 |
| GET | `/api/admin/status` 等 | Bearer + admin 角色；只读管理后台数据，唯一的写接口是支付配置 `POST /api/admin/payments/config`，见 [Web 工作台](WEB.md) |

旧 `/api/merchant/register`、`login` 和 `password` 委托同一实现，兼容旧调用方；
商家登录和所有商家业务接口必须是 merchant 角色。普通顾客请求商家接口得到
403。账号重名在所有角色之间统一检查，不允许用另一角色重复注册同名账号。

## 配置与安全

- 服务端 `ITP_JWT_SECRET` 沿用原设置；不可提交或展示。首次生成时使用原子
  写入与 0600 权限。公网部署无法持久保存签名密钥时登录返回 503，不能悄悄
  使用重启失效的临时密钥。
- `ITP_MERCHANT_TOKEN_HOURS` 暂作为统一登录时长（旧配置名兼容）；默认 12 小时。
- 密码仍为 8–128 字，scrypt n=16384/r=8/p=1；未知账号也执行 scrypt 校验。
- 旧及新登录入口共用限流：同 IP/账号每 5 分钟最多 8 次；有效注册请求同 IP
  每小时最多 10 次。限流仅存进程内、容量有限，重启会重置；上线规模扩大时
  需升级为可信代理 + 共享限流，不可启动多 worker 绕过现有限制。
- 账号响应 `Cache-Control: no-store`，校验错误不回显密码或输入凭据。
- JWT 只发往本站 API，前端沿用一个浏览器存储键兼容商家历史会话；logout 和
  密码修改会同步清理商家控制台状态。没有实现邮箱/短信找回，请求运维核验。

会员、余额、价格与周期权益由 [C02 资金模块](COMMERCE.md) 提供；支付订单履约属于 C03。
顾客照片及模型的本地化已在 C04 完成，见
[顾客本地数据与临时计算](PRIVACY.md)。部署层全站 Basic Auth 已撤下：首页和商家页面直接访问，业务需要时登录 ITP。
账号注册、JWT、角色检查、密码吊销及账号归属隔离全部保留。旧共享人体档案
接口在公网返回 404，现有客户端使用浏览器本地数据；旧数据和备份不自动删除。

## 验证

`tests/test_accounts.py` 覆盖两种角色、老账号迁移、统一旧接口、越权、资料
边界、注销持久化、随机令牌、改密全会话吊销、限流、校验脱敏、精确到期和
公网签名密钥持久化失败。`tests/test_admin_api.py` 覆盖 admin 角色迁移
（旧 CHECK 重建、备份、幂等、新库）、CLI 白名单、注册仍拒绝 admin，以及
`/api/admin/*` 的 401/403/禁用账号与只读数据。`frontend/tests/accounts.spec.ts`
在桌面/手机覆盖头像、注册登录、刷新恢复、资料保存、退出、顾客商家限制与改密。


账号概览验证命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest
npm run build --prefix frontend
npm run test:e2e --prefix frontend -- tests/accounts.spec.ts tests/payments.spec.ts tests/routing.spec.ts --output=/tmp/itp-account-overview-results
```

人工浏览器验收：在 1440×900 和 412×915 下登录 `/account`，核对三张指标卡
的服务端金额及会员状态，检查桌面中间与底部卡片并排、手机纵向堆叠无溢出。
点击「立即充值」检查输入焦点，保存资料并重新加载，展开改密后验证密码不一致
提示及改密重新登录；创建订单后查询服务器状态，再检查流水分页及真实退款。
生产支付未配置时应显示说明且无法提交订单；钱包读取失败后仍能编辑资料和
查询订单，并可刷新重试。使用 Tab 检查按钮、输入框与权益折叠区焦点。
