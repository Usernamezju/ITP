import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from itp.config import Settings

_ENV_KEY = re.compile(r"^\s*(?:export\s+)?(ITP_[A-Z0-9_]+)\s*=")


def write_env_values(path: Path, fields: tuple[str, ...], changes: dict) -> None:
    """Atomically rewrite exactly these ITP_* keys in the server settings file.

    Old lines for the same keys are dropped, values are JSON-encoded so PEM
    material with newlines stays on one line, the replacement is atomic and the
    file keeps owner-only permissions. Symlinked files are refused.
    """
    if not changes:
        return
    if path.is_symlink():
        raise OSError("Refusing to replace a symlinked settings file")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    keys = {f"ITP_{field.upper()}" for field in changes}
    lines = [
        line for line in original.splitlines(keepends=True)
        if not (match := _ENV_KEY.match(line)) or match.group(1) not in keys
    ]
    content = "".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    for field in fields:
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


EDITABLE_FIELDS = (
    "tencent_endpoint",
    "tencent_region",
    "tencent_model",
    "tencent_secret_id",
    "tencent_secret_key",
    "pose_endpoint",
    "pose_model",
    "pose_api_key",
    "seedream_endpoint",
    "seedream_model",
    "seedream_api_key",
    "flux_endpoint",
    "flux_model",
    "flux_api_key",
    "flux_max_endpoint",
    "flux_max_model",
    "flux_max_api_key",
    "flux_klein_endpoint",
    "flux_klein_model",
    "flux_klein_api_key",
    "flux_klein_9b_endpoint",
    "flux_klein_9b_model",
    "flux_klein_9b_api_key",
    "gpt_image_endpoint",
    "gpt_image_model",
    "gpt_image_api_key",
    "faceverse_endpoint",
    "faceverse_model",
    "faceverse_api_key",
)


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tencent_endpoint: str | None = Field(default=None, max_length=512)
    tencent_region: str | None = Field(default=None, max_length=512)
    tencent_model: str | None = Field(default=None, max_length=512)
    tencent_secret_id: str | None = Field(default=None, max_length=1024)
    tencent_secret_key: str | None = Field(default=None, max_length=1024)
    pose_endpoint: str | None = Field(default=None, max_length=512)
    pose_model: str | None = Field(default=None, max_length=512)
    pose_api_key: str | None = Field(default=None, max_length=1024)
    seedream_endpoint: str | None = Field(default=None, max_length=512)
    seedream_model: str | None = Field(default=None, max_length=512)
    seedream_api_key: str | None = Field(default=None, max_length=1024)
    flux_endpoint: str | None = Field(default=None, max_length=512)
    flux_model: str | None = Field(default=None, max_length=512)
    flux_api_key: str | None = Field(default=None, max_length=1024)
    flux_max_endpoint: str | None = Field(default=None, max_length=512)
    flux_max_model: str | None = Field(default=None, max_length=512)
    flux_max_api_key: str | None = Field(default=None, max_length=1024)
    flux_klein_endpoint: str | None = Field(default=None, max_length=512)
    flux_klein_model: str | None = Field(default=None, max_length=512)
    flux_klein_api_key: str | None = Field(default=None, max_length=1024)
    flux_klein_9b_endpoint: str | None = Field(default=None, max_length=512)
    flux_klein_9b_model: str | None = Field(default=None, max_length=512)
    flux_klein_9b_api_key: str | None = Field(default=None, max_length=1024)
    gpt_image_endpoint: str | None = Field(default=None, max_length=512)
    gpt_image_model: str | None = Field(default=None, max_length=512)
    gpt_image_api_key: str | None = Field(default=None, max_length=1024)
    faceverse_endpoint: str | None = Field(default=None, max_length=512)
    faceverse_model: str | None = Field(default=None, max_length=512)
    faceverse_api_key: str | None = Field(default=None, max_length=1024)


def public_provider_settings(settings: Settings) -> dict:
    return {
        "tencent_endpoint": settings.tencent_endpoint,
        "tencent_region": settings.tencent_region,
        "tencent_model": settings.tencent_model,
        "tencent_secret_id_set": bool(settings.tencent_secret_id.get_secret_value()),
        "tencent_secret_key_set": bool(settings.tencent_secret_key.get_secret_value()),
        "pose_endpoint": settings.pose_endpoint,
        "pose_model": settings.pose_model,
        "pose_api_key_set": bool(settings.pose_api_key.get_secret_value()),
        "seedream_endpoint": settings.seedream_endpoint,
        "seedream_model": settings.seedream_model,
        "seedream_api_key_set": bool(settings.seedream_api_key.get_secret_value()),
        "flux_endpoint": settings.flux_endpoint,
        "flux_model": settings.flux_model,
        "flux_api_key_set": bool(settings.flux_api_key.get_secret_value()),
        "flux_max_endpoint": settings.flux_max_endpoint,
        "flux_max_model": settings.flux_max_model,
        "flux_max_api_key_set": bool(settings.flux_max_api_key.get_secret_value()),
        "flux_klein_endpoint": settings.flux_klein_endpoint,
        "flux_klein_model": settings.flux_klein_model,
        "flux_klein_api_key_set": bool(settings.flux_klein_api_key.get_secret_value()),
        "flux_klein_9b_endpoint": settings.flux_klein_9b_endpoint,
        "flux_klein_9b_model": settings.flux_klein_9b_model,
        "flux_klein_9b_api_key_set": bool(settings.flux_klein_9b_api_key.get_secret_value()),
        "gpt_image_endpoint": settings.gpt_image_endpoint,
        "gpt_image_model": settings.gpt_image_model,
        "gpt_image_api_key_set": bool(settings.gpt_image_api_key.get_secret_value()),
        "faceverse_endpoint": settings.faceverse_endpoint,
        "faceverse_model": settings.faceverse_model,
        "faceverse_api_key_set": bool(settings.faceverse_api_key.get_secret_value()),
    }


def validate_provider_update(
    settings: Settings, body: ProviderSettingsUpdate
) -> tuple[Settings, dict]:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{field} contains invalid control characters")
    values = settings.model_dump()
    values.update(changes)
    updated = Settings(_env_file=None, **values)
    return updated, changes


def save_provider_settings(path: Path, changes: dict) -> None:
    write_env_values(path, EDITABLE_FIELDS, changes)
