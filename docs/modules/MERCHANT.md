# 商家账号与服装指标导入

商家在这里注册账号、登录，并把自己的服装按**统一指标**导入数据库，供穿搭推荐与尺码匹配复用。所有数据存在本机的 `data/merchants.sqlite3`，图片沿用共享资产目录 `data/assets/`。

## 商家后台页面（推荐入口）

应用左侧导航的**「商家后台」**就是给真人商家用的界面，不需要手写 HTTP：

- **注册 / 登录**：注册后自动登录；令牌存在浏览器本地，刷新不掉线，令牌失效时自动回到登录页（401 会清空令牌）。
- **我的商品**：列表显示颜色块或商品图、品类、价格、图片数与**适配区间摘要**（例如「适合身高 158–176 · 适合胸围 86–96」），可直接 编辑 / 发布 / 转为草稿 / 删除。
- **新建与编辑商品**：按「基本信息 / 尺寸与适配区间 / 版型与属性 + 商品图」三段填写。**所有下拉项、取值范围与长度上限都由 `GET /api/garment-options` 提供**，该文档由后端校验器生成，因此表单不可能放过一个后端会拒绝的值；越界、区间倒置、颜色格式等在前端就地提示，不会发出注定 422 的请求。
- **套装**：从**已发布**的单品里勾选组合（上限见 `look_items_max`），填名称、搭配故事、风格/季节/场合，配色默认取成员主色。
- 商品与套装都可先存草稿：草稿不进推荐，发布后才出现在「穿搭推荐」的候选集里。

页面上的每个动作背后就是下面这些接口，第三方商家系统可以按同一套契约接入。

## 接入流程

1. **注册**：`POST /api/merchant/register`，拿到 `merchant_id`。
2. **登录**：`POST /api/merchant/login`，拿到 `access_token`（默认 12 小时有效）。
3. **上传商品**：`POST /api/merchant/garments`，一次 multipart 提交「指标 JSON + 0–8 张图片」。
4. **补充/修改**：`PATCH` 改指标，`POST .../images` 追加图片，`DELETE` 删除。
5. **上架**：把 `status` 改成 `published`，公开只读接口才会返回它。

除注册与登录外，所有 `/api/merchant/*` 都需要请求头 `Authorization: Bearer <access_token>`。`/api/body-profile`、`/api/garment-options` 与公开只读接口不需要令牌，见下文。浏览器里也可以直接打开 `/docs`：商家端点在 OpenAPI 文档里声明了 Bearer 方案，点右上角 **Authorize** 粘贴令牌即可逐个调用。

## 鉴权

- 令牌是自实现的 HS256 JWT，载荷含 `sub`（merchant_id）、`iat`、`exp`，以及 `pwd`（签发时密码哈希的短指纹）；签名密钥来自设置项 `ITP_JWT_SECRET`。
- `ITP_JWT_SECRET` 为空时，服务在**第一次真正用到商家功能时**生成 64 位十六进制密钥并写入本地 `.env`（原子替换、权限 0600、拒绝符号链接），日志只提示"已生成"，不打印密钥本身。若 `.env` 不可写，则使用临时密钥并在日志中警告：**重启后所有令牌失效**。
- 登录失败统一返回 401「账号或密码不正确」，不区分账号不存在与密码错误；账号不存在时也会执行一次等价的 scrypt 校验，避免通过响应时间区分。
- 商家账号被禁用后，任何已签发的令牌都会得到 403。
- **改密码即吊销**：`POST /api/merchant/password`（需当前密码）或本地重置脚本改了密码后，所有旧令牌因 `pwd` 指纹不再匹配而立即失效，返回 401「密码已修改，请用新密码重新登录」。`pwd` 是密码哈希的 SHA-256 前缀（16 个十六进制字符），无法从中还原密码或哈希；不带该声明的旧令牌仍然可用。
- 密码用 `hashlib.scrypt`（n=16384, r=8, p=1，每账号独立随机盐）存储，格式 `scrypt$n$r$p$salt_hex$hash_hex`，校验用 `hmac.compare_digest`。密码本身不落库、不写日志。

### 忘记密码怎么办

账号存在本机 SQLite 里，没有邮箱与第二因素，所以恢复方式就是"能读到数据目录的人可以重置"：

```
python scripts/reset_merchant_password.py --list                    # 看看有哪些账号
python scripts/reset_merchant_password.py --name demo-shop          # 生成 16 位随机密码
python scripts/reset_merchant_password.py --name demo-shop --password 自己的新密码
```

重置同样会让旧令牌全部失效。登录后在「商家后台 → 账号设置」里随时可以自己改密（需要当前密码）。

## 状态码

| 状态码 | 含义 |
| --- | --- |
| 200 / 201 / 204 | 成功 |
| 401 | 未登录、令牌无效或过期、账号或密码不正确 |
| 403 | 商家账号已被禁用；非本地来源的写请求 |
| 404 | 资源不存在**或不属于当前商家**（两种情况故意不区分，避免泄露 id 是否存在） |
| 409 | 商家账号重复、货号重复、商品数量超出配额 |
| 411 | 上传请求缺少 `Content-Length` |
| 413 | 请求体过大（单图 >10 MiB，或整体超过上限） |
| 422 | 指标字段校验失败，`detail` 为中文原因 |

## 服装指标

`metrics` 以 JSON 存储。标 ★ 为必填；未提供的可选字段在响应里是 `null`，键始终存在，方便前端直接绑定。未知字段（含拼写错误）一律 422。

| 字段 | 必填 | 类型与取值 | 边界 |
| --- | --- | --- | --- |
| `category` | ★ | `上装` / `下装` / `外套` / `鞋履` / `配饰` | 枚举 |
| `name` | ★ | 文本 | ≤ 200 字，不能为空 |
| `status` | ★ | `draft` / `published` | 枚举 |
| `sku` | | 文本，商家自己的货号 | ≤ 80 字；同一商家内唯一，`null` 表示无货号 |
| `brand` | | 文本 | ≤ 80 字 |
| `price_cents` | | 整数，单位分 | 0 – 10¹² |
| `measurements` | | 对象，单位 cm | 每项 > 0 且 ≤ 300 |
| `measurements.shoulder_cm` `bust_cm` `waist_cm` `hip_cm` `length_cm` `hem_cm` | | 数字 | 同上 |
| `fit_ranges` | | 对象，每项 `[最小值, 最大值]` | 两端必须落在下列区间，且 最小值 ≤ 最大值 |
| `fit_ranges.height_cm` | | 数字区间 | 120 – 220 |
| `fit_ranges.bust_cm` | | 数字区间 | 60 – 160 |
| `fit_ranges.waist_cm` | | 数字区间 | 45 – 150 |
| `fit_ranges.hip_cm` | | 数字区间 | 60 – 170 |
| `fit_ranges.shoulder_cm` | | 数字区间 | 25 – 70 |
| `attributes.silhouette` | | `修身` / `标准` / `宽松` / `oversize` | 枚举 |
| `attributes.stretch` | | `无弹` / `微弹` / `高弹` | 枚举 |
| `attributes.weight_gsm` | | 整数，克重 | 20 – 2000 |
| `attributes.color` | | `#rrggbb` **小写**十六进制 | 正则校验 |
| `attributes.length_type` | | `短款` / `常规` / `长款` | 枚举 |
| `style` | | `通勤` / `休闲` / `街头` / `运动` / `度假` / `复古` / `极简` / `学院` | 枚举 |
| `season` | | `春` / `夏` / `秋` / `冬` / `四季` | 枚举 |
| `occasion` | | 自由中文短语 | ≤ 80 字 |
| `description` | | 商品介绍，允许换行 | ≤ 1000 字 |
| `tips` | | 字符串数组 | ≤ 3 条，每条 ≤ 120 字 |

**补丁语义**：`PATCH` 只改传入的字段。`measurements` / `fit_ranges` / `attributes` 三节**按字段合并**——只传 `{"bust_cm": 95}` 时其余尺寸保持原值；把某一项显式设为 `null` 可清掉它；把整节设为 `null` 可清空整节。

## 本地单用户接口：人体指标

人体数据（身高/体重/肩宽/胸围/腰围/臀围）是**本机使用者自己的数据**，采集入口在「人体建模」页，属于选填项：填了就用于尺码匹配，不填也不影响任何流程。因此这两个端点**不需要商家令牌**，与 `/api/jobs`、`/api/assets` 一样靠"只绑定回环地址 + 单机部署"来保护；带上令牌调用同样能成功，两种调用共享同一份数据。

`PUT /api/body-profile` 保存，`GET /api/body-profile?job_id=` 读取，字段全部可空：

| 字段 | 单位 | 边界 |
| --- | --- | --- |
| `height_cm` | cm | 100 – 250 |
| `weight_kg` | kg | 20 – 300 |
| `shoulder_cm` | cm | 25 – 70 |
| `bust_cm` | cm | 60 – 200 |
| `waist_cm` | cm | 40 – 200 |
| `hip_cm` | cm | 60 – 220 |

- 响应顶层固定为 `job_id`、`updated` 与上述六个字段；没有数据时六个字段全为 `null`，**不返回 404**。
- 请求体可带 `job_id`：带则保存到该任务名下，不带则保存为「当前默认档案」。
- 部分写入只更新传入字段，不会把默认档案的值复制进某个任务的档案；显式传 `null` 表示清除该项。
- 读取时优先返回该任务的档案，没有则回退到默认档案；越界或类型错误返回 422，且不会写入任何数据。

```bash
# 不需要任何令牌：这就是「人体建模」页保存的选填数据
curl -sS -X PUT http://127.0.0.1:8000/api/body-profile \
  -H 'Content-Type: application/json' \
  -d '{"height_cm":172,"weight_kg":62,"waist_cm":74}'

curl -sS http://127.0.0.1:8000/api/body-profile
# {"job_id":null,"updated":1791210725.0,"height_cm":172.0,"weight_kg":62.0,
#  "shoulder_cm":null,"bust_cm":null,"waist_cm":74.0,"hip_cm":null}
```

## 穿搭（looks）

`POST /api/merchant/looks` 的字段：`name`★（≤80 字）、`status`★、`story`（≤1000 字）、`style`、`season`、`occasion`、`palette`（≤6 个 `#rrggbb` 小写色值）、`items`（≤12 个 garment id，必须都是自己的商品，否则 422）。

公开接口返回的穿搭只包含 **published** 的成员商品；商家自己的接口返回全部成员。删除商品会同时把它从所有穿搭中移除。

## 接口清单

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/merchant/register` | 否 | 注册，201 返回 `merchant_id`、`name`、`display_name` |
| POST | `/api/merchant/login` | 否 | 登录，返回 `access_token`、`token_type`、`expires_in` |
| GET | `/api/merchant/me` | 是 | 账号信息 + `quota` + `garment_count` |
| POST | `/api/merchant/garments` | 是 | multipart 导入商品（`payload` + `images`），201 |
| GET | `/api/merchant/garments` | 是 | 自己的商品，`limit` 1–100、`offset`、`status`，返回 `{total, items}` |
| GET | `/api/merchant/garments/{id}` | 是 | 自己的商品详情，否则 404 |
| PATCH | `/api/merchant/garments/{id}` | 是 | 部分更新指标 |
| DELETE | `/api/merchant/garments/{id}` | 是 | 204，同时删除图片记录与文件 |
| POST | `/api/merchant/garments/{id}/images` | 是 | 追加图片，201 返回图片列表 |
| DELETE | `/api/merchant/garments/{id}/images/{image_id}` | 是 | 204，同时删除文件 |
| POST | `/api/merchant/looks` | 是 | 新建穿搭，201 |
| GET | `/api/merchant/looks` | 是 | 自己的穿搭 |
| PATCH | `/api/merchant/looks/{id}` | 是 | 部分更新 |
| DELETE | `/api/merchant/looks/{id}` | 是 | 204 |
| PUT | `/api/body-profile` | **否** | 保存人体参数（可带 `job_id`），本机单用户数据 |
| GET | `/api/body-profile` | **否** | 读取，缺失时返回全 null 骨架 |
| GET | `/api/garments` | **否** | 公开商品列表，仅 `published`，支持 `style`/`season`/`occasion`/`category`/`limit`/`offset` |
| GET | `/api/garments/{id}` | **否** | 公开商品详情，非 `published` 返回 404 |
| GET | `/api/looks` | **否** | 公开穿搭列表，仅 `published` |
| GET | `/api/looks/{id}` | **否** | 公开穿搭详情 |
| GET | `/api/garment-images/{id}` | **否** | 商品图片文件，带 `Cache-Control: public, max-age=604800` |
| GET | `/api/garment-options` | **否** | 参考数据：枚举、字段标签、取值范围与长度上限，由校验器生成；表单据此构建 |

商品对象形状：

```json
{
  "id": "32 位十六进制",
  "merchant_id": "32 位十六进制",
  "status": "published",
  "created": 1791210725.0,
  "updated": 1791210725.0,
  "metrics": { "...": "见上文指标表" },
  "images": [{"id": "32 位十六进制", "url": "/api/garment-images/<id>", "position": 0, "created": 1791210725.0}]
}
```

## 图片要求

- 单张 ≤ 10 MiB；支持 PNG、JPEG、WebP；短边 ≥ 128 像素、总像素 ≤ 2500 万；不支持动图。
- 服务端统一转成 PNG，并把长边缩放到 ≤ 1024 像素后落盘；**不做去背景**。
- 单次请求最多 8 张，`images` 字段可重复出现（多文件）。
- 图片文件通过 `/api/garment-images/{image_id}` 提供，只接受服务自己生成的 32 位十六进制 id，文件名取自资产表，因此请求内容不会进入文件系统路径（目录穿越一律 404）。

## 配额

每个商家可导入的商品件数由 `ITP_MERCHANT_QUOTA` 决定（默认 200）。新建账号时写入配额，超出后导入返回 409「商品数量已达配额上限」。配额在写入前用同一把进程锁检查，因此并发导入不会突破上限。

## 请求示例

### curl

```bash
BASE=http://127.0.0.1:8000

curl -sS -X POST "$BASE/api/merchant/register" \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo-shop","display_name":"示例店铺","contact":"owner@example.com","password":"secret-password"}'

TOKEN=$(curl -sS -X POST "$BASE/api/merchant/login" \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo-shop","password":"secret-password"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -sS -X POST "$BASE/api/merchant/garments" \
  -H "Authorization: Bearer $TOKEN" \
  -F 'payload={"category":"上装","name":"落肩针织开衫","status":"published","sku":"ITP-001",
       "price_cents":39900,"measurements":{"shoulder_cm":52,"bust_cm":96,"length_cm":58},
       "fit_ranges":{"height_cm":[155,175],"bust_cm":[84,100]},
       "attributes":{"silhouette":"宽松","stretch":"微弹","weight_gsm":320,
       "color":"#c9d6bd","length_type":"常规"},
       "style":"通勤","season":"秋","occasion":"通勤办公","tips":["内搭保持同色"]};type=application/json' \
  -F 'images=@front.png;type=image/png' \
  -F 'images=@detail.png;type=image/png'
```

### Python（httpx，与项目依赖一致）

```python
import json
import httpx

metrics = {
    "category": "下装",
    "name": "高腰直筒西裤",
    "status": "published",
    "measurements": {"waist_cm": 68, "hip_cm": 96, "length_cm": 100},
    "fit_ranges": {"height_cm": [155, 178], "waist_cm": [62, 76]},
    "attributes": {"color": "#4a5b52", "stretch": "微弹"},
    "style": "通勤",
    "season": "四季",
}

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=60) as client:
    token = client.post("/api/merchant/login", json={
        "name": "demo-shop", "password": "secret-password",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    with open("pants.png", "rb") as handle:
        created = client.post(
            "/api/merchant/garments",
            headers=headers,
            data={"payload": json.dumps(metrics, ensure_ascii=False)},
            files=[("images", ("pants.png", handle, "image/png"))],
        )
    created.raise_for_status()
    garment = created.json()
    print(garment["id"], garment["images"][0]["url"])

with httpx.Client(base_url="http://127.0.0.1:8000") as client:
    print(client.get("/api/garments", params={"style": "通勤"}).json()["total"])
```

## 安全边界（当前为单机部署）

- 服务只监听回环地址，适合本机单用户使用；**没有**速率限制、没有邮箱验证、没有多因素认证。
- 改密码会吊销旧令牌，但**令牌本身没有刷新机制**：过期后只能重新登录；也没有"忘记密码"的线上流程，只能在本机用重置脚本（这也是唯一需要接触到数据目录的操作）。
- 任何人只要能读到 `data/merchants.sqlite3`，就能用重置脚本改掉密码——这是本地单用户工具的合理边界，但不适用于多租户托管。
- `ITP_JWT_SECRET` 与密码哈希都存在本机 `.env` / SQLite 中，`data/` 已被 Git 忽略；不要把 `.env`、`data/` 或任何密钥提交到仓库。
- 图片与指标按商家隔离：读取他人商品一律 404，不泄露 id 是否存在。
- 对公网开放前必须先补齐：HTTPS、请求限流、审计日志、按商家的资源配额与磁盘清理、令牌刷新与主动吊销接口、以及备份与恢复流程。
