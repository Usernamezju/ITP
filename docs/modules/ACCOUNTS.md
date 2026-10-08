# 统一用户账号

## 2026-10-08 手机号修正

注册页优先引导填写中国大陆 11 位手机号，号码可选，不阻断旧账号登录。
独立的 `account_phones` 保存账号归属、唯一手机号和验证标记；旧 `contact`
仍是联系方式，不自动回填号码或冒充验证。账号页支持补绑与换绑。
未配置短信网关时仅保存为“未验证”；配置后新号码必须通过短信，换绑已验证
号码还须验证原号码。通过短信可取回别人未经验证占用的号码，但不能覆盖另一
账号的已验证绑定。绑定与单次验证码消费在同一 SQLite 写事务内，竞争请求
不会绑定同一号码。验证码仅保存 HMAC，5 分钟过期、最多 5 次校验，发送有
号码、账号和来源频率限制；进程重启使未完成验证码失效。

运维配置 `ITP_SMS_ENDPOINT`（HTTPS）与 `ITP_SMS_API_KEY`：网关接受 Bearer
认证及 `{phone,code,expires_in,purpose}` JSON，发送成功返回 2xx；失败不写验证
标记，密钥不回传客户端。`GET /api/auth/phone-policy` 仅报告是否启用短信；
`POST /api/auth/sms` 发送注册验证码，注册请求追加 `phone`、`sms_challenge_id`、
`sms_code`。登录用户用 `POST /api/account/phone/sms` 和
`PUT /api/account/phone` 补绑／换绑，旧号码证明字段为
`old_challenge_id`、`old_code`。没有短信服务时不提供找回或验证承诺。

迁移仅新增两张表，可重复启动，不改账号、密码或已有商品。回滚代码后新表
保留但旧版本不使用；上线前用 SQLite 在线备份 API 留存数据库。验证见
`tests/test_phones.py`（格式、唯一、并发、尝试限制、归属、过期和重启）及
`frontend/tests/amendment-phones.spec.ts`（桌面／手机注册、补绑与刷新）。

顾客与商家共享同一账号、密码规则、scrypt 实现、JWT 签名密钥及浏览器会话。
不新增平行认证体系。页面圆形头像与侧栏「设置」都直接进入 `/account`：
未登录时显示登录/注册及主题设置，登录后显示账户概览、个人资料、修改密码、
退出登录及主题设置。旧 `/appearance` 地址自动跳转 `/account`。模型 API Key/token 与用户登录令牌是
不同概念：前者仅由服务端运维管理，客户端没有配置入口。

## 账户概览页面

`/account` 登录后采用三层卡片布局：顶部余额与会员状态；中间
个人资料与充值/会员；下方我的订单与最近资金流水。桌面分栏，手机按顺序
堆叠，路由、左侧导航和 JWT 会话保持原样。修改密码通过资料卡中的按钮
展开，提交后仍吊销所有旧登录状态并要求重新登录。

余额、套餐价格、会员生效区间与调用单价均读取服务端，加载或查询失败时
显示占位和重试提示，不伪造零余额。会员卡只统计当前已生效且未到期的订阅；
点击详情可展开完整权益和未来续费日期。顶部「账户信息与资金 / 主题设置」
链接在当前页面内跳转，显示模式（日间/夜间）继续由 `AppearancePage` 和共享
主题状态管理，仅保存在浏览器；未登录也可以切换模式，仍不能查询钱包。
旧的多套配色与高对比度选择在读取时自动迁移为日间模式，并清掉浏览器里
遗留的对比度键。
充值按钮聚焦金额输入，订单与流水
继续分页查询。没有配置支付渠道时显示说明并禁用订单创建，不承诺未实现的
线下充值。此次页面调整复用 `CommercePanel` / `PaymentPanel`，无需数据迁移。

## 从顾客升级为商家

顾客在「商家后台」看到的不再是拒绝提示，而是**注册成为商家**卡片：填写商家
名称与手机号后调用 `POST /api/account/merchant`，把**当前账号**的角色从
`customer` 改成 `merchant`，不新建账号、不改密码、不换 ID。因此：

- 同一枚 JWT 立即获得商家权限（服务端每次请求都从数据库读取角色，不看令牌
  里的角色声明），顾客端功能也全部保留：余额、订单、流水、建模、试穿与穿搭
  推荐继续可用，浏览器里的本机素材与账号分区不变。
- 上传额度按商家免费套餐从账号创建时间起算，无需额外迁移。
- 已经是商家返回 409，管理员账号返回 403；`display_name` 与 `contact` 复用
  个人资料的校验规则（1–40 / ≤80 字符、拒绝不可见控制字符），省略字段表示
  保留原值，显式传空字符串会被拒绝。
- 顾客在升级前访问 `/api/merchant/*` 仍是 403，升级后同一批接口返回 200。
  旧的 `POST /api/merchant/register` 仍然可用，供独立的商家账号注册。

## 头像

顾客与商家都可以上传自己的头像，也可以继续使用系统默认头像（内置
`frontend/src/assets/default-avatar.svg`，品牌橙底色的人形剪影，随应用一起
发布，不依赖任何外部图床）。入口在「个人资料」卡：`上传头像` 选择文件，
`恢复默认头像` 清空；商家后台顶栏与顾客端右上角显示同一张图。

- `POST /api/account/avatar`（JWT，multipart `file`）在服务端解码后**重新编码**
  为 256×256 正方形 PNG：限制 4 MiB、2500 万像素、短边至少 64 像素，只接受
  PNG/JPEG/WebP 且拒绝动态图；重编码同时按 EXIF 方向摆正并**丢弃全部拍摄
  信息（含 GPS）**。`DELETE /api/account/avatar` 恢复默认。
- 文件按随机键存为 `data/avatars/<32 位十六进制>.png`，账号表只保存
  `avatar_key`（新增可空列，旧账号默认头像）。图片通过
  `GET /api/avatars/{key}` 提供，该地址不需要登录：键不可猜测，且**不含账号
  ID**；每次上传生成新键并删除旧文件，因此旧 URL 自然失效、缓存不会显示旧图。
  键格式外的路径一律 404（已覆盖编码后的目录穿越）。
- `avatar_key` 同时出现在 `/api/account/me`、`/api/merchant/me` 和管理员账号
  列表里，前端只拿它拼图片地址，拿不到也不会回显任何账号资料。

## 存储与迁移

复用 `MerchantStore` 和 `merchants.sqlite3` 中原有 `merchants` 凭据表，增加
`role`（`customer` / `merchant` / `admin`）列，旧行默认为商家。历史表名保留，
以免复制账号、修改商品外键或丢失 scrypt 哈希。迁移只增加列及
`auth_revocations` 表，可重复启动；旧账号 ID、哈希和商品不变。顾客的 legacy
quota 为 0。头像键 `avatar_key` 同样是启动时新增的可空列，旧账号保持默认
头像，不需要回填。

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
| POST | `/api/account/merchant` | Bearer；顾客把当前账号升级为商家，可传 display_name/contact |
| POST | `/api/account/avatar` | Bearer；multipart `file`，服务端重编码为 256×256 PNG 后保存 |
| DELETE | `/api/account/avatar` | Bearer；恢复默认头像并删除已上传的文件 |
| GET | `/api/avatars/{key}` | 公开；按随机键返回头像 PNG，键格式外一律 404 |
| GET | `/api/admin/status` 等 | Bearer + admin 角色；管理后台数据，见 [Web 工作台](WEB.md) |
| GET / POST | `/api/admin/gifts` 等 | Bearer + admin 角色；[会员与积分赠送](ADMIN_GIFTS.md)，包含审计记录与幂等保护 |

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
[顾客本地数据与临时计算](PRIVACY.md)。部署层全站 Basic Auth 已撤下：首页和商家页面直接访问，业务需要时登录 ClothiNation。
账号注册、JWT、角色检查、密码吊销及账号归属隔离全部保留。旧共享人体档案
接口在公网返回 404，现有客户端使用浏览器本地数据；旧数据和备份不自动删除。

## 验证

`tests/test_accounts.py` 覆盖两种角色、老账号迁移、统一旧接口、越权、资料
边界、注销持久化、随机令牌、改密全会话吊销、限流、校验脱敏、精确到期和
公网签名密钥持久化失败。`tests/test_admin_api.py` 覆盖 admin 角色迁移
（旧 CHECK 重建、备份、幂等、新库）、CLI 白名单、注册仍拒绝 admin，以及
`/api/admin/*` 的 401/403/禁用账号与只读数据。`frontend/tests/accounts.spec.ts`
在桌面/手机覆盖头像直接进入统一设置页、注册登录、刷新恢复、资料保存、退出、顾客商家限制与改密。
`routing.spec.ts` 验证两个入口在登录/未登录时都进入同页、旧地址跳转、主题
持久化和桌面/手机无溢出，`offline-features.spec.ts` 验证两种显示模式保存，
`theme.spec.ts` 断言日间纯白黑字、夜间纯黑红字红框以及商家端与管理端跟随。


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
