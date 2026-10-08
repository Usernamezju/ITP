# 统一支付订单

充值、普通会员及商业会员使用同一套 `payment_orders`，与账号、钱包、订阅
共用 `merchants.sqlite3`。客户端只选择支付方式、输入充值金额或选择套餐；
不存在填写商户密钥、服务地址或发送“支付成功”标记的入口。

## 前端订单查询

`PaymentPanel.tsx` 复用现有接口：选择充值金额与支付方式后创建订单，会员
购买仅传套餐 ID。订单列表通过 `GET /api/account/orders?limit=10&offset=...`
分页，点击订单显示金额、渠道、编号、时间、状态及可用的支付二维码。
待支付订单每三秒读取本平台已经落盘的状态；「刷新支付状态」调用
`POST /api/account/orders/{id}/refresh` 由服务端请求渠道并验签。

真实状态为 `created`、`submitting`、`pending`、`uncertain`、`paid`。分别显示
等待支付、订单处理中、状态待确认、支付已确认，并配文字和图标。只有服务端
返回 `paid` 才刷新余额/会员；二维码展示、点击查询与网络恢复都不代表付款成功。
网络失败说明下一步查询/重试；创建失败后相同请求保留原幂等键，防止网络
响应丢失时重复创建订单。账户切换时支付面板会重建，不混用另一账户请求。

当前后端保留迟到支付回调，不以本地计时取消订单。`expires` 到期后页面隐藏
旧二维码，提示「付款时限已到，请先刷新支付状态」，仍可查询原订单，不把
超时伪装为支付失败，也不劝已付款用户再次付款。失败建模的退款由钱包流水
展示（见 [资金模块](COMMERCE.md)）；目前没有渠道原路退款或退款处理中
支付订单 API，页面没有虚构这些状态。

## 订单与资金安全

- `POST /api/account/orders` 要求 JWT 与 `Idempotency-Key`。充值传整数分；
  购买会员只传 `plan_id`，价格和权益由服务端读取并保存快照。
- 同一账号、幂等键、请求只产生一个订单；改变请求返回 409。数据库先记录
  `submitting` 再请求支付渠道。请求超时标为 `uncertain`，不重新创建支付；
  用户可查询原订单，已支付回调即使迟到仍能履约。
- 官方验签后的回调或主动查询结果才能创建 `payment_transactions`。校验
  渠道、订单号、商户、应用、币种和整数分金额；渠道交易号及订单各自唯一。
  记账/发放订阅/修改订单状态在一个 `BEGIN IMMEDIATE` 事务完成。
- 重复通知返回成功但不重复入账；不匹配通知拒绝。会员保存购买时的套餐
  快照，后续价格调整不追溯修改。平台明确配置为零价的套餐可由服务器免费
  发放，而不是让浏览器决定价格。
- 数据库只保存业务订单、交易号、金额和权益，不保存付款人资料或回调原文。
  日志不记录签名、密钥、通知原文和支付账户资料。

## Provider

`PaymentProvider` 定义 `create`、`query`、`callback`。统一服务执行身份、
金额校验和原子履约，渠道实现只负责官方协议和验签。

支付宝使用官方预创建二维码、交易查询和 RSA2 签名。同步响应校验原始 JSON
子对象签名，不能反序列化后重排字段再验签；异步通知剔除 `sign/sign_type`
后验签，并核对 `app_id/seller_id`。官方查询不返回这两个字段，查询通过已签
应用请求和官方签名响应建立身份，仍核对订单、交易号、金额和成功状态。
协议依据：[预创建](https://developer.alibaba.com/docs/api.htm?apiId=862&docType=4)、
[交易查询](https://developer.alibaba.com/docs/api.htm?apiId=757&docType=4)、
[异步通知](https://developer.alibaba.com/docs/doc.htm?articleId=103296&docType=1&treeId=346)。

微信支付使用 API v3 Native：请求 RSA 签名、响应与回调平台签名、五分钟
时间窗口及 AES-256-GCM 通知解密。只信任运维预先配置的平台公钥/证书，拒绝
未知证书序列号及 `SIGNTEST`。平台公钥轮换在运维控制台的支付配置里完成：
新公钥按 ID 追加后新旧公钥并存，确认切换完成再移除旧 ID；不从
未验签来源自动安装证书。依据：[官方签名规范](https://pay.wechatpay.cn/doc/v3/merchant/4012365342)、
[官方 SDK 通知处理](https://github.com/wechatpay-apiv3/wechatpay-php/blob/main/README.md)。

所有网络请求只发往官方固定 HTTPS 地址，无重定向，限时并限制响应大小。
二维码通过本地库生成 PNG，不调用二维码服务。前端轮询本系统订单，不自动
重复访问支付渠道；“查询支付结果”才请求服务端官方查询，且有频率限制。

## 人工收款码（ManualQrProvider）

没有商户号时，运维可以上传自己的微信/支付宝个人收款码作为备用通道。
它与其他渠道共用 `payment_orders`，但**没有任何自动确认路径**：

- `ManualQrProvider.create()` 返回管理员上传图片的地址
  （`checkout.qr_image` → `/api/payments/manual/qr/<key>`）并置
  `checkout.manual = true`。顾客端读出这个标记后：状态显示「待人工确认」而不是
  「等待支付」——后者会让已经转过账的人以为钱没到、再转一次；同时明确写出应转
  金额，并说明这是一张**不含金额**的收款图片，转账金额要自己填，好让人不必守着
  银行 App 等一个不会出现的金额。「刷新支付状态」在这类订单上改叫「查询人工确认
  结果」，因为渠道那边确实没有结果可查，能变的是管理员的确认。
- `query()` 永远返回 `None`：扫码、轮询、「刷新支付状态」都不会把订单变成
  已支付；`callback()` 同样不产生任何已验证事件（`/api/payments/callbacks/`
  只接受 alipay/wechat/mock，人工渠道名直接 422）。顾客端也没有提交
  “支付成功”的入口。
- 订单初始状态是 `pending`，有效期 24 小时（在线渠道 30 分钟），以便顾客
  稍后再扫码；过期只影响前端展示，不影响管理员确认已经到账的款项。
- 只有管理员能改变结果：`/admin` 的「待确认人工支付订单」按订单号、账号、
  金额、类型、渠道、创建时间和状态列出待确认订单，可「确认到账」或「拒绝」。
  拒绝会清空该订单的二维码并置为 `rejected`，避免顾客继续转账。

确认到账由服务端在**一个事务**内完成，并且复用线上渠道同一条履约路径
（`PaymentService.fulfill` → `payment_transactions` + 钱包入账或订阅发放
→ 置 `paid`），绝不只有 `UPDATE payment_orders SET state='paid'`：

- 交易号是确定性的 `manual_<uuid5(order_id)>`，同一订单重复确认得到同一笔
  交易，第二次确认直接返回已支付订单，不会重复入账；钱包流水与订阅都按
  `order_id` 幂等。
- 金额、币种、归属全部取自订单行本身，确认接口不接受任何客户端金额或状态。
- 数据库忙或履约异常时整个事务回滚并返回 503，订单保持待确认，可安全重试。

收款码存在 `data/payment/manual/<32 位随机名>.png`（`data/` 已被 Git 忽略），
上传时由服务端解码并重新编码为 PNG，限制 4 MiB / 1600 万像素 / 短边 120px；
设置文件里只写入这个随机文件名（`ITP_PAYMENT_MANUAL_WECHAT_QR`、
`ITP_PAYMENT_MANUAL_ALIPAY_QR`），图片本身既不进 `.env` 也不进 Git。
`ITP_PAYMENT_MANUAL_ENABLED` 是总开关：关闭后顾客端不再出现人工收款选项、
无法创建新的人工订单，已创建的订单仍可确认到账（钱确实已经收了）。
收款码图片通过公开的随机地址提供，因为 `<img>` 不会带令牌；地址本身不可
猜测，且响应 `no-store`，它只是一张供扫码的图片，不代表任何支付结果。

## 运维配置与回调

字段见 `.env.example`。支付宝需要应用 ID、卖家 ID、RSA 私钥及支付宝公钥；
微信需要应用 ID、商户号、商户证书序列号、私钥、32 字节 API v3 密钥和可信
平台密钥映射。PEM 可在带引号的环境值里使用转义换行。真实值仅留在服务器。

`ITP_PAYMENT_NOTIFY_ORIGIN` 是纯 HTTPS origin，留空使用 `ITP_PUBLIC_ORIGIN`。
配置渠道通知地址：

- `/api/payments/callbacks/alipay`
- `/api/payments/callbacks/wechat`

Nginx 已取消站点级 Basic Auth，支付回调无需浏览器账号，但服务端始终验签；请求上限
128 KiB。`/api/payments/methods` 只返回可用状态，不公开密钥或商户配置。
账号订单路由由 JWT 保护。没有配置完整真实凭据时显示“支付暂未开放”，
**不会自动启用模拟充值，也不会把配置齐全当作真实支付已验收**。

## 控制台支付配置（真实商户凭据）

`/admin` 的「支付配置」是控制台里**唯一可写**的分区：模型凭据仍只读，商户
支付密钥只能来自运维自己的支付宝/微信商户账号，所以由管理员在页面上填写。
控制台仍只有 `admin` 角色可访问（`scripts/create_admin.py` 创建），接口
`include_in_schema=False`，并有每账号 10 次/分钟的写入限流。

| 接口 | 作用 |
| --- | --- |
| `GET /api/admin/payments` | 只回传“是否已配置”布尔值、公钥 ID 列表、渠道就绪状态、回调地址与人工收款码状态 |
| `POST /api/admin/payments/config` | 校验并写入凭据，重载渠道，随后探测官方接口并回传结果 |
| `POST /api/admin/payments/manual/qr` | 上传/替换一张收款码（multipart：`channel` + `file`），服务端重新编码后保存 |
| `GET /api/admin/payments/manual/orders` | 待确认的人工订单（`limit`/`offset`，含账号名） |
| `POST /api/admin/payments/manual/orders/{id}/confirm` | 核实到账后确认：一个事务内入账并置为 `paid` |
| `POST /api/admin/payments/manual/orders/{id}/reject` | 拒绝：不入账，清空二维码并置为 `rejected` |

请求字段：`alipay_app_id`、`alipay_seller_id`、`alipay_private_key`、
`alipay_public_key`、`wechat_app_id`、`wechat_mch_id`、`wechat_merchant_serial`、
`wechat_private_key`、`wechat_api_v3_key`、`wechat_platform_key_id` +
`wechat_platform_public_key`（成对追加）、`wechat_platform_key_remove`（移除一个
已轮换的公钥 ID）、`payment_manual_enabled`（人工收款总开关）、
`payment_manual_clear`（`wechat` 或 `alipay`，移除该收款码）。
`extra="forbid"`；未出现的字段保持原值，显式空串表示清除。

写入前的校验（失败返回 422 且不改动任何文件）：控制字符；标识符字符集；
RSA 私钥/公钥（或证书）能否解析且不低于 2048 位；APIv3 密钥是否 32 字符；
公钥 ID 与公钥是否成对出现。凭据随后由
`provider_settings.write_env_values` 原子重写进服务器 `.env`（0600、拒绝符号
链接、只改白名单键），因此**不会进入 Git、前端响应或日志**：响应只有
`*_set` 布尔值与公钥 ID，审计日志只记录字段名。若同名键已由进程环境变量
提供，接口返回 409，要求运维在启动环境中修改。

保存成功后 `PaymentService.reload(settings)` 就地重建渠道注册表——这是“重启
支付服务”的等价实现，不重启进程、不触碰订单/交易/钱包任何一行。随后
`probe_all()` 用一笔探测请求向官方接口核对真实凭据：

- 支付宝：`alipay.trade.query` 查询一个随机不存在订单，返回
  `ACQ.TRADE_NOT_EXIST` 说明平台接受了本应用签名、且响应能用所填支付宝公钥
  验签通过；
- 微信支付：查询随机不存在订单，`ORDER_NOT_EXIST` 的 404 响应同样带有官方
  签名，能验签通过说明商户证书与平台公钥都正确（签名被拒时官方返回 401/403）。

探测失败**不会**回滚已保存的配置：渠道照常加载，页面显示「渠道校验未通过：
原因」，运维修好后重新保存即可。服务器无法访问官方接口时提示检查网络与该
商户的接口权限。**这一步只做凭据自检，不创建任何交易**，也不代表商户号已
开通或已完成真实收款验收。

## 测试边界

仅 `ITP_ENVIRONMENT=development/test`、没有公网 origin 且显式启用 mock 并
配置至少 32 字符服务端签名密钥时可用模拟支付。生产环境拒绝该配置，模拟
确认路由不会注册或出现在 OpenAPI。模拟事件仍由服务端生成、HMAC 验证并
走统一履约；前端不能提供金额/成功状态绕过订单校验。

pytest 使用生成的测试 RSA 密钥及离线 HTTP 响应，覆盖签名篡改、通知解密、
过期时间、未知证书、金额/商户不符、并发与重复回调、崩溃中间态和套餐快照。
`tests/test_payment_config.py` 另外覆盖控制台写路径：非管理员（顾客/商家/
匿名）读写被拒且不落盘、非法密钥与不成对公钥被 422 拒绝、`.env` 原子写入与
0600 权限、环境变量优先 409、轮换与移除公钥、探测失败仍保留配置、日志与
响应不含密钥。`tests/test_payment_providers.py` 用离线传输验证两个渠道的
凭据探测（成功、返回真实交易、未验签响应、401）。Playwright 验证非默认价格、
整数分、待支付不加余额、无凭据时不回退 mock，以及管理员在控制台填写凭据后
只看到就绪状态与自检结果、页面不回显密钥。

`tests/test_manual_payments.py` 覆盖人工收款的全部拒绝路径与唯一入账路径：
未确认时刷新任意次数都不入账、普通用户与匿名无法确认或拒绝、管理员确认后
钱包按订单金额入账且只写一条 `payment_transactions`（交易号
`manual_<uuid5>`）、重复确认不重复入账、会员订单改为发放订阅而不是加余额、
拒绝后不可再确认且已支付订单不可拒绝、确认接口忽略客户端提交的金额与状态、
伪造回调 422、履约中数据库忙碌时 503 且事务回滚、关闭开关后不能创建新人工
订单而已有订单仍可确认、上传收款码的权限/类型/大小校验，以及清除收款码会
同时删除文件。Playwright 另外验证顾客端在人工订单上看到管理员上传的二维码、
反复刷新仍是「等待支付」且没有模拟付款入口，以及管理员在控制台完成上传、
启用、确认到账与拒绝。

**尚未完成真实商户验收**：上述链路使用官方协议与真实签名算法，但在没有
真实支付宝/微信商户凭据的环境中，二维码下单、官方回调验签与主动查询只经过
离线替身验证；拿到商户凭据后应在沙箱或小额真实订单上复核一次。
