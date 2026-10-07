# FLUX.2 Klein 4B / 9B FastAPI service

## Model selection

The same implementation supports two **independent** services. Set
`ITP_KLEIN_MODEL_ID=flux.2-klein-4b` (default, port 8788), or
`ITP_KLEIN_MODEL_ID=flux.2-klein-9b` (port 8789). `ITP_KLEIN_PORT` overrides
the port. Requests and `/health` identify the configured variant; a 9B
request cannot silently use 4B weights. Before loading weights, the service
checks the transformer architecture against the chosen variant.

The official [9B model](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B)
uses the FLUX non-commercial license, not 4B's Apache-2.0 license. Its
repository is gated: accept the license with your Hugging Face account and
provide `HF_TOKEN` through the private runtime environment when downloading.
The model card lists approximately 29 GB VRAM; CPU offload may reduce GPU
memory use but needs separate validation. No 9B inference or quality
validation has been performed as part of this integration.

For a separately provisioned 9B service, use the same bootstrap and start
scripts with `ITP_KLEIN_MODEL_ID=flux.2-klein-9b`. If setting
`ITP_KLEIN_MODEL_PATH`, it must reference a complete **9B Diffusers snapshot**,
not the existing 4B cache or a standalone FP8 checkpoint. Configure ClothiNation's
9B settings separately with endpoint
`http://127.0.0.1:8789/v1/flux-klein/edit`, model `flux.2-klein-9b`, and the
matching service token. `ITP_KLEIN_SERVICE_PYTHON` optionally selects an
existing compatible service interpreter instead of this directory's venv.

## Current AutoDL container (2026-10-04)

The existing service at `/root/autodl-tmp/itp-flux-klein-service` retains its
venv and about 15 GB of model cache. Its Python files, FastAPI import and
routes, and a loopback HTTP/OpenAPI startup with `--lifespan off` were
verified without loading the model. `/health` correctly returned
`ready=false`. The previously exposed bearer token was rotated, and ClothiNation's
Flux Klein provider is disabled until GPU inference is explicitly enabled
and validated. The live inference results described below belong to a
**previous deployment**, not this no-GPU verification.

This directory contains a real Diffusers-backed image-editing service for ClothiNation. It does not contain model weights. The model is [`black-forest-labs/FLUX.2-klein-4B`](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), licensed under Apache-2.0. The model card states roughly 13 GB VRAM for normal loading. The validated 32 GB RTX 4080 SUPER host runs without CPU offload; smaller hosts may need offload and separate validation.

## Server preparation

The service uses the existing CUDA-enabled PyTorch from `/root/miniconda3/bin/python` through an isolated `--system-site-packages` venv. Set `ITP_KLEIN_PYTHON` if that Python is elsewhere. Before running inference, inspect **all** GPUs with `nvidia-smi`, select a free device using `ITP_KLEIN_GPU`, and make sure the model cache has enough disk space. The bootstrap script uses the Tsinghua PyPI mirror by default; override `ITP_KLEIN_PYPI_INDEX` as needed. It checks CUDA, available GPUs, package versions and the Diffusers pipeline class, but does not claim that inference succeeds.

From the service directory on the server:

```bash
bash scripts/bootstrap.sh
export ITP_KLEIN_GPU=0
export ITP_KLEIN_OFFLOAD=none
export ITP_KLEIN_API_TOKEN='set-a-private-token'
bash scripts/start.sh
```

`ITP_KLEIN_OFFLOAD` accepts `sequential` (the default and slowest), `model`, or `none` (the validated 32 GB setting). Set `ITP_KLEIN_MODEL_PATH` to an existing absolute model snapshot directory, or leave it unset to let Diffusers download into the Hugging Face cache. `HF_ENDPOINT` may be set to an accessible compatible mirror; verify its provenance. The token above is only an example: use a private value and never commit it.

The server binds to `127.0.0.1:8788` only. With an SSH tunnel, set ClothiNation's Klein endpoint to `http://127.0.0.1:8788/v1/flux-klein/edit` and enter the same token in the ClothiNation settings page. A public deployment requires an authenticated HTTPS reverse proxy; `scripts/start.sh` intentionally refuses a public bind.

## Contract and validation

- `GET /health` returns `ready`, `model` and a non-sensitive error type. `ready=true` means weights loaded; it does **not** prove visual quality or four-reference inference.
- `POST /v1/flux-klein/edit` requires `Authorization: Bearer ...` and JSON `{ "model": "flux.2-klein-4b", "prompt": "...", "images": ["data:image/jpeg;base64,..."] }`. Supply 1–4 images. It returns `{ "model": "flux.2-klein-4b", "data": [{ "b64_json": "..." }] }` with a PNG result. For a 9B service, both model fields are `flux.2-klein-9b`.
- Each input is limited to 10 MiB and validated as a still PNG, JPEG or WebP. The service runs the official 4-step distilled pipeline at 768×1024 and serializes GPU inference. Model errors are logged server-side without returning user images or tokens.

## Deployment validation (2026-10-03)

On the current 32 GB RTX 4080 SUPER host, the service loaded model snapshot `e7b7dc27f91deacad38e78976d1f2b499d76a294` with PyTorch 2.8.0+cu128 and Diffusers 0.40.0. Only the 18 Diffusers component files were downloaded (about 15 GB in the cache); the separate monolithic checkpoint was omitted. The server could not reach `huggingface.co` directly, so `hf-mirror.com` was used. The mirror's provenance was not independently checked against upstream file hashes.

The live `/health` response reported `ready=true`. Real `POST /v1/flux-klein/edit` requests with one and four references each returned HTTP 200 and valid 768×1024 PNG images. A request without a Bearer token returned HTTP 401. The one-reference cat-to-dog edit visibly changed the subject while retaining a similar composition. The input and both outputs are retained on the server under `/root/autodl-tmp/itp-flux-klein-service/validation/`. This verifies image generation and the four-reference API path, **not** identity consistency or garment fidelity on real virtual-try-on photographs; those need representative user images for evaluation.
