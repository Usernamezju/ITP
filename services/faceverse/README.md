# FaceVerse V4 refinement service

## Current AutoDL container (2026-10-04)

The current ClothiNation deployment keeps this service at
`/root/autodl-tmp/itp-app/services/faceverse` with an isolated venv and
`libegl1`. The upstream source is checked out at commit
`19c67cc4d7234b1ea7d55a185a2cb55fd49bb877` under
`/root/autodl-tmp/itp-app/services/vendor/FaceVerse_v4`. Package imports,
upstream imports, Python syntax, FastAPI routes, and a loopback HTTP/OpenAPI
startup with `--lifespan off` were verified without GPU or inference.

The three model assets are **not present** in this container. Consequently,
normal lifespan startup and actual face refinement were not tested and the
ClothiNation provider remains disabled. The deployment path and successful inference
described below refer to a **previous container**, not this one.

This is the separate Ubuntu 22.04 / RTX 3080 Ti service for ClothiNation's `POST /v1/face-refine` protocol. It uses the actual FaceVerse V4 network and weights, MediaPipe face detection, mesh rendering/registration, surface deformation and GLB export. It does not change the independent body-generation path in ClothiNation. Never commit weights, face photos, tokens or generated meshes.

## Previous-container layout and installation

The service is deployed at `/root/itp-faceverse-service/app`; the pinned upstream code and model files are under `/root/itp-faceverse-service/vendor/FaceVerse_v4`. Source is pinned to FaceVerse V4 commit [`19c67cc4d7234b1ea7d55a185a2cb55fd49bb877`](https://github.com/LizhenWangT/FaceVerse_v4/tree/19c67cc4d7234b1ea7d55a185a2cb55fd49bb877). Required upstream Python files: `faceversev4/__init__.py`, `faceversev4/FaceVerse_networks.py`, and `faceversev4/FaceVerseModel_torch.py`. Required assets in `data/`: `faceverse_v4_2.npy`, `faceverse_resnet50.pth`, and `face_landmarker.task`.

The server already has NVIDIA driver 580.105.08 and CUDA-enabled PyTorch 2.8.0+cu128. `scripts/bootstrap.sh` creates an isolated venv using that PyTorch; it does **not** replace the driver or base Conda packages. Off-screen GL rendering also needs the Ubuntu package `libegl1`:

```bash
apt-get update && apt-get install -y libegl1
cd /root/itp-faceverse-service/app
bash scripts/bootstrap.sh
```

The bootstrap script installs dependencies from the Tsinghua PyPI mirror by default (`ITP_FACEVERSE_PYPI_INDEX` overrides it) and runs `scripts/doctor.py`. Doctor checks the GPU, CUDA tensor execution, Python packages, file sizes, and SHA-256 digests. Run it again with:

```bash
.venv/bin/python scripts/doctor.py --models-dir ../vendor/FaceVerse_v4/data
```

The authors distribute the two FaceVerse weights through OneDrive, which the target server could not reach. The deployed copies came from a [third-party GitHub release](https://github.com/Mrkomiljon/faceverse-onnx/releases/tag/v4.1.0). SHA-256 values in `scripts/doctor.py` verify the transferred copies, **not** equivalence to the authors' OneDrive originals. Structural checks and real-photo inference succeeded, but official provenance remains unverified. The MediaPipe `face_landmarker.task` came from the official [Google model bucket](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task).

## Start and connect

Bind to loopback for SSH tunneling (default). Set `ITP_FACEVERSE_API_TOKEN` to a private value when bearer authentication is required; the script refuses a non-loopback bind without a token:

```bash
cd /root/itp-faceverse-service/app
export ITP_FACEVERSE_API_TOKEN='your-private-token'
bash scripts/start.sh
```

`GET http://127.0.0.1:8787/health` returns the model and GPU readiness. To connect from the ClothiNation host, forward the port over the supplied SSH connection:

```bash
ssh -N -L 8787:127.0.0.1:8787 -i ~/.ssh/autodl -p 40292 root@connect.bjb2.seetacloud.com
```

Then enter `http://127.0.0.1:8787/v1/face-refine` in ClothiNation's FaceVerse endpoint setting, `faceverse-v4` as the model, and the same bearer token in ClothiNation's API-key setting. The token is intentionally not stored in this repository. For a public endpoint, add TLS and access control at a reverse proxy; ClothiNation intentionally rejects non-local plain HTTP.

## Pipeline and acceptance

The request contract is specified in [FACE_REFINEMENT.md](../../docs/modules/FACE_REFINEMENT.md). The service rejects invalid base64, oversize assets, non-GLB geometry, unsuitable photos, unsupported model or missing required operations. Each accepted request performs detection, FaceVerse reconstruction, orthographic four-direction body-head search, 3-D landmark similarity alignment, layered front-face cutting, boundary matching, sparse Laplacian deformation, conforming remesh, seam bridge/welding, vertex-colour fusion and a collision screening pass. It preserves geometry outside the detected front-face oval, including hair, rear skull and neck. The output is reloaded after GLB export to verify that it contains geometry.

The integration checks under `tests/` require real input files; they do not mock model inference. For example:

```bash
export PYOPENGL_PLATFORM=egl
.venv/bin/python tests/verify_reconstruction.py ../validation/test.jpg ../validation/reconstructed_face.glb --vendor-root ../vendor/FaceVerse_v4
.venv/bin/python tests/verify_full_pipeline.py ../validation/body.glb ../validation/test.jpg ../validation/refined.glb --vendor-root ../vendor/FaceVerse_v4
```

The report includes landmark RMS, seam distance, repaired loops, residual open edges and `collision_count`. The collision count is a **signed-nearest-surface screening metric**, not a certified self-intersection test. Visually review frontal and profile renders before using an asset; source photo and body must depict the same person. The tested example used an unrelated official sample photo and an existing ClothiNation body asset, so its identity similarity is not an acceptance result. Very low-quality source heads, undetectable rendered faces, large pose differences, accessories overlapping the face, and non-manifold meshes may be rejected or require manual cleanup. A tiny residual open edge remained in the tested sample; this is reported rather than hidden.
