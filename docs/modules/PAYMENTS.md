# 统一支付订单

充值、普通会员及商业会员使用同一套 `payment_orders`，与账号、钱包、订阅
共用 `merchants.sqlite3`。客户端只选择支付方式、输入充值金额或选择套餐；
不存在填写商户密钥、服务地址或发送“支付成功”标记的入口。

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
未知证书序列号及 `SIGNTEST`。平台密钥轮换由运维更新环境后重启后端；不从
未验签来源自动安装证书。依据：[官方签名规范](https://pay.wechatpay.cn/doc/v3/merchant/4012365342)、
[官方 SDK 通知处理](https://github.com/wechatpay-apiv3/wechatpay-php/blob/main/README.md)。

所有网络请求只发往官方固定 HTTPS 地址，无重定向，限时并限制响应大小。
二维码通过本地库生成 PNG，不调用二维码服务。前端轮询本系统订单，不自动
重复访问支付渠道；“查询支付结果”才请求服务端官方查询，且有频率限制。

## 运维配置与回调

字段见 `.env.example`。支付宝需要应用 ID、卖家 ID、RSA 私钥及支付宝公钥；
微信需要应用 ID、商户号、商户证书序列号、私钥、32 字节 API v3 密钥和可信
平台密钥映射。PEM 可在带引号的环境值里使用转义换行。真实值仅留在服务器。

`ITP_PAYMENT_NOTIFY_ORIGIN` 是纯 HTTPS origin，留空使用 `ITP_PUBLIC_ORIGIN`。
配置渠道通知地址：

- `/api/payments/callbacks/alipay`
- `/api/payments/callbacks/wechat`

Nginx 对这些路径关闭旧工作台 Basic gate，但服务端始终验签；请求上限
128 KiB。`/api/payments/methods` 只返回可用状态，不公开密钥或商户配置。
账号订单路由由 JWT 保护。没有配置完整真实凭据时显示“支付暂未开放”，
**不会自动启用模拟充值，也不会把配置齐全当作真实支付已验收**。

## 测试边界

仅 `ITP_ENVIRONMENT=development/test`、没有公网 origin 且显式启用 mock 并
配置至少 32 字符服务端签名密钥时可用模拟支付。生产环境拒绝该配置，模拟
确认路由不会注册或出现在 OpenAPI。模拟事件仍由服务端生成、HMAC 验证并
走统一履约；前端不能提供金额/成功状态绕过订单校验。

pytest 使用生成的测试 RSA 密钥及离线 HTTP 响应，覆盖签名篡改、通知解密、
过期时间、未知证书、金额/商户不符、并发与重复回调、崩溃中间态和套餐快照。
Playwright 验证非默认价格、整数分、待支付不加余额和无凭据时不回退 mock。
这些不是支付宝/微信商户开通或真实交易验收；本次没有调用真实支付接口。
