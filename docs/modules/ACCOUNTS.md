# 统一用户账号

顾客与商家共享同一账号、密码规则、scrypt 实现、JWT 签名密钥及浏览器会话。
不新增平行认证体系。页面右上角的圆形头像进入登录/注册，登录后可打开账号
菜单、个人资料、修改密码与退出登录。模型 API Key/token 与用户登录令牌是
不同概念：前者仅由服务端运维管理，客户端没有配置入口。

## 存储与迁移

复用 `MerchantStore` 和 `merchants.sqlite3` 中原有 `merchants` 凭据表，增加
`role`（`customer` / `merchant`）列，旧行默认为商家。历史表名保留，以免
复制账号、修改商品外键或丢失 scrypt 哈希。迁移只增加列及 `auth_revocations`
表，可重复启动；旧账号 ID、哈希和商品不变。顾客的 legacy quota 为 0。

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
顾客照片及模型的独立本地化属于 C04。过渡部署仍保留共享工作台的 Basic
访问保护；新账号接口和 JWT 保护接口不受 Basic/Bearer 同头冲突影响。完成
资产隔离前不能把当前版本当作完全开放的多租户商业站点。

## 验证

`tests/test_accounts.py` 覆盖两种角色、老账号迁移、统一旧接口、越权、资料
边界、注销持久化、随机令牌、改密全会话吊销、限流、校验脱敏、精确到期和
公网签名密钥持久化失败。`frontend/tests/accounts.spec.ts` 在桌面/手机覆盖
头像、注册登录、刷新恢复、资料保存、退出、顾客商家限制与改密。
