# AutoDL production deployment

This deployment uses the AutoDL HTTPS gateway (`:8443` -> container port `6006`),
Nginx Basic Authentication, one Uvicorn worker, and the image's supervised
`/etc/autodl.sh` boot hook. The app and credentials live under
`/root/autodl-tmp/itp-app` and `/root/autodl-tmp/itp-data` respectively.

`configure.py` preserves the configured Tencent, Qwen and SeedDream secrets
from the existing `.env`, sets an exact public HTTPS origin, and points the
Flux Klein and FaceVerse endpoints at the loopback model services on ports
8788 and 8787. It creates the two bearer tokens if they are absent, in root-only
files outside this repository (`itp-flux-klein-service/.token` and
`itp-data/faceverse.token`), and reads them back into `.env`.
It creates a random site password at `itp-data/access-password`; the username
is `itp`. Do not commit the passwords, the tokens, `.env`, or `access.htpasswd`.

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

Merchant registration and login also stay behind the site password. Only the
JWT-guarded `/api/merchant/me`, `/password`, `/garments` and `/looks` paths
(including their child paths) bypass Nginx Basic Authentication: FastAPI
requires their merchant Bearer token, because both authentication schemes would
otherwise compete for the same `Authorization` header. This exception does not
expose `/api/settings`, body profiles or the rest of the site. Invalid or absent
merchant tokens are rejected by FastAPI. The proxy allows up to 81 MiB on these
paths for eight 10 MiB images plus multipart overhead; the application still
enforces per-image, total-body and route-specific limits.

Unified `/api/auth/register`, `/login`, `/logout` and `/api/account/me`, `/password`
also bypass Basic Authentication to avoid the same header conflict. Registration
and login are rate-limited by the application; account routes validate JWTs and
merchant business routes additionally validate the database role. The new account
module adds a role column and token-revocation table to the existing credential
database without copying users or changing garment ownership. The main shared
workspace remains Basic-protected during the asset-isolation upgrade.

The merchant module creates `merchants.sqlite3` and its tables on application
startup; it does not replace the existing assets or job databases. Back up live
SQLite databases with the SQLite backup API before upgrading. `ITP_JWT_SECRET`
must persist across restarts: retain an existing value; if absent, add one
generated secret without replacing other settings. No extra supervisor program
or model service is needed for merchant accounts and size recommendations.

The current app still uses a shared asset database for authenticated visitors.
Provider configuration is operator-owned and cannot be modified through the
public site. Full customer account isolation, financial controls and personal
asset storage boundaries are separate commercial-upgrade modules; do not
interpret removal of the settings page as completion of those modules.

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
