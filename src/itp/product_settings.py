"""Admin-managed configuration of the product-image vision model.

Same audited path as the payment credentials: structural validation, an atomic
rewrite of only these keys in the server settings file, and a public view that
reports whether a key is configured instead of the key itself.  The operator can
therefore switch provider, endpoint or model from the console without a shell.
"""

import re

from pydantic import BaseModel, ConfigDict, Field

from itp.config import Settings
from itp.provider_settings import write_env_values

PRODUCT_FIELDS = ("product_ai_endpoint", "product_ai_model", "product_ai_api_key")
MAX_ENDPOINT = 512
MAX_MODEL = 80
MAX_KEY = 1024
# Model ids seen in the wild: letters, digits, dot, dash, underscore, colon, slash.
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]+$")


class ProductSettingsUpdate(BaseModel):
    """``None`` keeps the stored value; an empty string clears it."""

    model_config = ConfigDict(extra="forbid")

    product_ai_endpoint: str | None = Field(default=None, max_length=MAX_ENDPOINT)
    product_ai_model: str | None = Field(default=None, max_length=MAX_MODEL)
    product_ai_api_key: str | None = Field(default=None, max_length=MAX_KEY)


def _reject_controls(field: str, value: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} 含非法控制字符")


def validate_product_update(
    settings: Settings, body: ProductSettingsUpdate
) -> tuple[Settings, dict]:
    """Return the updated settings and exactly the ``.env`` keys to rewrite."""
    changes = {
        field: value.strip() if field != "product_ai_api_key" else value
        for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items()
    }
    for field, value in changes.items():
        _reject_controls(field, value)
    model = changes.get("product_ai_model")
    if model is not None and model and not MODEL_ID.match(model):
        raise ValueError("模型名只能包含字母、数字、点、短横线、下划线、冒号或斜杠")
    values = settings.model_dump()
    values.update(changes)
    # The Settings validator owns the endpoint rules (HTTPS chat/completions).
    updated = Settings(_env_file=None, **values)
    return updated, changes


def public_product_settings(settings: Settings) -> dict:
    """Endpoint and model are operator-facing values; the key is only a flag."""
    return {
        "endpoint": settings.product_ai_endpoint,
        "model": settings.product_ai_model,
        "key_set": bool(settings.product_ai_api_key.get_secret_value()),
        "key_from_pose": bool(
            not settings.product_ai_api_key.get_secret_value()
            and settings.pose_api_key.get_secret_value()
        ),
        "ready": settings.product_ai_ready,
    }


def save_product_settings(path, changes: dict) -> None:
    write_env_values(path, PRODUCT_FIELDS, changes)
