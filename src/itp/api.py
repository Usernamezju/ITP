import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from itp.accounts import (
    AccountMerchantUpgrade,
    AccountProfileUpdate,
    AccountRegisterRequest,
    AuthLimiter,
    public_account,
)
from itp.avatars import MAX_AVATAR_UPLOAD, AvatarStore, render_avatar

from itp.config import BFL_PROVIDERS, KLEIN_PROVIDERS, Settings
from itp.face_refine import (
    FaceRefineRequest,
    FaceRefineWorker,
    prepare_face_photo,
    valid_glb,
)
from itp.commerce import CommerceError
from itp.manual_qr import MAX_QR_UPLOAD, ManualQrStore, render_qr
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
    bearer_token,
    current_admin,
    current_merchant,
    current_user,
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
from itp.payment_service import PaymentService
from itp.payment_routes import payment_router
from itp.payments import MANUAL_QR_FIELDS, PaymentError, manual_qr_dir
from itp.payment_settings import (
    PaymentSettingsUpdate,
    public_payment_settings,
    save_payment_settings,
    validate_payment_update,
)
from itp.preprocessing import MAX_UPLOAD, Segmenter, image_base64, prepare_image
from itp.provider_settings import (
    ProviderSettingsUpdate,
    public_provider_settings,
    save_provider_settings,
    validate_provider_update,
    write_env_values,
)
from itp.private_jobs import PrivateFaceStore, PrivateTryOnStore
from itp.product_ai import ProductAiError, ProductDescriber, fetch_product_image, preview_jpeg
from itp.product_settings import (
    ProductSettingsUpdate,
    public_product_settings,
    save_product_settings,
    validate_product_update,
)
from itp.schemas import JobRequest
from itp.storage import Store, public_asset, public_job
from itp.tryon import TryOnRequest, TryOnWorker
from itp.transient import TransientStore
from itp.wardrobe import CATALOG, DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT, POSE_LABELS

logger = logging.getLogger(__name__)


class ReviewRequest(BaseModel):
    approve: bool


class OutfitRecommendRequest(BaseModel):
    """A recommendation scored from data the browser holds, not the server.

    ``asset_id`` points at a GLB the customer just uploaded and ``measurements``
    carries the numbers typed on the modelling page; neither is stored, and the
    file is deleted once the answer is assembled.
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    measurements: dict[str, Any] | None = None
    history_preferences: dict[str, Any] | None = None
    style: str | None = None
    season: str | None = None
    occasion: str | None = None
    pose_mode: str | None = None
    limit: int = Field(DEFAULT_LIMIT, ge=MIN_LIMIT, le=MAX_LIMIT)


class OutfitDiscoveryRequest(BaseModel):
    """Public ranking accepts compact category counts, never private assets."""
    model_config = ConfigDict(extra="forbid")
    style: str | None = Field(default=None, max_length=80)
    season: str | None = Field(default=None, max_length=80)
    occasion: str | None = Field(default=None, max_length=80)
    limit: int = Field(DEFAULT_LIMIT, ge=MIN_LIMIT, le=MAX_LIMIT)
    history_preferences: dict[str, Any] | None = None


# --- merchant accounts -------------------------------------------------------

MERCHANT_NAME = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")

# Multipart bodies may carry up to eight images, so the merchant upload routes
# get a larger (still bounded) cap than the single-image routes.  Existing
# paths keep the original limit.
UPLOAD_PATHS = {"/api/assets", "/api/face-photos", "/api/model-assets", "/api/merchant/garments"}
MERCHANT_IMAGE_PATH = re.compile(r"^/api/merchant/garments/[0-9a-zA-Z]+/images$")
MERCHANT_BODY_LIMIT = MAX_GARMENT_IMAGES * MAX_UPLOAD + 65536
DEFAULT_BODY_LIMIT = MAX_UPLOAD + 65536
# A customer may score one recommendation against a large local GLB; the file
# is temporary and deleted as soon as the calculation finishes.
MAX_MODEL_UPLOAD = 150 * 1024 * 1024
MODEL_BODY_LIMIT = MAX_MODEL_UPLOAD + 65536


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


class ProductImportRequest(BaseModel):
    """One shop or image link the merchant wants described."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


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
    fields = account_profile_fields(body.display_name, body.contact)
    check_password(body.password)
    return {"name": name, **fields}


def account_profile_fields(display_name: str, contact: str) -> dict[str, str]:
    """One profile validator for legacy merchant and unified account clients."""
    display_name = display_name.strip()
    if not 1 <= len(display_name) <= 40:
        raise HTTPException(422, "商家名称需为 1-40 个字符")
    contact = contact.strip()
    if len(contact) > 80:
        raise HTTPException(422, "联系方式不能超过 80 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in f"{display_name}{contact}"):
        raise HTTPException(422, "商家名称或联系方式含有不可见控制字符")
    return {"display_name": display_name, "contact": contact}


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
        "product_ai_endpoint": settings.product_ai_endpoint,
        "product_ai_model": settings.product_ai_model,
        "product_ai_api_key_set": bool(settings.product_ai_key),
        "image_provider": settings.image_provider,
        "unsplash_access_key_set": bool(settings.unsplash_access_key.get_secret_value()),
        "pixabay_api_key_set": bool(settings.pixabay_api_key.get_secret_value()),
    }


def save_image_settings(path: Path, changes: dict) -> None:
    """Write the outfit photo keys into ``.env`` beside the other settings.

    Uses the same audited atomic rewrite as the other settings sections, with
    its own field whitelist.
    """
    write_env_values(path, IMAGE_SETTING_FIELDS, changes)


def create_app(
    settings: Settings | None = None,
    *,
    start_worker: bool = True,
    config_path: Path = Path(".env"),
    frontend_dir: Path | None = None,
    payment_transport: httpx.BaseTransport | None = None,
    product_ai_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()
    store = TransientStore(settings.data_dir)
    commercial_assets = Store(settings.data_dir / "commercial")
    segmenter = Segmenter(settings.segmentation_model)
    pipeline = Pipeline(store, settings)
    tryons = PrivateTryOnStore()
    tryon_worker = TryOnWorker(store, tryons, settings)
    face_jobs = PrivateFaceStore()
    face_worker = FaceRefineWorker(store, face_jobs, settings)
    merchants = MerchantStore(settings.data_dir, settings)
    pipeline.commerce = merchants.commerce
    avatars = AvatarStore(settings.data_dir / "avatars")
    product_ai = ProductDescriber(settings, transport=product_ai_transport)
    settings_lock = threading.Lock()
    face_lock = threading.Lock()
    auth_limiter = AuthLimiter()
    avatar_limiter = AuthLimiter()
    product_limiter = AuthLimiter()
    product_config_limiter = AuthLimiter()
    janitor_stop = threading.Event()

    def janitor():
        while not janitor_stop.wait(1):
            for task_id in store.reap(settings.task_timeout_seconds):
                merchants.commerce.finish_model(task_id, succeeded=False, valid_result=False)
                tryons.delete(task_id)
                face_jobs.delete(task_id)

    @asynccontextmanager
    async def lifespan(app):
        lock = FileLock(str(settings.data_dir / "worker.lock"))
        thread = None
        tryon_thread = None
        face_thread = None
        janitor_thread = None
        if start_worker:
            try:
                lock.acquire(timeout=0)
            except Timeout as exc:
                raise RuntimeError(
                    "ClothiNation already uses this data directory; run one worker only"
                ) from exc
            store.sweep_stale()
            merchants.commerce.reconcile_models(store.job, pipeline.has_valid_result)
            janitor_thread = threading.Thread(target=janitor, daemon=True, name="itp-cleanup")
            janitor_thread.start()
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
                janitor_stop.set()
                await asyncio.to_thread(janitor_thread.join)
                lock.release()
            for job in store.jobs(active=True):
                merchants.commerce.finish_model(job["id"], succeeded=False, valid_result=False)
            store.close()

    app = FastAPI(title="ClothiNation Studio API", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.commercial_assets = commercial_assets
    app.state.pipeline = pipeline
    app.state.tryons = tryons
    app.state.tryon_worker = tryon_worker
    app.state.face_jobs = face_jobs
    app.state.face_worker = face_worker
    app.state.merchants = merchants
    app.state.commerce = merchants.commerce
    app.state.config_path = config_path
    app.state.settings = settings
    app.state.payments = PaymentService(merchants, settings, transport=payment_transport)
    app.state.product_ai = product_ai
    app.include_router(payment_router(app.state.payments))
    from itp.feedback import feedback_router
    app.include_router(feedback_router(merchants))
    from itp.product_clicks import click_router
    app.include_router(click_router(merchants))
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
        if request.url.path.startswith(("/api/auth/", "/api/account/")) or request.url.path in {
            "/api/merchant/register", "/api/merchant/login", "/api/merchant/password"
        }:
            # Pydantic's default errors include input values, including passwords.
            errors = [{key: item[key] for key in ("loc", "msg", "type")}
                      for item in exc.errors()]
            return JSONResponse({"detail": errors}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.url.path.rstrip("/") in {"/api/settings", "/api/body-profile"} and (
            app.state.settings.public_origin or origin
            or request.headers.get("x-forwarded-for")
            or request.headers.get("x-forwarded-proto")
        ):
            return JSONResponse({"detail": "Not found"}, status_code=404, headers={
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            })
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
            temporary = request.url.path in {"/api/model-assets", "/api/outfits/recommend"}
            limit = MODEL_BODY_LIMIT if temporary else (
                MERCHANT_BODY_LIMIT if is_upload else DEFAULT_BODY_LIMIT)
            if not size.isdigit() or int(size) > limit:
                return JSONResponse({"detail": "请求体过大或长度无效"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        private_paths = ("/api/auth/", "/api/account/", "/api/merchant/", "/api/admin/",
                         "/api/jobs", "/api/assets", "/api/face-", "/api/tryons",
                         "/api/model-assets")
        if (request.url.path == "/api/settings" or request.url.path == "/api/outfits/recommend"
                or request.url.path.startswith(private_paths)):
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

    def require_local_operator(request: Request):
        # Legacy CLI maintenance only. Never expose shared credentials through
        # a public deployment or an API schema consumed by customer clients.
        if (app.state.settings.public_origin or request.headers.get("origin")
                or request.headers.get("x-forwarded-for")
                or request.headers.get("x-forwarded-proto")):
            raise HTTPException(404, "Not found")

    @app.get("/api/settings", include_in_schema=False,
             dependencies=[Depends(require_local_operator)])
    def get_provider_settings():
        return public_settings(app.state.settings)

    @app.patch("/api/settings", include_in_schema=False,
               dependencies=[Depends(require_local_operator)])
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

    # ---------------------------------------------------- admin console (read-only)
    # Platform developer/administrator views. Admin accounts can only be minted
    # by scripts/create_admin.py; registration refuses the role outright. As with
    # the legacy /api/settings maintenance surface, these endpoints stay out of
    # the public OpenAPI document.

    def faceverse_health() -> dict:
        current = app.state.settings
        model = current.faceverse_model
        if not current.faceverse_ready:
            return {"configured": False, "reachable": False, "model": model,
                    "status": None, "cuda": None, "gpu": None}
        endpoint = current.faceverse_endpoint.removesuffix("/v1/face-refine") + "/health"
        token = current.faceverse_api_key.get_secret_value()
        try:
            with httpx.Client(timeout=3, follow_redirects=False, trust_env=False) as client:
                response = client.get(endpoint, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                body = response.json()
            return {"configured": True, "reachable": True, "model": model,
                    "status": body.get("status"), "cuda": body.get("cuda"),
                    "gpu": body.get("gpu")}
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            return {"configured": True, "reachable": False, "model": model,
                    "status": None, "cuda": None, "gpu": None}

    @app.get("/api/admin/status", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_status():
        current = app.state.settings
        return {
            "version": "0.1.0",
            "services": {
                "geometry": current.geometry_ready,
                "pose": current.pose_ready,
                "segmentation": current.segmentation_model.is_file(),
                "outfit_images": resolve_provider(current)[0],
                "faceverse": faceverse_health(),
                "tryon": {
                    name: {
                        "ready": current.tryon_provider_ready(name),
                        "model": current.tryon_model_for(name),
                    }
                    for name in ("seedream", *BFL_PROVIDERS, *KLEIN_PROVIDERS, "gpt_image")
                },
                "flux_klein": flux_klein_health(),
                "flux_klein_9b": flux_klein_health("flux_klein_9b"),
            },
            "payments": app.state.payments.methods(),
            "product_ai": {"ready": current.product_ai_ready,
                           "model": current.product_ai_model},
        }

    @app.get("/api/admin/settings", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_settings():
        # Secrets never leave as values: public_settings reports *_set booleans.
        return public_settings(app.state.settings)

    # The console stays read-only for model credentials; payment credentials are
    # the one operator-writable section, because the merchant keys can only come
    # from the operator's Alipay/WeChat accounts.  Values are written to the
    # server settings file and are never echoed back, logged or committed.
    payment_config_limiter = AuthLimiter()

    @app.get("/api/admin/product-ai", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_product_ai():
        return {"settings": public_product_settings(app.state.settings)}

    @app.post("/api/admin/product-ai/config", include_in_schema=False)
    def update_admin_product_ai(body: ProductSettingsUpdate,
                                admin: dict = Depends(current_admin)):
        """Point the product-image reader at another endpoint, model or key."""
        product_config_limiter.check(("product-ai-config", admin["id"]), attempts=10, seconds=60)
        with settings_lock:
            try:
                updated, changes = validate_product_update(app.state.settings, body)
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, str(exc) or "AI 配置无效") from exc
            if not changes:
                raise HTTPException(422, "没有需要保存的改动")
            if any(f"ITP_{field.upper()}" in os.environ for field in changes):
                raise HTTPException(409, "该配置已由进程环境变量指定，请在启动环境中修改")
            try:
                save_product_settings(config_path, changes)
            except OSError as exc:
                raise HTTPException(500, "无法保存 AI 配置，请检查服务器文件权限") from exc
            app.state.settings = updated
            # Field names only: values never reach a log line.
            logger.info("Admin %s updated product AI settings: %s",
                        admin["name"], sorted(changes))
            product_ai.settings = updated
        document = {"settings": public_product_settings(app.state.settings)}
        # Prove the new credentials against the provider, outside the lock.
        document["check"] = product_ai.probe()
        return document

    def admin_payment_document() -> dict:
        return {
            "settings": public_payment_settings(app.state.settings),
            "status": app.state.payments.status(),
            "manual": app.state.payments.manual_document(),
        }

    @app.get("/api/admin/payments", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_payments():
        return admin_payment_document()

    @app.post("/api/admin/payments/config", include_in_schema=False)
    def update_admin_payments(body: PaymentSettingsUpdate, admin: dict = Depends(current_admin)):
        payment_config_limiter.check(("payment-config", admin["id"]), attempts=10, seconds=60)
        with settings_lock:
            try:
                updated, changes = validate_payment_update(app.state.settings, body)
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, str(exc) or "支付配置无效") from exc
            if not changes:
                raise HTTPException(422, "没有需要保存的改动")
            if any(f"ITP_{field.upper()}" in os.environ for field in changes):
                raise HTTPException(409, "该配置已由进程环境变量指定，请在启动环境中修改")
            try:
                save_payment_settings(config_path, changes)
            except OSError as exc:
                raise HTTPException(500, "无法保存支付配置，请检查服务器文件权限") from exc
            retired = [
                getattr(app.state.settings, field)
                for field in MANUAL_QR_FIELDS.values()
                if changes.get(field) == "" and getattr(app.state.settings, field)
            ]
            app.state.settings = updated
            # Field names only: values never reach a log line.
            logger.info(
                "Admin %s updated payment credentials: %s", admin["name"], sorted(changes)
            )
            app.state.payments.reload(updated)
        # A cleared collection code is deleted after the new settings are live.
        for name in retired:
            manual_codes.remove(name)
        document = admin_payment_document()
        # Probing calls the official gateway, so it happens after the new
        # credentials are live and never inside the settings lock.
        document["checks"] = app.state.payments.probe_all()
        return document

    # --- manual collection codes ---------------------------------------------
    # The operator's own WeChat/Alipay codes.  An upload is re-encoded and kept
    # as a file under data/payment/manual; the settings file only ever holds its
    # random name.  No channel here can mark an order paid — that is the
    # operator's decision, and it happens in confirm_manual below.
    manual_codes = ManualQrStore(manual_qr_dir(settings))

    @app.post("/api/admin/payments/manual/qr", include_in_schema=False)
    async def upload_manual_qr(channel: str = Form(...), file: UploadFile = File(...),
                               admin: dict = Depends(current_admin)):
        payment_config_limiter.check(("manual-qr", admin["id"]), attempts=10, seconds=60)
        field = MANUAL_QR_FIELDS.get("manual_" + channel)
        if not field:
            raise HTTPException(422, "只能上传微信或支付宝收款码")
        try:
            data = await file.read(MAX_QR_UPLOAD + 1)
        finally:
            await file.close()
        try:
            picture = await asyncio.to_thread(render_qr, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        name = await asyncio.to_thread(manual_codes.save, picture)
        previous = getattr(app.state.settings, field)
        if f"ITP_{field.upper()}" in os.environ:
            manual_codes.remove(name)
            raise HTTPException(409, "该配置已由进程环境变量指定，请在启动环境中修改")
        with settings_lock:
            try:
                save_payment_settings(config_path, {field: name})
            except OSError as exc:
                manual_codes.remove(name)
                raise HTTPException(500, "无法保存收款码配置，请检查服务器文件权限") from exc
            values = app.state.settings.model_dump()
            values[field] = name
            updated = Settings(_env_file=None, **values)
            app.state.settings = updated
            app.state.payments.reload(updated)
        if previous and previous != name:
            manual_codes.remove(previous)
        logger.info("Admin %s uploaded the %s collection code", admin["name"], channel)
        return admin_payment_document()

    @app.get("/api/admin/payments/manual/orders", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_manual_orders(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        """Orders a customer says they paid; only a human can confirm them."""
        total, items = app.state.payments.manual_orders(limit=limit, offset=offset)
        return {"total": total, "items": items}

    @app.post("/api/admin/payments/manual/orders/{order_id}/confirm", include_in_schema=False)
    def admin_manual_confirm(order_id: str, admin: dict = Depends(current_admin)):
        payment_config_limiter.check(("manual-confirm", admin["id"]), attempts=60, seconds=60)
        try:
            order = app.state.payments.confirm_manual(order_id)
        except KeyError as exc:
            raise HTTPException(404, "订单不存在") from exc
        except (PaymentError, CommerceError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except sqlite3.OperationalError as exc:
            # The whole fulfillment rolls back with the transaction, so the
            # order stays pending and the operator can simply try again.
            logger.warning("Manual confirmation %s hit a busy database", order_id)
            raise HTTPException(503, "订单确认暂时失败，请稍后重试") from exc
        logger.info("Admin %s confirmed manual order %s", admin["name"], order_id)
        return order

    @app.post("/api/admin/payments/manual/orders/{order_id}/reject", include_in_schema=False)
    def admin_manual_reject(order_id: str, admin: dict = Depends(current_admin)):
        payment_config_limiter.check(("manual-reject", admin["id"]), attempts=60, seconds=60)
        try:
            order = app.state.payments.reject_manual(order_id)
        except KeyError as exc:
            raise HTTPException(404, "订单不存在") from exc
        except PaymentError as exc:
            raise HTTPException(409, str(exc)) from exc
        logger.info("Admin %s rejected manual order %s", admin["name"], order_id)
        return order

    @app.get("/api/admin/accounts", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_accounts(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        total, accounts = merchants.list_accounts(limit=limit, offset=offset)
        return {"total": total, "items": [
            public_account(account) | {
                "disabled": account["disabled"],
                "quota": account["quota"],
                "garment_count": account["garment_count"],
            }
            for account in accounts
        ]}

    @app.get("/api/admin/usage", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_usage():
        content = merchants.content_counts()
        usage = app.state.payments.commerce.platform_totals()
        usage["garments"] = content["garments"]
        usage["looks"] = content["looks"]
        usage["orders"] = app.state.payments.order_counts()
        return usage

    @app.get("/api/admin/orders", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_orders(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        total, orders = app.state.payments.list_all(limit=limit, offset=offset)
        return {"total": total, "items": orders}

    def admin_job_view(job: dict) -> dict:
        return {
            "id": job.get("id"),
            "owner_id": job.get("owner_id"),
            "state": job.get("state"),
            "created": job.get("created"),
            "updated": job.get("updated"),
            "steps": [
                {"name": step.get("name"), "status": step.get("status")}
                for step in job.get("steps", [])
            ],
        }

    def admin_transient_view(item: dict) -> dict:
        return {
            "id": item.get("id"),
            "owner_id": item.get("owner_id"),
            "state": item.get("state"),
            "created": item.get("created"),
            "model": item.get("model"),
            "provider": item.get("provider"),
        }

    @app.get("/api/admin/jobs", include_in_schema=False,
             dependencies=[Depends(current_admin)])
    def admin_jobs():
        return {
            "jobs": [admin_job_view(job) for job in store.jobs()],
            "tryons": [admin_transient_view(item) for item in tryons.list()],
            "face_refinements": [admin_transient_view(item) for item in face_jobs.list()],
            "note": "仅显示服务端当前保留的任务；顾客确认保存后服务端副本即被删除，"
                    "没有历史任务记录。",
        }

    @app.get("/api/payments/manual/qr/{key}")
    def manual_qr_image(key: str):
        """The operator's collection code, shown to whoever holds an order.

        Public by design: the payer's browser loads it as a plain image, and
        the key is a fresh random name that only this order's checkout carries.
        It is a picture to scan, never a payment confirmation.
        """
        path = manual_codes.path(key)
        if not path:
            raise HTTPException(404, "收款码不存在")
        return FileResponse(path, media_type="image/png", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })

    @app.post("/api/assets", status_code=201)
    async def upload(file: UploadFile = File(...), remove_background: bool = Query(False),
                     user: dict = Depends(current_user)):
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
            owner_id=user["id"],
        )
        return public_asset(asset)

    @app.post("/api/face-photos", status_code=201)
    async def upload_face_photo(file: UploadFile = File(...), user: dict = Depends(current_user)):
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
            asset_id, path, "face_photo", owner_id=user["id"],
            width=image.width, height=image.height,
        ))

    @app.post("/api/model-assets", status_code=201)
    async def upload_model_asset(file: UploadFile = File(...), user: dict = Depends(current_user)):
        """A customer GLB, kept only long enough to score one recommendation."""
        try:
            data = await file.read(MAX_MODEL_UPLOAD + 1)
        finally:
            await file.close()
        if len(data) > MAX_MODEL_UPLOAD:
            raise HTTPException(422, f"模型文件不能超过 {MAX_MODEL_UPLOAD // (1024 * 1024)} MiB")
        if not valid_glb(data):
            raise HTTPException(422, "请上传有效的 GLB 模型文件")
        asset_id, path = store.new_asset_path("glb")
        await asyncio.to_thread(path.write_bytes, data)
        return public_asset(store.add_asset(
            asset_id, path, "model", format="GLB", owner_id=user["id"]
        ))

    def owned_asset(asset_id, user):
        asset = store.asset(asset_id)
        if not asset or asset.get("owner_id") != user["id"]:
            raise HTTPException(404, "资产不存在或已过期")
        return asset

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str, user: dict = Depends(current_user)):
        return public_asset(owned_asset(asset_id, user))

    @app.get("/api/assets/{asset_id}/file")
    def get_file(asset_id: str, download: bool = False, user: dict = Depends(current_user)):
        asset = owned_asset(asset_id, user)
        media = {"GLB": "model/gltf-binary", "PNG": "image/png"}.get(
            asset.get("format", "PNG"), "application/octet-stream"
        )
        headers = {"Cache-Control": "no-store"}
        if download:
            suffix = asset.get("format", "png").lower()
            headers["Content-Disposition"] = f'attachment; filename="{asset_id}.{suffix}"'
        try:
            return Response(store.read(asset_id), media_type=media, headers=headers)
        except FileNotFoundError as exc:
            raise HTTPException(404, "资产已过期") from exc

    @app.get("/api/jobs")
    def list_jobs(user: dict = Depends(current_user)):
        return [public_job(j) for j in store.jobs(owner_id=user["id"])]

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobRequest, request: Request, user: dict = Depends(current_user)):
        ids = [body.front, *body.views.values()]
        if body.pose_reference:
            ids.append(body.pose_reference)
        for asset_id in ids:
            asset = store.asset(asset_id)
            if not asset or asset["kind"] != "image" or not store.path(asset_id).is_file():
                raise HTTPException(422, "输入图片不存在，请重新上传")
            if asset.get("owner_id") != user["id"]:
                raise HTTPException(404, "资产不存在或已过期")
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
            raise HTTPException(503, "平台人体建模服务暂不可用，请稍后再试")
        if body.pose_mode != "original" and not current.pose_ready:
            raise HTTPException(503, "平台姿势编辑服务暂不可用，可使用原始姿势")
        models = {"geometry": current.tencent_model}
        if body.pose_mode != "original":
            models["pose"] = current.pose_model
        return enqueue_paid_job(body, request, user, models=models)

    def enqueue_paid_job(body, request, user, *, models=None):
        from itp.commerce import CommerceError, IdempotencyConflict, InsufficientFunds
        try:
            reservation = merchants.commerce.reserve_model(user["id"],
                request.headers.get("idempotency-key", ""), body.model_dump())
        except InsufficientFunds as exc:
            raise HTTPException(402, str(exc)) from exc
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except CommerceError as exc:
            raise HTTPException(422, str(exc)) from exc
        if reservation["replayed"]:
            existing = store.job(reservation["job_id"])
            if not existing:
                raise HTTPException(409, "该请求已处理；请查看任务历史与钱包流水，勿重复扣费")
            return public_job(existing)
        try:
            inputs = [body.front, *body.views.values()]
            if body.pose_reference:
                inputs.append(body.pose_reference)
            store.pin(reservation["job_id"], user["id"], inputs)
            return public_job(store.create_job(body.model_dump(), models=models,
                job_id=reservation["job_id"], owner_id=user["id"]))
        except Exception:
            merchants.commerce.finish_model(reservation["job_id"], succeeded=False, valid_result=False)
            raise

    @app.get("/api/tryons")
    def list_tryons(user: dict = Depends(current_user)):
        return tryons.list(user["id"])

    @app.post("/api/tryons", status_code=201)
    def create_tryon(body: TryOnRequest, user: dict = Depends(current_user)):
        try:
            body.validate_views()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        for asset_id in [*body.person.values(), *body.garment.values()]:
            asset = store.asset(asset_id)
            if not asset or asset["kind"] != "image" or not store.path(asset_id).is_file():
                raise HTTPException(422, "输入图片不存在，请重新上传")
            if asset.get("owner_id") != user["id"]:
                raise HTTPException(404, "资产不存在或已过期")
        if not app.state.settings.tryon_provider_ready(body.provider):
            raise HTTPException(503, "所选平台生图服务暂不可用，请稍后再试")
        if body.provider in KLEIN_PROVIDERS and not flux_klein_health(body.provider)["ready"]:
            variant = "9B" if body.provider == "flux_klein_9b" else "4B"
            raise HTTPException(503, f"FLUX.2 Klein {variant} 服务尚未就绪；请检查健康状态")
        job = tryons.create(body, app.state.settings.tryon_model_for(body.provider), user["id"])
        store.pin(job["id"], user["id"], [*body.person.values(), *body.garment.values()])
        return job

    @app.get("/api/tryons/{tryon_id}")
    def get_tryon(tryon_id: str, user: dict = Depends(current_user)):
        job = tryons.get(tryon_id)
        if not job or job.get("owner_id") != user["id"]:
            raise HTTPException(404, "试穿任务不存在")
        return job

    @app.post("/api/jobs/{job_id}/acknowledge", status_code=204)
    def acknowledge_job(job_id: str, user: dict = Depends(current_user)):
        """The browser saved these results locally; drop the server copies."""
        owned_job(job_id, user)
        acknowledge_scope(job_id, user)
        return None

    @app.post("/api/tryons/{tryon_id}/acknowledge", status_code=204)
    def acknowledge_tryon(tryon_id: str, user: dict = Depends(current_user)):
        tryon = tryons.get(tryon_id)
        if not tryon or tryon.get("owner_id") != user["id"]:
            raise HTTPException(404, "试穿任务不存在")
        acknowledge_scope(tryon_id, user)
        tryons.delete(tryon_id)
        return None

    @app.post("/api/face-refinements/{item_id}/acknowledge", status_code=204)
    def acknowledge_face_refinement(item_id: str, user: dict = Depends(current_user)):
        item = face_jobs.get(item_id)
        if not item or item.get("owner_id") != user["id"]:
            raise HTTPException(404, "脸部精修任务不存在")
        acknowledge_scope(item_id, user)
        face_jobs.delete(item_id)
        return None

    def acknowledge_scope(scope: str, user: dict):
        try:
            store.acknowledge(scope, user["id"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, user: dict = Depends(current_user)):
        return public_job(owned_job(job_id, user))

    def owned_job(job_id, user):
        job = store.job(job_id)
        if not job or job.get("owner_id") != user["id"]:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/api/face-refinements/{item_id}")
    def get_face_refinement(item_id: str, user: dict = Depends(current_user)):
        item = face_jobs.get(item_id)
        if not item or item.get("owner_id") != user["id"]:
            raise HTTPException(404, "脸部精修任务不存在")
        return item

    @app.post("/api/face-refinements", status_code=201)
    def create_face_refinement(body: FaceRefineRequest, user: dict = Depends(current_user)):
        """Refine a model the browser handed back for this one run.

        The mesh and the photo are temporary uploads: the browser kept the model
        it generated, and hands it in again whenever a refinement is wanted.
        """
        mesh = owned_asset(body.mesh, user)
        if mesh["kind"] != "model" or mesh.get("format") != "GLB" or not valid_glb(
            store.read(body.mesh)
        ):
            raise HTTPException(422, "请上传有效的 GLB 模型文件")
        photo = owned_asset(body.face_photo, user)
        if photo["kind"] != "face_photo" or not store.path(body.face_photo).is_file():
            raise HTTPException(422, "请上传原始高清正面人物照片")
        if not app.state.settings.faceverse_ready:
            raise HTTPException(503, "平台脸部精修服务暂不可用，请稍后再试")
        with face_lock:
            if any(item["state"] in {"queued", "running", "submitting"}
                   for item in face_jobs.for_mesh(body.mesh, user["id"])):
                raise HTTPException(409, "该模型已有进行中的脸部精修任务")
            item = face_jobs.create(body.mesh, body.face_photo,
                                    app.state.settings.faceverse_model, user["id"])
            store.pin(item["id"], user["id"], [body.mesh, body.face_photo])
            return item

    @app.post("/api/jobs/{job_id}/review")
    def review_job(job_id: str, body: ReviewRequest, user: dict = Depends(current_user)):
        owned_job(job_id, user)
        if body.approve and not app.state.settings.geometry_ready:
            raise HTTPException(503, "腾讯云 API 待配置，不能继续生成")
        try:
            reviewed = store.review(job_id, body.approve)
            if not body.approve:
                merchants.commerce.finish_model(job_id, succeeded=False, valid_result=False)
                store.finish(job_id)
            return public_job(reviewed)
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/outfits")
    def list_outfits(
        style: str | None = Query(None),
        season: str | None = Query(None),
        occasion: str | None = Query(None),
        limit: int = Query(DEFAULT_LIMIT, ge=MIN_LIMIT, le=MAX_LIMIT),
    ):
        # The public catalogue, with no customer data in it at all: published
        # merchant items are ranked as generic body advice.  Scoring against a
        # real body happens on /api/outfits/recommend, owner by owner.
        return recommend_outfits(
            store,
            merchants,
            style=style,
            season=season,
            occasion=occasion,
            limit=limit,
        )

    @app.post("/api/outfits/discover")
    def discover_outfits(body: OutfitDiscoveryRequest):
        from itp.hybrid_recommendation import normalize_history_preferences
        try:
            history = normalize_history_preferences(body.history_preferences)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return recommend_outfits(store, merchants, style=body.style, season=body.season,
                                 occasion=body.occasion, limit=body.limit,
                                 history_preferences=history)

    @app.post("/api/outfits/recommend")
    def recommend_for_browser(body: OutfitRecommendRequest, user: dict = Depends(current_user)):
        """Score one recommendation from data the browser uploaded just now.

        The measurements and the model exist only for this request: the file is
        deleted once the calculation finishes, whatever the outcome.
        """
        if body.pose_mode and body.pose_mode not in POSE_LABELS:
            raise HTTPException(422, "未知的姿势类型")
        if body.asset_id:
            asset = owned_asset(body.asset_id, user)
            if asset["kind"] != "model" or not store.path(body.asset_id).is_file():
                raise HTTPException(422, "模型文件已过期，请重新上传")
        try:
            from itp.hybrid_recommendation import normalize_history_preferences
            history = normalize_history_preferences(body.history_preferences)
            measurements = (
                normalize_body_profile(body.measurements) if body.measurements else None
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return recommend_outfits(
                store,
                merchants,
                asset_id=body.asset_id,
                style=body.style,
                season=body.season,
                occasion=body.occasion,
                limit=body.limit,
                body_profile=measurements,
                pose_mode=body.pose_mode,
                history_preferences=history,
            )
        finally:
            if body.asset_id:
                store.discard(body.asset_id)

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
    def merchant_register(body: MerchantRegisterRequest, request: Request):
        account = account_register(AccountRegisterRequest(**body.model_dump(), role="merchant"), request)
        return {
            "merchant_id": account["id"],
            "name": account["name"],
            "display_name": account["display_name"],
        }

    @app.post("/api/merchant/login")
    def merchant_login(body: MerchantLoginRequest, request: Request):
        return login_account(body, request, merchant_only=True)

    def login_account(body: MerchantLoginRequest, request: Request, *, merchant_only=False):
        if len(body.name) > 128 or len(body.password) > 128:
            verify_password(body.password[:128], DUMMY_PASSWORD_HASH)
            raise HTTPException(401, "账号或密码不正确")
        # Nginx appends the actual client IP last; do not trust the first value,
        # which might have been supplied by a visitor.
        peer = request.client.host if request.client else "unknown"
        if peer in {"127.0.0.1", "::1"}:
            peer = request.headers.get("x-forwarded-for", peer).split(",")[-1].strip()
        auth_limiter.check(("login", peer, body.name.strip()), attempts=8, seconds=300)
        merchant = merchants.merchant_by_name(body.name.strip())
        stored = merchant["password_hash"] if merchant else DUMMY_PASSWORD_HASH
        accepted = verify_password(body.password, stored)
        if not merchant or not accepted:
            raise HTTPException(401, "账号或密码不正确")
        if merchant["disabled"]:
            raise HTTPException(403, "该账号已被禁用")
        if merchant_only and merchant["role"] != "merchant":
            raise HTTPException(403, "需要商家身份才能访问")
        token, expires_in = encode_token(
            resolve_jwt_secret(app),
            merchant["id"],
            hours=app.state.settings.merchant_token_hours,
            # Tags the token with this password, so changing it revokes the token.
            password_hash=merchant["password_hash"],
        )
        return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}

    @app.post("/api/merchant/import-link")
    async def merchant_import_link(body: ProductImportRequest,
                                   merchant: dict = Depends(current_merchant)):
        """Describe a product link for the form; nothing is stored or published.

        The picture is fetched by the server, downscaled, and sent to the
        operator's vision model, so the merchant's browser never needs a key and
        the merchant still decides what to save.
        """
        product_limiter.check(("product-import", merchant["id"]), attempts=20, seconds=3600)
        try:
            data, _content_type, source = await asyncio.to_thread(
                fetch_product_image, body.url)
            preview, width, height = await asyncio.to_thread(preview_jpeg, data)
            fields = await asyncio.to_thread(product_ai.describe, preview)
        except ProductAiError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "fields": fields,
            "image": {
                "data_url": "data:image/jpeg;base64," + base64.b64encode(preview).decode(),
                "width": width,
                "height": height,
                "source_url": source,
            },
            "model": app.state.settings.product_ai_model,
        }

    @app.get("/api/merchant/me")
    def merchant_me(merchant: dict = Depends(current_merchant)):
        usage = merchants.commerce.summary(merchant["id"])["upload_usage"]
        return public_merchant(merchant, garment_count=merchants.count_garments(merchant["id"])) | {
            "quota": usage["limit"], "upload_usage": usage}

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

    @app.post("/api/auth/register", status_code=201)
    def account_register(body: AccountRegisterRequest, request: Request):
        fields = merchant_register_fields(MerchantRegisterRequest(
            **body.model_dump(exclude={"role"})))
        peer = request.client.host if request.client else "unknown"
        if peer in {"127.0.0.1", "::1"}:
            peer = request.headers.get("x-forwarded-for", peer).split(",")[-1].strip()
        auth_limiter.check(("register", peer), attempts=10, seconds=3600)
        try:
            user = merchants.create_merchant(**fields, password_hash=hash_password(body.password),
                role=body.role, quota=app.state.settings.merchant_quota if body.role == "merchant" else 0)
        except AlreadyExists as exc:
            raise HTTPException(409, "账号名称已被占用") from exc
        return public_account(user)

    @app.post("/api/auth/login")
    def account_login(body: MerchantLoginRequest, request: Request):
        return login_account(body, request)

    @app.post("/api/auth/logout")
    def account_logout(request: Request, user: dict = Depends(current_user)):
        token = bearer_token(request)
        claims = request.state.auth_claims
        merchants.revoke_token(hashlib.sha256(token.encode()).hexdigest(), user["id"], claims["exp"])
        return {"logged_out": True}

    @app.get("/api/account/me")
    def account_me(user: dict = Depends(current_user)):
        return public_account(user)

    @app.get("/api/pricing")
    def pricing():
        return merchants.commerce.prices()

    @app.get("/api/account/commerce")
    def account_commerce(user: dict = Depends(current_user)):
        return merchants.commerce.summary(user["id"])

    @app.get("/api/account/ledger")
    def account_ledger(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                       user: dict = Depends(current_user)):
        return {"items": merchants.commerce.ledger(user["id"], limit=limit, offset=offset)}

    @app.patch("/api/account/me")
    def account_update(body: AccountProfileUpdate, user: dict = Depends(current_user)):
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(422, "个人资料不能为 null")
        fields = account_profile_fields(changes.get("display_name", user["display_name"]),
                                        changes.get("contact", user["contact"]))
        return public_account(merchants.update_account_profile(user["id"],
            display_name=fields["display_name"], contact=fields["contact"]))

    @app.post("/api/account/password")
    def account_password(body: MerchantPasswordRequest, user: dict = Depends(current_user)):
        return merchant_change_password(body, user)

    @app.post("/api/account/merchant")
    def become_merchant(body: AccountMerchantUpgrade, user: dict = Depends(current_user)):
        """A customer registers as a shop without creating a second account."""
        if user.get("role") == "admin":
            raise HTTPException(403, "管理员账号不能注册为商家")
        # An omitted field keeps the account's current value; an explicit empty
        # one is validated like the profile form and refused.
        fields = account_profile_fields(
            body.display_name if body.display_name is not None else user["display_name"],
            body.contact if body.contact is not None else user["contact"],
        )
        try:
            return public_account(merchants.upgrade_to_merchant(user["id"], **fields))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    # Avatars belong to the account, so customers and shops can both use them.
    # The picture is re-encoded on the server and published under a random key
    # URL: no account id travels with it, and replacing it retires the old URL.
    @app.post("/api/account/avatar")
    async def upload_avatar(file: UploadFile = File(...), user: dict = Depends(current_user)):
        avatar_limiter.check(("avatar", user["id"]), attempts=10, seconds=60)
        try:
            data = await file.read(MAX_AVATAR_UPLOAD + 1)
        finally:
            await file.close()
        try:
            picture = await asyncio.to_thread(render_avatar, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        previous = user.get("avatar_key")
        updated = merchants.set_avatar_key(user["id"], avatars.save(picture))
        if previous:
            avatars.remove(previous)
        return public_account(updated)

    @app.delete("/api/account/avatar")
    def delete_avatar(user: dict = Depends(current_user)):
        updated = merchants.set_avatar_key(user["id"], None)
        if user.get("avatar_key"):
            avatars.remove(user["avatar_key"])
        return public_account(updated)

    @app.get("/api/avatars/{key}")
    def avatar_image(key: str):
        path = avatars.path(key)
        if not path:
            raise HTTPException(404, "头像不存在")
        # The key changes with every upload, so the file itself never changes.
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

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
                asset_id, path = commercial_assets.new_asset_path("png")
                await asyncio.to_thread(image.save, path, "PNG")
                commercial_assets.add_asset(
                    asset_id, path, "garment_image", width=image.width, height=image.height
                )
                stored.append(merchants.add_image(garment["id"], asset_id))
        except Exception:
            merchants.delete_garment(merchant["id"], garment["id"])
            for item in stored:
                asset = commercial_assets.asset(item["asset_id"])
                if asset:
                    (commercial_assets.root / "assets" / asset["filename"]).unlink(missing_ok=True)
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
            asset = commercial_assets.asset(asset_id)
            if asset:
                (commercial_assets.root / "assets" / asset["filename"]).unlink(missing_ok=True)
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
            asset_id, path = commercial_assets.new_asset_path("png")
            await asyncio.to_thread(image.save, path, "PNG")
            commercial_assets.add_asset(
                asset_id, path, "garment_image", width=image.width, height=image.height
            )
            merchants.add_image(garment_id, asset_id)
        return [public_image(item) for item in merchants.images_for(garment_id)]

    @app.delete("/api/merchant/garments/{garment_id}/images/{image_id}", status_code=204)
    def merchant_delete_garment_image(
        garment_id: str, image_id: str, merchant: dict = Depends(current_merchant)
    ):
        asset_id = merchants.delete_image(merchant["id"], garment_id, image_id)
        if asset_id is None:
            raise HTTPException(404, "图片不存在")
        asset = commercial_assets.asset(asset_id)
        if asset:
            (commercial_assets.root / "assets" / asset["filename"]).unlink(missing_ok=True)
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
        asset = commercial_assets.asset(image["asset_id"])
        if not asset:
            raise HTTPException(404, "图片不存在")
        path = commercial_assets.root / "assets" / asset["filename"]
        if not path.is_file():
            raise HTTPException(404, "图片文件已丢失")
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    frontend = frontend_dir or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    root = frontend.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def single_page_app(path: str):
        # Customer, merchant and admin addresses are real URLs: /, /tryon,
        # /outfits, /history, /account, /appearance, /merchant, /admin.
        # Built files win when they exist; every other path returns the app
        # itself so a deep link or a refresh lands on the right page.  The
        # API never falls back to HTML, and no request escapes the dist root.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index)
        if path in {"", "index.html"}:
            return {
                "message": "ClothiNation API ready. Build frontend to serve the workspace.",
                "docs": "/docs",
            }
        raise HTTPException(404, "前端尚未构建")

    return app
