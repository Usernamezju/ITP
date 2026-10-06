import asyncio
import json
import os
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from itp.config import BFL_PROVIDERS, KLEIN_PROVIDERS, Settings
from itp.face_refine import (
    FaceRefineRequest,
    FaceRefineStore,
    FaceRefineWorker,
    prepare_face_photo,
)
from itp.garments import (
    MAX_GARMENT_IMAGES,
    STATUSES,
    AlreadyExists,
    MerchantStore,
    QuotaExceeded,
    normalize_body_profile,
    normalize_look,
    normalize_metrics,
    options_document,
    public_body_profile,
    public_garment,
    public_image,
    public_look,
    public_merchant,
)
from itp.merchant_auth import (
    DUMMY_PASSWORD_HASH,
    current_merchant,
    encode_token,
    hash_password,
    resolve_jwt_secret,
    verify_password,
)
from itp.outfit_images import (
    IMAGE_DEFAULT_LIMIT,
    IMAGE_MAX_LIMIT,
    IMAGE_MAX_PAGE,
    IMAGE_MIN_LIMIT,
    IMAGE_MIN_PAGE,
    cached_image_path,
    content_type_for,
    resolve_provider,
    search_outfit_images,
    validate_provider_choice,
)
from itp.outfit_service import recommend as recommend_outfits
from itp.pipeline import Pipeline
from itp.preprocessing import MAX_UPLOAD, Segmenter, image_base64, prepare_image
from itp.provider_settings import (
    ProviderSettingsUpdate,
    public_provider_settings,
    save_provider_settings,
    validate_provider_update,
)
from itp.schemas import JobRequest
from itp.storage import Store, public_asset, public_job
from itp.tryon import VIEWS, TryOnRequest, TryOnStore, TryOnWorker
from itp.wardrobe import CATALOG, DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT


class ReviewRequest(BaseModel):
    approve: bool


# --- merchant accounts -------------------------------------------------------

MERCHANT_NAME = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")

# Multipart bodies may carry up to eight images, so the merchant upload routes
# get a larger (still bounded) cap than the single-image routes.  Existing
# paths keep the original limit.
UPLOAD_PATHS = {"/api/assets", "/api/face-photos", "/api/merchant/garments"}
MERCHANT_IMAGE_PATH = re.compile(r"^/api/merchant/garments/[0-9a-zA-Z]+/images$")
MERCHANT_BODY_LIMIT = MAX_GARMENT_IMAGES * MAX_UPLOAD + 65536
DEFAULT_BODY_LIMIT = MAX_UPLOAD + 65536


class MerchantRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    display_name: str
    contact: str = ""
    password: str


class MerchantLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    password: str


class MerchantPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: str


PASSWORD_RULE = "密码长度需为 8-128 位"


def check_password(text: str) -> str:
    """The one password rule, shared by registration and password changes."""
    if not 8 <= len(text) <= 128:
        raise HTTPException(422, PASSWORD_RULE)
    return text


def merchant_register_fields(body: MerchantRegisterRequest) -> dict[str, str]:
    """Validate a registration payload, with Chinese reasons for every rule."""
    name = body.name.strip()
    if not MERCHANT_NAME.match(name):
        raise HTTPException(422, "商家账号需为 3-32 位字母、数字、下划线或短横线")
    display_name = body.display_name.strip()
    if not 1 <= len(display_name) <= 40:
        raise HTTPException(422, "商家名称需为 1-40 个字符")
    contact = body.contact.strip()
    if len(contact) > 80:
        raise HTTPException(422, "联系方式不能超过 80 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in f"{display_name}{contact}"):
        raise HTTPException(422, "商家名称或联系方式含有不可见控制字符")
    check_password(body.password)
    return {"name": name, "display_name": display_name, "contact": contact}


def parse_metrics_payload(raw: str) -> dict:
    """Decode the JSON ``payload`` part of a garment upload."""
    try:
        document = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "payload 必须是合法的 JSON 对象") from exc
    if not isinstance(document, dict):
        raise HTTPException(422, "payload 必须是合法的 JSON 对象")
    try:
        return normalize_metrics(document)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def parse_json_body(document: dict, normalizer, *, base: dict | None = None) -> dict:
    try:
        return normalizer(document, base)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def read_garment_images(images: list[UploadFile]) -> list:
    """Validate and normalize every uploaded image before anything is stored."""
    if len(images) > MAX_GARMENT_IMAGES:
        raise HTTPException(422, f"最多上传 {MAX_GARMENT_IMAGES} 张图片")
    prepared = []
    for upload in images:
        try:
            data = await upload.read(MAX_UPLOAD + 1)
        finally:
            await upload.close()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "单张图片不能超过 10 MiB")
        if not data:
            raise HTTPException(422, "图片内容为空")
        try:
            prepared.append(await asyncio.to_thread(prepare_image, data))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return prepared


# Outfit photo search settings.  ``ProviderSettingsUpdate`` forbids unknown keys
# and is owned by another branch of this feature, so the three image fields are
# declared on a subclass here instead of extending that model.
IMAGE_SETTING_FIELDS = ("image_provider", "unsplash_access_key", "pixabay_api_key")
_ENV_KEY = re.compile(r"^\s*(?:export\s+)?(ITP_[A-Z0-9_]+)\s*=")


class ImageSettingsUpdate(ProviderSettingsUpdate):
    """Provider settings plus the outfit photo search fields."""

    model_config = ConfigDict(extra="forbid")

    image_provider: str | None = Field(default=None, max_length=32)
    unsplash_access_key: str | None = Field(default=None, max_length=1024)
    pixabay_api_key: str | None = Field(default=None, max_length=1024)


def image_setting_changes(body: ImageSettingsUpdate) -> dict[str, str]:
    """Pull the outfit photo fields out of a patch, validating each value."""
    changes = {
        field: value
        for field in IMAGE_SETTING_FIELDS
        if (value := getattr(body, field)) is not None
    }
    for field, value in changes.items():
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{field} contains invalid control characters")
    if "image_provider" in changes:
        changes["image_provider"] = validate_provider_choice(changes["image_provider"])
    return changes


def public_settings(settings: Settings) -> dict:
    """The settings document, extended with the outfit photo fields.

    Secrets are reported as booleans only, never echoed back.
    """
    return public_provider_settings(settings) | {
        "image_provider": settings.image_provider,
        "unsplash_access_key_set": bool(settings.unsplash_access_key.get_secret_value()),
        "pixabay_api_key_set": bool(settings.pixabay_api_key.get_secret_value()),
    }


def save_image_settings(path: Path, changes: dict) -> None:
    """Write the outfit photo keys into ``.env`` beside the other settings.

    ``save_provider_settings`` rewrites only the fields in its own editable
    list, which these keys are deliberately not part of, so this repeats its
    safe rewrite: drop the old lines for exactly these keys, append the new
    ones, then replace the file atomically with owner-only permissions.
    """
    if not changes:
        return
    if path.is_symlink():
        raise OSError("Refusing to replace a symlinked settings file")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    keys = {f"ITP_{field.upper()}" for field in changes}
    lines = [
        line
        for line in original.splitlines(keepends=True)
        if not (match := _ENV_KEY.match(line)) or match.group(1) not in keys
    ]
    content = "".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    for field in IMAGE_SETTING_FIELDS:
        if field in changes:
            content += f"ITP_{field.upper()}={json.dumps(changes[field], ensure_ascii=False)}\n"
    fd, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def create_app(
    settings: Settings | None = None,
    *,
    start_worker: bool = True,
    config_path: Path = Path(".env"),
) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.data_dir)
    segmenter = Segmenter(settings.segmentation_model)
    pipeline = Pipeline(store, settings)
    tryons = TryOnStore(store.root)
    tryon_worker = TryOnWorker(store, tryons, settings)
    face_jobs = FaceRefineStore(store.root)
    face_worker = FaceRefineWorker(store, face_jobs, settings)
    merchants = MerchantStore(store.root)
    settings_lock = threading.Lock()
    face_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        lock = FileLock(str(store.root / "worker.lock"))
        thread = None
        tryon_thread = None
        face_thread = None
        if start_worker:
            try:
                lock.acquire(timeout=0)
            except Timeout as exc:
                raise RuntimeError(
                    "ITP already uses this data directory; run one worker only"
                ) from exc
            thread = threading.Thread(target=pipeline.run_forever, daemon=True, name="itp-worker")
            thread.start()
            tryon_thread = threading.Thread(
                target=tryon_worker.run_forever, daemon=True, name="itp-tryon-worker"
            )
            tryon_thread.start()
            face_thread = threading.Thread(
                target=face_worker.run_forever, daemon=True, name="itp-face-worker"
            )
            face_thread.start()
        try:
            yield
        finally:
            if thread:
                pipeline.stop.set()
                tryon_worker.stop.set()
                face_worker.stop.set()
                await asyncio.to_thread(thread.join)
                await asyncio.to_thread(tryon_thread.join)
                await asyncio.to_thread(face_thread.join)
                lock.release()

    app = FastAPI(title="ITP Studio API", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.tryons = tryons
    app.state.tryon_worker = tryon_worker
    app.state.face_jobs = face_jobs
    app.state.face_worker = face_worker
    app.state.merchants = merchants
    app.state.config_path = config_path
    app.state.settings = settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    def flux_klein_health(provider: str = "flux_klein") -> dict:
        current = app.state.settings
        model = current.tryon_model_for(provider)
        if not current.tryon_provider_ready(provider):
            return {"ready": False, "model": model}
        endpoint = getattr(current, f"{provider}_endpoint")
        token = getattr(current, f"{provider}_api_key").get_secret_value()
        url = endpoint.removesuffix("/v1/flux-klein/edit") + "/health"
        try:
            with httpx.Client(timeout=3, follow_redirects=False, trust_env=False) as client:
                response = client.get(url, headers={
                    "Authorization": f"Bearer {token}"
                })
                response.raise_for_status()
                body = response.json()
                ready = body.get("ready") is True and body.get("model") == model
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            ready = False
        return {"ready": ready, "model": model}

    @app.exception_handler(RequestValidationError)
    async def redact_settings_validation(request: Request, exc: RequestValidationError):
        if request.url.path == "/api/settings":
            return JSONResponse({"detail": "配置项无效，请检查输入内容"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        allowed = {
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        if request.headers.get("host"):
            allowed.add(f"{request.url.scheme}://{request.headers['host']}")
        if app.state.settings.public_origin:
            allowed.add(app.state.settings.public_origin)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in allowed:
            return JSONResponse({"detail": "仅允许本地工作台请求"}, status_code=403)
        # Normal browser uploads include Content-Length; route code also bounds the actual image.
        size = request.headers.get("content-length")
        is_upload = request.url.path in UPLOAD_PATHS or bool(
            MERCHANT_IMAGE_PATH.match(request.url.path)
        )
        if request.method == "POST" and is_upload and size is None:
            return JSONResponse({"detail": "上传图片需要 Content-Length 请求头"}, status_code=411)
        if size:
            limit = MERCHANT_BODY_LIMIT if is_upload else DEFAULT_BODY_LIMIT
            if not size.isdigit() or int(size) > limit:
                return JSONResponse({"detail": "请求体过大或长度无效"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/api/settings":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/capabilities")
    def capabilities():
        current = app.state.settings
        return {
            "geometry": current.geometry_ready,
            "pose": current.pose_ready,
            "tryon": current.tryon_ready,
            "tryon_model": current.seedream_model,
            "tryon_providers": {
                name: current.tryon_provider_ready(name)
                for name in ("seedream", *BFL_PROVIDERS, *KLEIN_PROVIDERS, "gpt_image")
            },
            "faceverse": current.faceverse_ready,
            "faceverse_model": current.faceverse_model,
            "segmentation": current.segmentation_model.is_file(),
            "outfit_images": True,
            "image_provider": resolve_provider(current)[0],
            "provider": "腾讯云混元 AI3D（国内）",
            "pose_provider": "千问图像编辑（国内）",
            "model": current.tencent_model,
            "pose_model": current.pose_model,
            "max_upload_mb": 10,
        }

    @app.get("/api/tryon-providers/flux-klein/health")
    def get_flux_klein_health():
        return flux_klein_health()

    @app.get("/api/tryon-providers/flux-klein-9b/health")
    def get_flux_klein_9b_health():
        return flux_klein_health("flux_klein_9b")

    @app.get("/api/settings")
    def get_provider_settings():
        return public_settings(app.state.settings)

    @app.patch("/api/settings")
    def update_provider_settings(body: ImageSettingsUpdate):
        with settings_lock:
            provided = body.model_dump(exclude_unset=True)
            try:
                base = ProviderSettingsUpdate(
                    **{
                        key: value
                        for key, value in provided.items()
                        if key not in IMAGE_SETTING_FIELDS
                    }
                )
                image_changes = image_setting_changes(body)
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, "配置项无效，请检查服务地址和输入内容") from exc
            try:
                updated, changes = validate_provider_update(app.state.settings, base)
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, "配置项无效，请检查服务地址和输入内容") from exc
            if any(
                f"ITP_{field.upper()}" in os.environ
                for field in [*changes, *image_changes]
            ):
                raise HTTPException(409, "该配置已由进程环境变量指定，请在启动环境中修改")
            try:
                save_provider_settings(config_path, changes)
                save_image_settings(config_path, image_changes)
            except OSError as exc:
                raise HTTPException(500, "无法保存配置文件，请检查文件权限") from exc
            if image_changes:
                values = updated.model_dump()
                values.update(image_changes)
                updated = Settings(_env_file=None, **values)
            app.state.settings = updated
            pipeline.settings = updated
            pipeline.cloud.settings = updated
            pipeline.pose.settings = updated
            tryon_worker.settings = updated
            tryon_worker.provider.settings = updated
            for provider in tryon_worker.providers.values():
                provider.settings = updated
            face_worker.settings = updated
            face_worker.provider.settings = updated
            return public_settings(updated)

    @app.post("/api/assets", status_code=201)
    async def upload(file: UploadFile = File(...), remove_background: bool = Query(False)):
        try:
            data = await file.read(MAX_UPLOAD + 1)
        finally:
            await file.close()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "图片不能超过 10 MiB")
        if remove_background and not app.state.settings.segmentation_model.is_file():
            raise HTTPException(503, "去背景权重尚未安装")
        try:
            image = await asyncio.to_thread(
                prepare_image, data, segmenter if remove_background else None
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        asset_id, path = store.new_asset_path("png")
        image.save(path, format="PNG")
        asset = store.add_asset(
            asset_id,
            path,
            "image",
            width=image.width,
            height=image.height,
            background_removed=remove_background,
        )
        return public_asset(asset)

    @app.post("/api/face-photos", status_code=201)
    async def upload_face_photo(file: UploadFile = File(...)):
        try:
            data = await file.read(MAX_UPLOAD + 1)
        finally:
            await file.close()
        try:
            image = await asyncio.to_thread(prepare_face_photo, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        asset_id, path = store.new_asset_path("png")
        image.save(path, format="PNG")
        return public_asset(store.add_asset(
            asset_id, path, "face_photo", width=image.width, height=image.height
        ))

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str):
        asset = store.asset(asset_id)
        if not asset:
            raise HTTPException(404, "资产不存在")
        return public_asset(asset)

    @app.get("/api/assets/{asset_id}/file")
    def get_file(asset_id: str, download: bool = False):
        asset = store.asset(asset_id)
        if not asset:
            raise HTTPException(404, "资产不存在")
        path = store.path(asset_id)
        if not path.is_file():
            raise HTTPException(404, "资产文件已丢失")
        media = {"GLB": "model/gltf-binary", "PNG": "image/png"}.get(
            asset.get("format", "PNG"), "application/octet-stream"
        )
        return FileResponse(path, media_type=media, filename=path.name if download else None)

    @app.get("/api/jobs")
    def list_jobs():
        return [public_job(j) for j in store.jobs()]

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobRequest):
        ids = [body.front, *body.views.values()]
        if body.pose_reference:
            ids.append(body.pose_reference)
        for asset_id in ids:
            asset = store.asset(asset_id)
            if not asset or asset["kind"] != "image" or not store.path(asset_id).is_file():
                raise HTTPException(422, "输入图片不存在，请重新上传")
        current = app.state.settings
        if current.tencent_model == "3.0" and any(
            view in body.views for view in ("left_front", "right_front")
        ):
            raise HTTPException(422, "左前/右前视图需要腾讯云混元 3D 3.1")
        encoded_total = sum(
            len(image_base64(store.path(asset_id)))
            for asset_id in [body.front, *body.views.values()]
        )
        if encoded_total > 8 * 1024 * 1024:
            raise HTTPException(422, "多视图图片编码后超过腾讯云 8 MiB 限制，请压缩后重试")
        if not current.geometry_ready:
            raise HTTPException(503, "腾讯云 API 待配置；请在设置页填写")
        if body.pose_mode != "original" and not current.pose_ready:
            raise HTTPException(503, "姿势编辑 API 待配置；请在设置页填写")
        models = {"geometry": current.tencent_model}
        if body.pose_mode != "original":
            models["pose"] = current.pose_model
        return public_job(store.create_job(body.model_dump(), models=models))

    @app.get("/api/tryons")
    def list_tryons():
        return tryons.list()

    @app.post("/api/tryons", status_code=201)
    def create_tryon(body: TryOnRequest):
        try:
            body.validate_views()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        for asset_id in [*body.person.values(), *body.garment.values()]:
            asset = store.asset(asset_id)
            if not asset or asset["kind"] != "image" or not store.path(asset_id).is_file():
                raise HTTPException(422, "输入图片不存在，请重新上传")
        if not app.state.settings.tryon_provider_ready(body.provider):
            raise HTTPException(503, "所选生图模型 API 待配置；请在设置页填写")
        if body.provider in KLEIN_PROVIDERS and not flux_klein_health(body.provider)["ready"]:
            variant = "9B" if body.provider == "flux_klein_9b" else "4B"
            raise HTTPException(503, f"FLUX.2 Klein {variant} 服务尚未就绪；请检查健康状态")
        return tryons.create(body, app.state.settings.tryon_model_for(body.provider))

    @app.get("/api/tryons/{tryon_id}")
    def get_tryon(tryon_id: str):
        job = tryons.get(tryon_id)
        if not job:
            raise HTTPException(404, "试穿任务不存在")
        return job

    @app.post("/api/tryons/{tryon_id}/continue", status_code=201)
    def continue_tryon(tryon_id: str):
        tryon = tryons.get(tryon_id)
        if not tryon:
            raise HTTPException(404, "试穿任务不存在")
        if tryon["state"] != "ready" or set(tryon["results"]) != set(VIEWS):
            raise HTTPException(409, "六张试穿结果尚未生成完成")
        current = app.state.settings
        if not current.geometry_ready:
            raise HTTPException(503, "腾讯云 API 待配置；请在设置页填写")
        results = tryon["results"]
        request = JobRequest(name=tryon["name"], front=results["front"],
                             views={view: results[view] for view in VIEWS if view != "front"},
                             views_consistent_confirmed=True)
        return public_job(store.create_job(request.model_dump()))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = store.job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return public_job(job)

    @app.get("/api/jobs/{job_id}/face-refinement")
    def get_face_refinements(job_id: str):
        if not store.job(job_id):
            raise HTTPException(404, "任务不存在")
        return face_jobs.for_job(job_id)

    @app.post("/api/jobs/{job_id}/face-refinement", status_code=201)
    def create_face_refinement(job_id: str, body: FaceRefineRequest):
        source = store.job(job_id)
        if not source:
            raise HTTPException(404, "任务不存在")
        if source["state"] != "succeeded":
            raise HTTPException(409, "请等待 3D 模型生成完成")
        photo = store.asset(body.face_photo)
        if not photo or photo["kind"] != "face_photo" or not store.path(body.face_photo).is_file():
            raise HTTPException(422, "请上传原始高清正面人物照片")
        if not app.state.settings.faceverse_ready:
            raise HTTPException(503, "FaceVerse 服务器待配置；请在设置页填写")
        meshes = [artifact for artifact in source["artifacts"]
                  if artifact["format"] == "GLB" and artifact["stage"] != "face_refine"]
        if not meshes:
            raise HTTPException(409, "当前任务尚无可精修的 GLB 模型")
        with face_lock:
            if any(item["state"] in {"queued", "running", "submitting"}
                   for item in face_jobs.for_job(job_id)):
                raise HTTPException(409, "该模型已有进行中的脸部精修任务")
            return face_jobs.create(job_id, body.face_photo, meshes[-1]["asset_id"],
                                    app.state.settings.faceverse_model)

    @app.post("/api/jobs/{job_id}/review")
    def review_job(job_id: str, body: ReviewRequest):
        if body.approve and not app.state.settings.geometry_ready:
            raise HTTPException(503, "腾讯云 API 待配置，不能继续生成")
        try:
            return public_job(store.review(job_id, body.approve))
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/outfits")
    def list_outfits(
        job_id: str | None = Query(None),
        asset_id: str | None = Query(None),
        style: str | None = Query(None),
        season: str | None = Query(None),
        occasion: str | None = Query(None),
        limit: int = Query(DEFAULT_LIMIT, ge=MIN_LIMIT, le=MAX_LIMIT),
    ):
        # Wardrobe advice never fails: an unknown, unreadable or unsupported
        # model degrades to the generic catalogue instead of raising.  Published
        # merchant items are scored against the body's size ranges first; the
        # built-in catalogue only answers when there is nothing published yet.
        return recommend_outfits(
            store,
            merchants,
            job_id=job_id,
            asset_id=asset_id,
            style=style,
            season=season,
            occasion=occasion,
            limit=limit,
        )

    @app.get("/api/outfits/{outfit_id}/images")
    def list_outfit_images(
        outfit_id: str,
        limit: int = Query(IMAGE_DEFAULT_LIMIT, ge=IMAGE_MIN_LIMIT, le=IMAGE_MAX_LIMIT),
        page: int = Query(IMAGE_MIN_PAGE, ge=IMAGE_MIN_PAGE, le=IMAGE_MAX_PAGE),
        refresh: bool = Query(False),
    ):
        # Searching never raises: a failing source answers 200 with an empty
        # list and a Chinese reason, so the page keeps its placeholder art.
        outfit = next((entry for entry in CATALOG if entry["id"] == outfit_id), None)
        if outfit is None:
            raise HTTPException(404, "穿搭方案不存在")
        return search_outfit_images(
            outfit,
            limit=limit,
            page=page,
            refresh=refresh,
            settings=app.state.settings,
        )

    @app.get("/api/outfit-images/{name}")
    def get_outfit_image(name: str):
        # Only the SHA-1 names this service wrote are served; separators and
        # parent references are refused before the path is built.
        path = cached_image_path(app.state.settings, name)
        if path is None:
            raise HTTPException(404, "图片不存在")
        return FileResponse(
            path,
            media_type=content_type_for(path.name),
            headers={"Cache-Control": "public, max-age=604800"},
        )

    # --- merchant accounts ---------------------------------------------------

    def look_document(look: dict, *, published_only: bool) -> dict:
        """A look plus its member garments and their images.

        The public endpoints expose only published members, so a published look
        can never leak a draft product.
        """
        members = merchants.garments_by_ids(look["items"], published_only=published_only)
        gallery = merchants.images_for_many([item["id"] for item in members])
        return public_look(look, members, gallery)

    @app.post("/api/merchant/register", status_code=201)
    def merchant_register(body: MerchantRegisterRequest):
        fields = merchant_register_fields(body)
        try:
            merchant = merchants.create_merchant(
                **fields,
                password_hash=hash_password(body.password),
                quota=app.state.settings.merchant_quota,
            )
        except AlreadyExists as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "merchant_id": merchant["id"],
            "name": merchant["name"],
            "display_name": merchant["display_name"],
        }

    @app.post("/api/merchant/login")
    def merchant_login(body: MerchantLoginRequest):
        merchant = merchants.merchant_by_name(body.name.strip())
        stored = merchant["password_hash"] if merchant else DUMMY_PASSWORD_HASH
        accepted = verify_password(body.password, stored)
        if not merchant or not accepted:
            raise HTTPException(401, "账号或密码不正确")
        if merchant["disabled"]:
            raise HTTPException(403, "该商家账号已被禁用")
        token, expires_in = encode_token(
            resolve_jwt_secret(app),
            merchant["id"],
            hours=app.state.settings.merchant_token_hours,
            # Tags the token with this password, so changing it revokes the token.
            password_hash=merchant["password_hash"],
        )
        return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}

    @app.get("/api/merchant/me")
    def merchant_me(merchant: dict = Depends(current_merchant)):
        return public_merchant(merchant, garment_count=merchants.count_garments(merchant["id"]))

    @app.post("/api/merchant/password")
    def merchant_change_password(
        body: MerchantPasswordRequest, merchant: dict = Depends(current_merchant)
    ):
        """Change the signed-in merchant's own password.

        The current password is required even though the caller already holds a
        token, so a borrowed browser session cannot lock the owner out.  Changing
        it revokes every token issued before now — the response says so, and the
        client is expected to sign in again.
        """
        if not verify_password(body.current_password, merchant["password_hash"]):
            raise HTTPException(401, "当前密码不正确")
        check_password(body.new_password)
        if body.new_password == body.current_password:
            raise HTTPException(422, "新密码不能与当前密码相同")
        merchants.set_password(merchant["id"], hash_password(body.new_password))
        return {"changed": True, "tokens_revoked": True}

    @app.post("/api/merchant/garments", status_code=201)
    async def merchant_create_garment(
        payload: str = Form(...),
        images: list[UploadFile] | None = File(default=None),
        merchant: dict = Depends(current_merchant),
    ):
        metrics = parse_metrics_payload(payload)
        prepared = await read_garment_images(images or [])
        try:
            garment = merchants.create_garment(merchant["id"], metrics)
        except QuotaExceeded as exc:
            raise HTTPException(409, str(exc)) from exc
        except AlreadyExists as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        stored = []
        try:
            for image in prepared:
                asset_id, path = store.new_asset_path("png")
                await asyncio.to_thread(image.save, path, "PNG")
                store.add_asset(
                    asset_id, path, "garment_image", width=image.width, height=image.height
                )
                stored.append(merchants.add_image(garment["id"], asset_id))
        except Exception:
            merchants.delete_garment(merchant["id"], garment["id"])
            for item in stored:
                asset = store.asset(item["asset_id"])
                if asset:
                    (store.root / "assets" / asset["filename"]).unlink(missing_ok=True)
            raise
        return public_garment(garment, merchants.images_for(garment["id"]))

    @app.get("/api/merchant/garments")
    def merchant_list_garments(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        status: str | None = Query(None),
        merchant: dict = Depends(current_merchant),
    ):
        if status is not None and status not in STATUSES:
            raise HTTPException(422, "status 取值必须是：draft、published")
        total, items = merchants.list_garments(
            merchant["id"], limit=limit, offset=offset, status=status
        )
        gallery = merchants.images_for_many([item["id"] for item in items])
        return {
            "total": total,
            "items": [public_garment(item, gallery[item["id"]]) for item in items],
        }

    @app.get("/api/merchant/garments/{garment_id}")
    def merchant_get_garment(garment_id: str, merchant: dict = Depends(current_merchant)):
        garment = merchants.garment_for(merchant["id"], garment_id)
        if not garment:
            raise HTTPException(404, "商品不存在")
        return public_garment(garment, merchants.images_for(garment_id))

    @app.patch("/api/merchant/garments/{garment_id}")
    def merchant_update_garment(
        garment_id: str, body: dict, merchant: dict = Depends(current_merchant)
    ):
        garment = merchants.garment_for(merchant["id"], garment_id)
        if not garment:
            raise HTTPException(404, "商品不存在")
        metrics = parse_json_body(body, normalize_metrics, base=garment["metrics"])
        try:
            updated = merchants.update_garment(merchant["id"], garment_id, metrics)
        except AlreadyExists as exc:
            raise HTTPException(409, str(exc)) from exc
        if not updated:
            raise HTTPException(404, "商品不存在")
        return public_garment(updated, merchants.images_for(garment_id))

    @app.delete("/api/merchant/garments/{garment_id}", status_code=204)
    def merchant_delete_garment(garment_id: str, merchant: dict = Depends(current_merchant)):
        asset_ids = merchants.delete_garment(merchant["id"], garment_id)
        if asset_ids is None:
            raise HTTPException(404, "商品不存在")
        for asset_id in asset_ids:
            asset = store.asset(asset_id)
            if asset:
                (store.root / "assets" / asset["filename"]).unlink(missing_ok=True)
        return None

    @app.post("/api/merchant/garments/{garment_id}/images", status_code=201)
    async def merchant_add_garment_images(
        garment_id: str,
        images: list[UploadFile] | None = File(default=None),
        merchant: dict = Depends(current_merchant),
    ):
        garment = merchants.garment_for(merchant["id"], garment_id)
        if not garment:
            raise HTTPException(404, "商品不存在")
        prepared = await read_garment_images(images or [])
        if not prepared:
            raise HTTPException(422, "请至少上传一张图片")
        for image in prepared:
            asset_id, path = store.new_asset_path("png")
            await asyncio.to_thread(image.save, path, "PNG")
            store.add_asset(asset_id, path, "garment_image", width=image.width, height=image.height)
            merchants.add_image(garment_id, asset_id)
        return [public_image(item) for item in merchants.images_for(garment_id)]

    @app.delete("/api/merchant/garments/{garment_id}/images/{image_id}", status_code=204)
    def merchant_delete_garment_image(
        garment_id: str, image_id: str, merchant: dict = Depends(current_merchant)
    ):
        asset_id = merchants.delete_image(merchant["id"], garment_id, image_id)
        if asset_id is None:
            raise HTTPException(404, "图片不存在")
        asset = store.asset(asset_id)
        if asset:
            (store.root / "assets" / asset["filename"]).unlink(missing_ok=True)
        return None

    @app.post("/api/merchant/looks", status_code=201)
    def merchant_create_look(body: dict, merchant: dict = Depends(current_merchant)):
        look = parse_json_body(body, normalize_look)
        try:
            created = merchants.create_look(merchant["id"], look)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return look_document(created, published_only=False)

    @app.get("/api/merchant/looks")
    def merchant_list_looks(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        merchant: dict = Depends(current_merchant),
    ):
        total, looks = merchants.list_looks(merchant["id"], limit=limit, offset=offset)
        return {
            "total": total,
            "items": [look_document(item, published_only=False) for item in looks],
        }

    @app.patch("/api/merchant/looks/{look_id}")
    def merchant_update_look(
        look_id: str, body: dict, merchant: dict = Depends(current_merchant)
    ):
        existing = merchants.look_for(merchant["id"], look_id)
        if not existing:
            raise HTTPException(404, "穿搭不存在")
        look = parse_json_body(body, normalize_look, base=existing)
        try:
            updated = merchants.update_look(merchant["id"], look_id, look)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not updated:
            raise HTTPException(404, "穿搭不存在")
        return look_document(updated, published_only=False)

    @app.delete("/api/merchant/looks/{look_id}", status_code=204)
    def merchant_delete_look(look_id: str, merchant: dict = Depends(current_merchant)):
        if not merchants.delete_look(merchant["id"], look_id):
            raise HTTPException(404, "穿搭不存在")
        return None

    @app.put("/api/body-profile")
    def put_body_profile(body: dict):
        # Single-user local data for the 人体建模 page: like /api/jobs and
        # /api/assets it is protected by the loopback-only binding, not by a
        # merchant token.
        if not isinstance(body, dict):
            raise HTTPException(422, "人体参数必须是 JSON 对象")
        payload = dict(body)
        job_id = payload.pop("job_id", None)
        if job_id is not None:
            if not isinstance(job_id, str) or not job_id.strip():
                raise HTTPException(422, "job_id 必须是非空字符串")
            job_id = job_id.strip()
        base = merchants.body_profile(job_id, fallback=False)
        profile = parse_json_body(payload, normalize_body_profile, base=base)
        return public_body_profile(merchants.save_body_profile(profile, job_id), job_id)

    @app.get("/api/body-profile")
    def get_body_profile(job_id: str | None = Query(None)):
        return public_body_profile(merchants.body_profile(job_id), job_id)

    # --- public catalogue ----------------------------------------------------

    @app.get("/api/garment-options")
    def garment_options():
        """Reference data for the import form, published like the catalogue.

        It carries the vocabulary and the bounds the metrics validator enforces,
        so a merchant client can build the form and check it locally instead of
        guessing and collecting 422s.
        """
        return options_document()

    @app.get("/api/garments")
    def list_public_garments(
        style: str | None = Query(None),
        season: str | None = Query(None),
        occasion: str | None = Query(None),
        category: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        total, items = merchants.list_published_garments(
            style=style,
            season=season,
            occasion=occasion,
            category=category,
            limit=limit,
            offset=offset,
        )
        gallery = merchants.images_for_many([item["id"] for item in items])
        return {
            "total": total,
            "items": [public_garment(item, gallery[item["id"]]) for item in items],
        }

    @app.get("/api/garments/{garment_id}")
    def get_public_garment(garment_id: str):
        garment = merchants.garment(garment_id)
        if not garment or garment["status"] != "published":
            raise HTTPException(404, "商品不存在")
        return public_garment(garment, merchants.images_for(garment_id))

    @app.get("/api/looks")
    def list_public_looks(
        style: str | None = Query(None),
        season: str | None = Query(None),
        occasion: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        total, looks = merchants.list_published_looks(
            style=style, season=season, occasion=occasion, limit=limit, offset=offset
        )
        return {
            "total": total,
            "items": [look_document(item, published_only=True) for item in looks],
        }

    @app.get("/api/looks/{look_id}")
    def get_public_look(look_id: str):
        look = merchants.look(look_id)
        if not look or look["status"] != "published":
            raise HTTPException(404, "穿搭不存在")
        return look_document(look, published_only=True)

    @app.get("/api/garment-images/{image_id}")
    def get_garment_image(image_id: str):
        # Only ids this service minted resolve; the file name comes from the
        # asset table, so no request text ever reaches the filesystem path.
        image = merchants.image(image_id)
        if not image:
            raise HTTPException(404, "图片不存在")
        asset = store.asset(image["asset_id"])
        if not asset:
            raise HTTPException(404, "图片不存在")
        path = store.root / "assets" / asset["filename"]
        if not path.is_file():
            raise HTTPException(404, "图片文件已丢失")
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:

        @app.get("/")
        def index():
            return {
                "message": "ITP API ready. Build frontend to serve the workspace.",
                "docs": "/docs",
            }

    return app
