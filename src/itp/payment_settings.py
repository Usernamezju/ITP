"""Admin-managed payment credentials for the real Alipay and WeChat channels.

Everything here is structural validation and safe writing: key material never
leaves the server.  ``public_payment_settings`` reports only whether a value is
configured plus the non-secret key identifiers an operator needs to rotate a
platform key, and the writes reuse the audited atomic ``.env`` rewrite.
"""

import re

from pydantic import BaseModel, ConfigDict, Field

from itp.config import Settings
from itp.payments import MANUAL_QR_FIELDS, PaymentError, private_key, public_key
from itp.provider_settings import write_env_values

# Only these settings keys may be written from the operator console.  The
# WeChat platform keys are one JSON object so the whole dict is replaced or
# kept atomically instead of leaving a half-updated key set behind.
PAYMENT_FIELDS = (
    "alipay_app_id",
    "alipay_seller_id",
    "alipay_private_key",
    "alipay_public_key",
    "wechat_app_id",
    "wechat_mch_id",
    "wechat_merchant_serial",
    "wechat_private_key",
    "wechat_api_v3_key",
    "wechat_platform_keys",
    # Manual collection stores only the switch and the uploaded file names;
    # the pictures themselves live under data/payment/manual.
    "payment_manual_enabled",
    "payment_manual_wechat_qr",
    "payment_manual_alipay_qr",
)

MAX_IDENTIFIER = 64
MAX_SECRET = 8192
KEY_ID = re.compile(r"^[A-Za-z0-9_-]+$")
IDENTIFIERS = (
    "alipay_app_id",
    "alipay_seller_id",
    "wechat_app_id",
    "wechat_mch_id",
    "wechat_merchant_serial",
)
SECRETS = (
    "alipay_private_key",
    "alipay_public_key",
    "wechat_private_key",
    "wechat_api_v3_key",
)


class PaymentSettingsUpdate(BaseModel):
    """``None`` keeps the stored value; an empty string clears it."""

    model_config = ConfigDict(extra="forbid")

    alipay_app_id: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    alipay_seller_id: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    alipay_private_key: str | None = Field(default=None, max_length=MAX_SECRET)
    alipay_public_key: str | None = Field(default=None, max_length=MAX_SECRET)
    wechat_app_id: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    wechat_mch_id: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    wechat_merchant_serial: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    wechat_private_key: str | None = Field(default=None, max_length=MAX_SECRET)
    wechat_api_v3_key: str | None = Field(default=None, max_length=MAX_SECRET)
    # One platform key at a time: the pair is upserted into the stored set, and
    # an explicit id removes a rotated key.
    wechat_platform_key_id: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    wechat_platform_public_key: str | None = Field(default=None, max_length=MAX_SECRET)
    wechat_platform_key_remove: str | None = Field(default=None, max_length=MAX_IDENTIFIER)
    # Manual collection: on/off, and which uploaded code to clear.
    payment_manual_enabled: bool | None = None
    payment_manual_clear: str | None = Field(default=None, max_length=16)


def _reject_controls(field: str, value: str) -> None:
    """PEM material keeps its line breaks; every other control character is refused."""
    for char in value:
        if char in "\n\r":
            continue
        if ord(char) < 32 or ord(char) == 127:
            raise ValueError(f"{field} 含非法控制字符")


def _check_identifier(field: str, value: str) -> str:
    value = value.strip()
    if value and not KEY_ID.match(value):
        raise ValueError(f"{field} 只能包含字母、数字、下划线或短横线")
    return value


def validate_payment_update(
    settings: Settings, body: PaymentSettingsUpdate
) -> tuple[Settings, dict]:
    """Return the updated settings and exactly the ``.env`` keys to rewrite."""
    provided = body.model_dump(exclude_unset=True, exclude_none=True)
    changes: dict = {}
    for field in (*IDENTIFIERS, *SECRETS):
        if field not in provided:
            continue
        value = provided[field]
        _reject_controls(field, value)
        changes[field] = _check_identifier(field, value) if field in IDENTIFIERS else value

    # Plain strings, so the whole dict can be JSON-encoded into the settings file.
    keys = {
        name: secret.get_secret_value() for name, secret in settings.wechat_platform_keys.items()
    }
    remove = provided.get("wechat_platform_key_remove", "")
    if remove:
        keys.pop(_check_identifier("wechat_platform_key_remove", remove), None)
    key_id = provided.get("wechat_platform_key_id", "") or ""
    key_pem = provided.get("wechat_platform_public_key", "") or ""
    if bool(key_id) != bool(key_pem):
        raise ValueError("请同时填写微信支付公钥 ID 与公钥内容")
    if key_id and key_pem:
        _reject_controls("wechat_platform_public_key", key_pem)
        keys[_check_identifier("wechat_platform_key_id", key_id)] = key_pem
    if keys != settings.wechat_platform_keys:
        changes["wechat_platform_keys"] = keys

    if "payment_manual_enabled" in provided:
        changes["payment_manual_enabled"] = bool(provided["payment_manual_enabled"])
    clear = provided.get("payment_manual_clear", "") or ""
    if clear:
        field = MANUAL_QR_FIELDS.get("manual_" + clear)
        if not field:
            raise ValueError("只能移除微信或支付宝收款码")
        if not getattr(settings, field):
            raise ValueError("该收款码尚未上传")
        # The file itself is removed by the console; only the key is cleared
        # here, and only that channel stops being offered.
        changes[field] = ""

    values = settings.model_dump()
    values.update(changes)
    updated = Settings(_env_file=None, **values)
    validate_material(updated)
    return updated, changes


def validate_material(settings: Settings) -> None:
    """Reject unusable key material before anything reaches the server file."""
    checks = (
        ("alipay_private_key", private_key, "支付宝应用私钥无效：请填写 2048 位以上的 RSA 私钥"),
        ("alipay_public_key", public_key, "支付宝公钥无效：请填写支付宝公钥或应用公钥证书"),
        ("wechat_private_key", private_key, "微信支付商户私钥无效：请填写 2048 位以上的 RSA 私钥"),
    )
    for field, loader, message in checks:
        if getattr(settings, field).get_secret_value():
            try:
                loader(getattr(settings, field))
            except PaymentError as exc:
                raise ValueError(message) from exc
    api_key = settings.wechat_api_v3_key.get_secret_value()
    if api_key and len(api_key.encode()) != 32:
        raise ValueError("微信支付 APIv3 密钥必须是 32 个字符")
    for identifier, pem in settings.wechat_platform_keys.items():
        if not KEY_ID.match(identifier):
            raise ValueError("微信支付公钥 ID 只能包含字母、数字、下划线或短横线")
        try:
            public_key(pem)
        except PaymentError as exc:
            raise ValueError(f"微信支付公钥（{identifier}）无效：请填写公钥或平台证书") from exc


def public_payment_settings(settings: Settings) -> dict:
    """What the console may see: identifiers are reported as configured or not."""
    return {
        "alipay": {
            "app_id_set": bool(settings.alipay_app_id),
            "seller_id_set": bool(settings.alipay_seller_id),
            "private_key_set": bool(settings.alipay_private_key.get_secret_value()),
            "public_key_set": bool(settings.alipay_public_key.get_secret_value()),
        },
        "wechat": {
            "app_id_set": bool(settings.wechat_app_id),
            "mch_id_set": bool(settings.wechat_mch_id),
            "merchant_serial_set": bool(settings.wechat_merchant_serial),
            "private_key_set": bool(settings.wechat_private_key.get_secret_value()),
            "api_v3_key_set": bool(settings.wechat_api_v3_key.get_secret_value()),
            # Key identifiers are public (they travel in callback headers) and
            # an operator needs them to drop a rotated key.
            "platform_key_ids": sorted(settings.wechat_platform_keys),
        },
        # Manual collection: the switch and whether each code is uploaded.  The
        # file key stays server-side; the console shows the picture instead.
        "manual": {
            "enabled": bool(settings.payment_manual_enabled),
            "wechat_qr_set": bool(settings.payment_manual_wechat_qr),
            "alipay_qr_set": bool(settings.payment_manual_alipay_qr),
        },
    }


def save_payment_settings(path, changes: dict) -> None:
    write_env_values(path, PAYMENT_FIELDS, changes)
