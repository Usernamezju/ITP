from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KLEIN_PROVIDERS = ("flux_klein", "flux_klein_9b")
BFL_PROVIDERS = ("flux", "flux_max")
HAIJING_GENERATION_ENDPOINT = "https://api.haijingai.com/v2/images/generations"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ITP_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    segmentation_model: Path = Path("models/u2netp.onnx")
    public_origin: str = ""
    tencent_endpoint: str = ""
    tencent_secret_id: SecretStr = SecretStr("")
    tencent_secret_key: SecretStr = SecretStr("")
    tencent_region: str = ""
    tencent_model: str = "3.1"
    pose_endpoint: str = ""
    pose_api_key: SecretStr = SecretStr("")
    pose_model: str = "qwen-image-edit-plus-2025-12-15"
    seedream_endpoint: str = ""
    seedream_api_key: SecretStr = SecretStr("")
    seedream_model: str = "doubao-seedream-5-0-flash-260915"
    flux_endpoint: str = ""
    flux_api_key: SecretStr = SecretStr("")
    flux_model: str = "flux-2-pro"
    flux_max_endpoint: str = ""
    flux_max_api_key: SecretStr = SecretStr("")
    flux_max_model: str = "flux-2-max"
    flux_klein_endpoint: str = ""
    flux_klein_api_key: SecretStr = SecretStr("")
    flux_klein_model: str = "flux.2-klein-4b"
    flux_klein_9b_endpoint: str = ""
    flux_klein_9b_api_key: SecretStr = SecretStr("")
    flux_klein_9b_model: str = "flux.2-klein-9b"
    gpt_image_endpoint: str = ""
    gpt_image_api_key: SecretStr = SecretStr("")
    gpt_image_model: str = "gpt-image-2"
    faceverse_endpoint: str = ""
    faceverse_api_key: SecretStr = SecretStr("")
    faceverse_model: str = "faceverse-v4"
    image_provider: str = "so"
    unsplash_access_key: SecretStr = SecretStr("")
    pixabay_api_key: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    merchant_token_hours: int = Field(default=12, ge=1, le=720)
    merchant_quota: int = Field(default=200, ge=0, le=100000)
    poll_seconds: float = Field(default=5, ge=0.05)
    task_timeout_seconds: int = Field(default=3600, ge=30)

    @model_validator(mode="after")
    def validate_endpoints(self):
        if self.public_origin:
            origin = urlparse(self.public_origin)
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path
                or origin.params
                or origin.query
                or origin.fragment
                or self.public_origin != f"{origin.scheme}://{origin.netloc}"
            ):
                raise ValueError("Public origin must be an HTTPS origin without a path")
        if self.tencent_endpoint and self.tencent_endpoint != "ai3d.tencentcloudapi.com":
            raise ValueError("Tencent endpoint must be the mainland AI3D hostname")
        if self.pose_endpoint:
            url = urlparse(self.pose_endpoint)
            host = url.hostname or ""
            allowed = host == "dashscope.aliyuncs.com" or host.endswith(
                ".cn-beijing.maas.aliyuncs.com"
            )
            if (
                not allowed
                or url.scheme != "https"
                or url.username
                or url.password
                or url.port not in (None, 443)
                or url.query
                or url.fragment
                or url.path != "/api/v1/services/aigc/multimodal-generation/generation"
            ):
                raise ValueError("Pose endpoint must be a mainland Model Studio generation URL")
        if self.seedream_endpoint and self.seedream_endpoint != "https://ark.cn-beijing.volces.com/api/v3/images/generations":
            raise ValueError("Seedream endpoint must be the mainland Ark image generations URL")
        for name, suffix in (
            ("flux_endpoint", "/v1/flux-2-pro"),
            ("flux_max_endpoint", "/v1/flux-2-max"),
            ("gpt_image_endpoint", "/v1/images/edits"),
        ):
            endpoint = getattr(self, name)
            if endpoint:
                if (name in {"flux_endpoint", "flux_max_endpoint"}
                        and endpoint == HAIJING_GENERATION_ENDPOINT):
                    continue
                url = urlparse(endpoint)
                if (url.scheme != "https" or not url.hostname or url.username or url.password
                        or url.query or url.fragment or url.path != suffix):
                    raise ValueError(f"{name} must be an HTTPS {suffix} URL")
        for provider in KLEIN_PROVIDERS:
            endpoint = getattr(self, f"{provider}_endpoint")
            if not endpoint:
                continue
            url = urlparse(endpoint)
            if (url.scheme not in {"https", "http"} or not url.hostname or url.username
                    or url.password or url.query or url.fragment
                    or url.path != "/v1/flux-klein/edit"
                    or (
                        url.scheme == "http"
                        and url.hostname not in {"localhost", "127.0.0.1", "::1"}
                    )):
                raise ValueError(
                    "FLUX Klein endpoint must be HTTPS or local HTTP /v1/flux-klein/edit"
                )
        if self.faceverse_endpoint:
            url = urlparse(self.faceverse_endpoint)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path != "/v1/face-refine"
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
            ):
                raise ValueError(
                    "FaceVerse endpoint must be HTTPS or a local HTTP /v1/face-refine URL"
                )
        return self

    @property
    def geometry_ready(self) -> bool:
        return all(
            (
                self.tencent_endpoint,
                self.tencent_region,
                self.tencent_secret_id.get_secret_value(),
                self.tencent_secret_key.get_secret_value(),
            )
        )

    @property
    def pose_ready(self) -> bool:
        return bool(self.pose_endpoint and self.pose_api_key.get_secret_value())

    @property
    def tryon_ready(self) -> bool:
        return bool(self.seedream_endpoint and self.seedream_api_key.get_secret_value())

    def tryon_provider_ready(self, provider: str) -> bool:
        if provider == "seedream":
            return self.tryon_ready
        if provider in (*BFL_PROVIDERS, *KLEIN_PROVIDERS):
            return bool(
                getattr(self, f"{provider}_endpoint")
                and getattr(self, f"{provider}_api_key").get_secret_value()
            )
        if provider == "gpt_image":
            return bool(self.gpt_image_endpoint and self.gpt_image_api_key.get_secret_value())
        return False

    def tryon_model_for(self, provider: str) -> str:
        return {"seedream": self.seedream_model, "flux": self.flux_model,
                "flux_max": self.flux_max_model,
                "flux_klein": self.flux_klein_model,
                "flux_klein_9b": self.flux_klein_9b_model,
                "gpt_image": self.gpt_image_model}[provider]

    @property
    def faceverse_ready(self) -> bool:
        return bool(self.faceverse_endpoint)
