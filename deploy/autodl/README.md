# AutoDL production deployment

This deployment uses the AutoDL HTTPS gateway (`:8443` -> container port `6006`),
public Nginx page access, ITP application JWT authentication, one Uvicorn worker, and the image's supervised
`/etc/autodl.sh` boot hook. The app and credentials live under
`/root/autodl-tmp/itp-app` and `/root/autodl-tmp/itp-data` respectively.

`configure.py` preserves the configured Tencent, Qwen and SeedDream secrets
from the existing `.env`, sets an exact public HTTPS origin, and points the
Flux Klein and FaceVerse endpoints at the loopback model services on ports
8788 and 8787. It creates the two bearer tokens if they are absent, in root-only
files outside this repository (`itp-flux-klein-service/.token` and
`itp-data/faceverse.token`), and reads them back into `.env`.
The deployment no longer creates a site password or htpasswd file. Keep model
service tokens, application JWT secrets, payment secrets and `.env` server-only.
Existing unused site-password files may be archived by the operator; upgrades
do not delete credentials or databases.

The vendor supervisor starts `/etc/autodl.sh` (a copy of `autodl.sh` here), which executes the nested
`supervisord` in this directory. The nested supervisor restarts Uvicorn and
Nginx if they fail. Check status with:

```bash
/root/miniconda3/bin/supervisorctl -c /root/autodl-tmp/itp-app/deploy/autodl/supervisord.conf status
curl -f http://127.0.0.1:8000/api/health
```

Nginx returns 404 for `/api/settings`, regardless of website credentials.
The API also disables the legacy settings endpoint when `ITP_PUBLIC_ORIGIN`
is set and omits it from OpenAPI. Customer and merchant clients have no model
key/token/endpoint configuration page. Operators manage provider credentials
in the server `.env` or process environment and restart only `itp-api` after
changes. Deployment must preserve the live `.env`; never replace it with the
example. Browser appearance preferences remain browser-local.

首页、`/merchant`、`/account`、`/admin` 可以直接打开，不再出现浏览器原生
Basic Auth 弹窗。访问流程为“直接访问网站 → 按业务需要注册/登录 ITP 账号”。
商品、钱包、订单及管理数据仍由 FastAPI 的 JWT、数据库角色和归属检查保护。
商家上传代理保留 81 MiB 上限，应用继续校验每张图片和请求体。

`/api/body-profile` 是遗留共享测量接口，公网 Nginx 和配置了 public origin 的
API 均返回 404；当前客户端使用账号分区的浏览器 IndexedDB。历史测量记录
留在原库，不删除、不自动归属新用户。`/api/settings` 继续返回 404。

## Admin console

`/admin` is a read-only operator console for the platform developer. Create the
administrator account on the server; registration can never mint one:

```bash
cd /root/autodl-tmp/itp-app && .venv/bin/python scripts/create_admin.py --name admin
```

The script asks for the password (or reads `ITP_ADMIN_PASSWORD`), refuses to
overwrite an existing account and never prints the password. Every `/api/admin/*` route still requires an admin Bearer token in
FastAPI: customers, merchants and anonymous visitors get 401/403. The console
shows system status, provider settings without secret values, account and
product totals, wallet/charge aggregates, orders and the tasks currently in
RAM. It has no write endpoint, so provider credentials remain `.env`-owned:
edit the server `.env` and restart `itp-api`. The `/admin` page itself opens directly; its data requires an admin account.

Unified registration and login are rate-limited by the application. Account
routes validate JWTs; merchant routes additionally validate database roles and
ownership. Account IDs, password hashes, garment ownership and token revocations
remain in the existing database.

The merchant module creates `merchants.sqlite3` and its tables on application
startup; it does not replace the existing assets or job databases. Back up live
SQLite databases with the SQLite backup API before upgrading. `ITP_JWT_SECRET`
must persist across restarts: retain an existing value; if absent, add one
generated secret without replacing other settings. No extra supervisor program
or model service is needed for merchant accounts and size recommendations.

Current customer photos, measurements and models stay browser-local, with
account-owned temporary server copies during processing. Old shared databases
and backups are retained for owner-authorized export/removal, never exposed by
the current routes. Merchant images remain persistent commercial assets.

## Payment upgrade

Install the updated locked Python dependencies before restarting `itp-api`:
the payment module uses `cryptography` and `qrcode`. No additional daemon or
database migration command is needed; tables are added on API startup.
Preserve the live environment and back up `merchants.sqlite3` with SQLite's
online backup API. Operator-only payment settings are listed in `.env.example`
and `docs/modules/PAYMENTS.md`; an administrator can also fill them in
`/admin` → 支付配置, which rewrites only those `.env` keys (mode 600) and
reloads the channels in place, so `configure.py` keeps them on every later run.
Missing real merchant credentials disable payment; production must never enable
mock payments. Do not test against live payment APIs.

Public payment methods and callbacks open directly; callbacks require official
signature verification. Account order and wallet routes require JWTs. Validate
Nginx with `nginx -t` before hot reload, and restart only the API for migrations;
model workers and existing secrets remain unchanged.

## Model services

`supervisord.conf` also starts the two local model services, last, so the site
answers as soon as the API and Nginx are up:

| Program | Service | Port | Weights |
|---|---|---|---|
| `itp-flux` | FLUX.2 Klein 4B | 8788 | `itp-flux-klein-service/hf-cache`, ~15 GB |
| `itp-faceverse` | FaceVerse V4 | 8787 | `itp-app/services/vendor/FaceVerse_v4/data`, ~276 MB |

Both run through `run-model-service.sh`, which waits for a `/dev/nvidia0`
device node before loading weights. A container booted in AutoDL no-GPU mode
therefore stays in a waiting state instead of crash-looping, and the service
starts by itself once the instance is rebooted with a GPU. Model loading needs
a GPU with enough VRAM for the offload mode in use: `ITP_KLEIN_OFFLOAD=model`
(the default here) peaks near one component and fits a 16 GB card, while
`none` holds all weights on the device and needs a larger allocation. Set
`ITP_KLEIN_MODEL_PATH` to a different snapshot directory after a re-download.

Check the services with:

```bash
/root/miniconda3/bin/supervisorctl -c /root/autodl-tmp/itp-app/deploy/autodl/supervisord.conf status
curl -s http://127.0.0.1:8788/health   # FLUX.2 Klein: ready is true once loaded
curl -s http://127.0.0.1:8787/health   # FaceVerse: reports cuda and the GPU name
```

`ready=false` (Flux) or a startup failure with `CUDA is not available` /
`FileNotFoundError` (FaceVerse) means the container has no GPU or the weights
are missing; the wait loop covers the former, so check `itp-data/*.log`.
