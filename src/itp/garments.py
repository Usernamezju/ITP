"""Merchant accounts, garment metrics, looks and body profiles on SQLite.

One database per concern lives under the data directory: ``merchants.sqlite3``
holds merchants, garments, garment images, looks and body profiles, while the
image bytes themselves stay in the shared asset store so the existing upload
path keeps owning files.  Every statement is parameterised, every write takes a
process lock so the quota check and its insert stay atomic, and an unknown id
comes back as ``None`` instead of raising.
"""

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

# --- metric vocabulary -------------------------------------------------------

CATEGORIES = ("上装", "下装", "外套", "鞋履", "配饰")
SILHOUETTES = ("修身", "标准", "宽松", "oversize")
STRETCHES = ("无弹", "微弹", "高弹")
LENGTH_TYPES = ("短款", "常规", "长款")
STYLES = ("通勤", "休闲", "街头", "运动", "度假", "复古", "极简", "学院")
SEASONS = ("春", "夏", "秋", "冬", "四季")
STATUSES = ("draft", "published")

MEASUREMENT_KEYS = ("shoulder_cm", "bust_cm", "waist_cm", "hip_cm", "length_cm", "hem_cm")
FIT_RANGE_BOUNDS = {
    "height_cm": (120.0, 220.0),
    "bust_cm": (60.0, 160.0),
    "waist_cm": (45.0, 150.0),
    "hip_cm": (60.0, 170.0),
    "shoulder_cm": (25.0, 70.0),
}
ATTRIBUTE_KEYS = ("silhouette", "stretch", "weight_gsm", "color", "length_type")
METRIC_FIELDS = (
    "category",
    "name",
    "sku",
    "brand",
    "price_cents",
    "measurements",
    "fit_ranges",
    "attributes",
    "style",
    "season",
    "occasion",
    "description",
    "tips",
    "status",
)
REQUIRED_METRIC_FIELDS = ("category", "name", "status")
SECTIONS = ("measurements", "fit_ranges", "attributes")
MEASUREMENT_MAX_CM = 300.0
WEIGHT_GSM_MIN = 20
WEIGHT_GSM_MAX = 2000
WEIGHT_GSM_MESSAGE = (
    f"attributes.weight_gsm 必须是 {WEIGHT_GSM_MIN} 到 {WEIGHT_GSM_MAX} 之间的整数"
)
PRICE_MAX_CENTS = 10**12
DESCRIPTION_MAX = 1000
TIPS_MAX = 3
TIP_MAX = 120
NAME_MAX = 200
SHORT_TEXT_MAX = 80
IMAGE_MAX_MB = 10
MAX_GARMENT_IMAGES = 8

# Chinese labels for the importer's form; kept next to the validators so the
# reference document below can never drift from what the API actually accepts.
MEASUREMENT_LABELS = {
    "shoulder_cm": "肩宽",
    "bust_cm": "胸围",
    "waist_cm": "腰围",
    "hip_cm": "臀围",
    "length_cm": "衣长",
    "hem_cm": "下摆",
}
FIT_RANGE_LABELS = {
    "height_cm": "适合身高",
    "bust_cm": "适合胸围",
    "waist_cm": "适合腰围",
    "hip_cm": "适合臀围",
    "shoulder_cm": "适合肩宽",
}
BODY_PROFILE_LABELS = {
    "height_cm": "身高",
    "weight_kg": "体重",
    "shoulder_cm": "肩宽",
    "bust_cm": "胸围",
    "waist_cm": "腰围",
    "hip_cm": "臀围",
}

# --- body profile vocabulary -------------------------------------------------

BODY_PROFILE_FIELDS = (
    "height_cm",
    "weight_kg",
    "shoulder_cm",
    "bust_cm",
    "waist_cm",
    "hip_cm",
)
BODY_PROFILE_BOUNDS = {
    "height_cm": (100.0, 250.0),
    "weight_kg": (20.0, 300.0),
    "shoulder_cm": (25.0, 70.0),
    "bust_cm": (60.0, 200.0),
    "waist_cm": (40.0, 200.0),
    "hip_cm": (60.0, 220.0),
}

# --- look vocabulary ---------------------------------------------------------

LOOK_STATUSES = STATUSES
PALETTE_MAX = 6
LOOK_ITEMS_MAX = 12
LOOK_NAME_MAX = 80
LOOK_STORY_MAX = 1000

LOOK_FIELDS = ("name", "story", "style", "season", "occasion", "palette", "status")

_COLOR = re.compile(r"^#[0-9a-f]{6}$")
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class AlreadyExists(ValueError):
    """A unique value (merchant name, sku) is already taken."""


class QuotaExceeded(ValueError):
    """The merchant already holds as many garments as its quota allows."""


def _has_control(text: str, *, allow_newlines: bool = False) -> bool:
    for char in text:
        code = ord(char)
        if code == 127 or (code < 32 and not (allow_newlines and char in "\n\t")):
            return True
    return False


def _text(
    value: Any,
    field: str,
    *,
    max_length: int,
    allow_empty: bool = True,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是文本")
    text = value.strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} 不能为空")
    if len(text) > max_length:
        raise ValueError(f"{field} 不能超过 {max_length} 个字符")
    if _has_control(text, allow_newlines=allow_newlines):
        raise ValueError(f"{field} 含有不可见控制字符")
    return text


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须是数字")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"{field} 必须是有效数字")
    return number


def _enum(value: Any, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} 取值必须是：{'、'.join(allowed)}")
    return value


def _optional_enum(value: Any, field: str, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    return _enum(value, field, allowed)


def _unknown_keys(payload: dict, allowed: tuple[str, ...]) -> list[str]:
    return sorted(set(payload) - set(allowed))


def _reject_unknown(payload: dict, allowed: tuple[str, ...], prefix: str = "") -> None:
    unknown = _unknown_keys(payload, allowed)
    if unknown:
        raise ValueError(f"未知的{prefix}字段：{'、'.join(unknown)}")


def _validate_measurements(section: Any) -> dict[str, float | None]:
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("measurements 必须是 JSON 对象")
    _reject_unknown(section, MEASUREMENT_KEYS, "尺寸")
    values: dict[str, float | None] = {key: None for key in MEASUREMENT_KEYS}
    for key, raw in section.items():
        if raw is None:
            continue
        number = _number(raw, f"measurements.{key}")
        if number <= 0 or number > MEASUREMENT_MAX_CM:
            raise ValueError(f"measurements.{key} 必须大于 0 且不超过 {MEASUREMENT_MAX_CM:g} cm")
        values[key] = number
    return values


def _validate_fit_ranges(section: Any) -> dict[str, list[float] | None]:
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("fit_ranges 必须是 JSON 对象")
    _reject_unknown(section, tuple(FIT_RANGE_BOUNDS), "适配区间")
    values: dict[str, list[float] | None] = {key: None for key in FIT_RANGE_BOUNDS}
    for key, raw in section.items():
        if raw is None:
            continue
        field = f"fit_ranges.{key}"
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError(f"{field} 必须是 [最小值, 最大值] 两元素数组")
        low = _number(raw[0], f"{field} 最小值")
        high = _number(raw[1], f"{field} 最大值")
        if low > high:
            raise ValueError(f"{field} 的最小值不能大于最大值")
        bound_low, bound_high = FIT_RANGE_BOUNDS[key]
        if low < bound_low or high > bound_high:
            raise ValueError(
                f"{field} 的两端必须落在 {bound_low:g}–{bound_high:g} cm 之间"
            )
        values[key] = [low, high]
    return values


def _validate_attributes(section: Any) -> dict[str, Any]:
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("attributes 必须是 JSON 对象")
    _reject_unknown(section, ATTRIBUTE_KEYS, "属性")
    values: dict[str, Any] = {key: None for key in ATTRIBUTE_KEYS}
    for key, raw in section.items():
        if raw is None:
            continue
        field = f"attributes.{key}"
        if key == "silhouette":
            values[key] = _enum(raw, field, SILHOUETTES)
        elif key == "stretch":
            values[key] = _enum(raw, field, STRETCHES)
        elif key == "length_type":
            values[key] = _enum(raw, field, LENGTH_TYPES)
        elif key == "color":
            if not isinstance(raw, str) or not _COLOR.match(raw):
                raise ValueError(f"{field} 必须是 #rrggbb 形式的小写十六进制颜色")
            values[key] = raw
        else:  # weight_gsm
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError(WEIGHT_GSM_MESSAGE)
            if raw < WEIGHT_GSM_MIN or raw > WEIGHT_GSM_MAX:
                raise ValueError(WEIGHT_GSM_MESSAGE)
            values[key] = raw
    return values


def _validate_tips(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("tips 必须是数组")
    if len(value) > TIPS_MAX:
        raise ValueError(f"tips 最多 {TIPS_MAX} 条")
    out = []
    for index, raw in enumerate(value):
        out.append(
            _text(raw, f"tips[{index}]", max_length=TIP_MAX, allow_empty=False)
        )
    return out


def normalize_metrics(payload: Any, base: dict | None = None) -> dict:
    """Validate a metrics document, merging it over ``base`` for patches.

    The result always carries the full key set, with ``null`` for values the
    merchant did not supply, so every API response has the same shape.
    """
    if not isinstance(payload, dict):
        raise ValueError("指标必须是 JSON 对象")
    _reject_unknown(payload, METRIC_FIELDS, "指标")

    current = dict(base or {})
    values: dict[str, Any] = {
        "category": current.get("category"),
        "name": current.get("name"),
        "sku": current.get("sku"),
        "brand": current.get("brand"),
        "price_cents": current.get("price_cents"),
        "style": current.get("style"),
        "season": current.get("season"),
        "occasion": current.get("occasion"),
        "description": current.get("description"),
        "tips": current.get("tips"),
        "status": current.get("status"),
        "measurements": dict(current.get("measurements") or {}),
        "fit_ranges": dict(current.get("fit_ranges") or {}),
        "attributes": dict(current.get("attributes") or {}),
    }

    for key in SECTIONS:
        if key not in payload:
            continue
        section = payload[key]
        if section is None:
            # An explicit null clears the whole section.
            merged_section: dict = {}
        else:
            if not isinstance(section, dict):
                raise ValueError(f"{key} 必须是 JSON 对象")
            # Patch semantics: a section merges into the stored one, so sending
            # one measurement keeps the rest, and a null entry clears that key.
            merged_section = {**(current.get(key) or {}), **section}
        if key == "measurements":
            values[key] = _validate_measurements(merged_section)
        elif key == "fit_ranges":
            values[key] = _validate_fit_ranges(merged_section)
        else:
            values[key] = _validate_attributes(merged_section)

    if "category" in payload:
        values["category"] = _enum(payload["category"], "category", CATEGORIES)
    if "name" in payload:
        values["name"] = _text(payload["name"], "name", max_length=NAME_MAX, allow_empty=False)
    if "status" in payload:
        values["status"] = _enum(payload["status"], "status", STATUSES)
    if "sku" in payload:
        raw = payload["sku"]
        values["sku"] = None if raw is None else _text(raw, "sku", max_length=SHORT_TEXT_MAX)
        if values["sku"] == "":
            values["sku"] = None
    if "brand" in payload:
        raw = payload["brand"]
        values["brand"] = None if raw is None else _text(raw, "brand", max_length=SHORT_TEXT_MAX)
    if "style" in payload:
        values["style"] = _optional_enum(payload["style"], "style", STYLES)
    if "season" in payload:
        values["season"] = _optional_enum(payload["season"], "season", SEASONS)
    if "occasion" in payload:
        raw = payload["occasion"]
        values["occasion"] = (
            None if raw is None else _text(raw, "occasion", max_length=SHORT_TEXT_MAX)
        )
    if "description" in payload:
        raw = payload["description"]
        values["description"] = (
            None
            if raw is None
            else _text(
                raw,
                "description",
                max_length=DESCRIPTION_MAX,
                allow_newlines=True,
            )
        )
    if "tips" in payload:
        values["tips"] = _validate_tips(payload["tips"])
    if "price_cents" in payload:
        raw = payload["price_cents"]
        if raw is None:
            values["price_cents"] = None
        else:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError("price_cents 必须是整数分")
            if raw < 0 or raw > PRICE_MAX_CENTS:
                raise ValueError(f"price_cents 必须在 0 到 {PRICE_MAX_CENTS} 之间")
            values["price_cents"] = raw

    for key in SECTIONS:
        if not isinstance(values[key], dict):
            values[key] = {}
    for field in REQUIRED_METRIC_FIELDS:
        if values.get(field) in (None, ""):
            raise ValueError(f"{field} 不能为空")
    return values


def normalize_body_profile(payload: Any, base: dict | None = None) -> dict:
    """Validate a body profile; every field is optional and may be nulled."""
    if not isinstance(payload, dict):
        raise ValueError("人体参数必须是 JSON 对象")
    _reject_unknown(payload, BODY_PROFILE_FIELDS, "人体参数")
    current = dict(base or {})
    values: dict[str, float | None] = {}
    for field in BODY_PROFILE_FIELDS:
        raw = payload[field] if field in payload else current.get(field)
        if raw is None:
            values[field] = None
            continue
        number = _number(raw, field)
        low, high = BODY_PROFILE_BOUNDS[field]
        if number < low or number > high:
            raise ValueError(f"{field} 必须在 {low:g} 到 {high:g} 之间")
        values[field] = number
    return values


def normalize_look(payload: Any, base: dict | None = None) -> dict:
    """Validate a look document; ``items`` is returned as a list of garment ids."""
    if not isinstance(payload, dict):
        raise ValueError("穿搭必须是 JSON 对象")
    _reject_unknown(payload, (*LOOK_FIELDS, "items"), "穿搭")
    current = dict(base or {})
    values: dict[str, Any] = {
        "name": current.get("name"),
        "story": current.get("story"),
        "style": current.get("style"),
        "season": current.get("season"),
        "occasion": current.get("occasion"),
        "palette": current.get("palette"),
        "status": current.get("status"),
    }
    if "name" in payload:
        values["name"] = _text(
            payload["name"], "name", max_length=LOOK_NAME_MAX, allow_empty=False
        )
    if "story" in payload:
        raw = payload["story"]
        values["story"] = (
            None
            if raw is None
            else _text(raw, "story", max_length=LOOK_STORY_MAX, allow_newlines=True)
        )
    if "style" in payload:
        values["style"] = _optional_enum(payload["style"], "style", STYLES)
    if "season" in payload:
        values["season"] = _optional_enum(payload["season"], "season", SEASONS)
    if "occasion" in payload:
        raw = payload["occasion"]
        values["occasion"] = (
            None if raw is None else _text(raw, "occasion", max_length=SHORT_TEXT_MAX)
        )
    if "status" in payload:
        values["status"] = _enum(payload["status"], "status", LOOK_STATUSES)
    if "palette" in payload:
        raw = payload["palette"]
        if raw is None:
            values["palette"] = None
        else:
            if not isinstance(raw, list):
                raise ValueError("palette 必须是颜色数组")
            if len(raw) > PALETTE_MAX:
                raise ValueError(f"palette 最多 {PALETTE_MAX} 个颜色")
            colors = []
            for index, color in enumerate(raw):
                if not isinstance(color, str) or not _COLOR.match(color):
                    raise ValueError(
                        f"palette[{index}] 必须是 #rrggbb 形式的小写十六进制颜色"
                    )
                colors.append(color)
            values["palette"] = colors
    items = current.get("items")
    if "items" in payload:
        raw = payload["items"]
        if raw is None:
            items = []
        else:
            if not isinstance(raw, list):
                raise ValueError("items 必须是商品 id 数组")
            if len(raw) > LOOK_ITEMS_MAX:
                raise ValueError(f"items 最多 {LOOK_ITEMS_MAX} 件")
            ids = []
            for index, item in enumerate(raw):
                if not isinstance(item, str) or not _ID_PATTERN.match(item):
                    raise ValueError(f"items[{index}] 必须是 32 位小写十六进制商品 id")
                if item in ids:
                    raise ValueError(f"items[{index}] 重复")
                ids.append(item)
            items = ids
    values["items"] = list(items or [])
    if not values.get("name"):
        raise ValueError("name 不能为空")
    if not values.get("status"):
        raise ValueError("status 不能为空")
    return values


def options_document() -> dict[str, Any]:
    """Every value the importer accepts, taken straight from the validators.

    The console page (and any third-party merchant client) builds its form and
    validates locally from this, so a form cannot accept something the API would
    then reject with 422.
    """
    return {
        "categories": list(CATEGORIES),
        "styles": list(STYLES),
        "seasons": list(SEASONS),
        "silhouettes": list(SILHOUETTES),
        "stretches": list(STRETCHES),
        "length_types": list(LENGTH_TYPES),
        "statuses": list(STATUSES),
        "measurements": [
            {"key": key, "label": MEASUREMENT_LABELS[key], "max": MEASUREMENT_MAX_CM}
            for key in MEASUREMENT_KEYS
        ],
        "fit_ranges": [
            {
                "key": key,
                "label": FIT_RANGE_LABELS[key],
                "min": FIT_RANGE_BOUNDS[key][0],
                "max": FIT_RANGE_BOUNDS[key][1],
            }
            for key in FIT_RANGE_LABELS
        ],
        "body_profile": [
            {
                "key": key,
                "label": BODY_PROFILE_LABELS[key],
                "min": BODY_PROFILE_BOUNDS[key][0],
                "max": BODY_PROFILE_BOUNDS[key][1],
            }
            for key in BODY_PROFILE_FIELDS
        ],
        "limits": {
            "name_max": NAME_MAX,
            "short_text_max": SHORT_TEXT_MAX,
            "description_max": DESCRIPTION_MAX,
            "tips_max": TIPS_MAX,
            "tip_max": TIP_MAX,
            "price_max_cents": PRICE_MAX_CENTS,
            "weight_gsm_min": WEIGHT_GSM_MIN,
            "weight_gsm_max": WEIGHT_GSM_MAX,
            "image_max_mb": IMAGE_MAX_MB,
            "images_max": MAX_GARMENT_IMAGES,
            "palette_max": PALETTE_MAX,
            "look_items_max": LOOK_ITEMS_MAX,
            "look_name_max": LOOK_NAME_MAX,
            "look_story_max": LOOK_STORY_MAX,
        },
    }


def public_merchant(merchant: dict, *, garment_count: int | None = None) -> dict:
    document = {
        "merchant_id": merchant["id"],
        "name": merchant["name"],
        "display_name": merchant["display_name"],
        "contact": merchant["contact"],
        "created": merchant["created"],
        "quota": merchant["quota"],
    }
    if garment_count is not None:
        document["garment_count"] = garment_count
    return document


def public_image(image: dict) -> dict:
    return {
        "id": image["id"],
        "url": f"/api/garment-images/{image['id']}",
        "position": image["position"],
        "created": image["created"],
    }


def public_garment(garment: dict, images: list[dict] | None = None) -> dict:
    return {
        "id": garment["id"],
        "merchant_id": garment["merchant_id"],
        "status": garment["status"],
        "created": garment["created"],
        "updated": garment["updated"],
        "metrics": garment["metrics"],
        "images": [public_image(image) for image in (images or [])],
    }


def public_look(look: dict, garments: list[dict] | None = None,
                images: dict[str, list[dict]] | None = None) -> dict:
    gallery = images or {}
    return {
        "id": look["id"],
        "merchant_id": look["merchant_id"],
        "name": look["name"],
        "story": look["story"],
        "style": look["style"],
        "season": look["season"],
        "occasion": look["occasion"],
        "palette": look["palette"],
        "status": look["status"],
        "created": look["created"],
        "items": [
            public_garment(garment, gallery.get(garment["id"], []))
            for garment in (garments or [])
        ],
    }


def public_body_profile(profile: dict | None, job_id: str | None = None) -> dict:
    profile = profile or {}
    document = {"job_id": profile.get("job_id", job_id), "updated": profile.get("updated")}
    for field in BODY_PROFILE_FIELDS:
        document[field] = profile.get(field)
    return document


class MerchantStore:
    """SQLite storage for merchant accounts, garments, looks and body profiles."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "merchants.sqlite3"
        self._lock = threading.Lock()
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS merchants (
                    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, display_name TEXT,
                    contact TEXT, password_hash TEXT, created REAL,
                    disabled INTEGER DEFAULT 0, quota INTEGER
                );
                CREATE TABLE IF NOT EXISTS garments (
                    id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, metrics TEXT NOT NULL,
                    status TEXT, created REAL, updated REAL
                );
                CREATE TABLE IF NOT EXISTS garment_images (
                    id TEXT PRIMARY KEY, garment_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                    position INTEGER, created REAL
                );
                CREATE TABLE IF NOT EXISTS looks (
                    id TEXT PRIMARY KEY, merchant_id TEXT, name TEXT, story TEXT, style TEXT,
                    season TEXT, occasion TEXT, palette TEXT, status TEXT, created REAL
                );
                CREATE TABLE IF NOT EXISTS look_items (
                    look_id TEXT, garment_id TEXT, position INTEGER,
                    PRIMARY KEY (look_id, position)
                );
                CREATE TABLE IF NOT EXISTS body_profiles (
                    id TEXT PRIMARY KEY, job_id TEXT, payload TEXT, created REAL, updated REAL
                );
                CREATE INDEX IF NOT EXISTS garments_by_merchant
                    ON garments (merchant_id, created);
                CREATE INDEX IF NOT EXISTS garment_images_by_garment
                    ON garment_images (garment_id, position);
                CREATE INDEX IF NOT EXISTS looks_by_merchant
                    ON looks (merchant_id, created);
                CREATE INDEX IF NOT EXISTS body_profiles_by_job
                    ON body_profiles (job_id);
                """
            )

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    # ------------------------------------------------------------------ merchants

    def create_merchant(
        self,
        *,
        name: str,
        display_name: str,
        contact: str,
        password_hash: str,
        quota: int,
    ) -> dict:
        merchant_id = uuid4().hex
        created = time.time()
        with self._lock, self.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO merchants "
                    "(id, name, display_name, contact, password_hash, created, disabled, quota) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                    (merchant_id, name, display_name, contact, password_hash, created, quota),
                )
            except sqlite3.IntegrityError as exc:
                raise AlreadyExists("商家名称已被占用") from exc
        return self.merchant(merchant_id)

    def merchant(self, merchant_id: str) -> dict | None:
        if not merchant_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, name, display_name, contact, password_hash, created, disabled, quota "
                "FROM merchants WHERE id = ?",
                (merchant_id,),
            ).fetchone()
        return self._merchant_row(row)

    def merchant_by_name(self, name: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, name, display_name, contact, password_hash, created, disabled, quota "
                "FROM merchants WHERE name = ?",
                (name,),
            ).fetchone()
        return self._merchant_row(row)

    def set_password(self, merchant_id: str, password_hash: str) -> bool:
        """Replace the password hash; tokens issued under the old one stop working."""
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                "UPDATE merchants SET password_hash = ? WHERE id = ?",
                (password_hash, merchant_id),
            )
        return cursor.rowcount > 0

    def set_disabled(self, merchant_id: str, disabled: bool) -> bool:
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                "UPDATE merchants SET disabled = ? WHERE id = ?",
                (1 if disabled else 0, merchant_id),
            )
        return cursor.rowcount > 0

    def set_quota(self, merchant_id: str, quota: int) -> bool:
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                "UPDATE merchants SET quota = ? WHERE id = ?", (int(quota), merchant_id)
            )
        return cursor.rowcount > 0

    @staticmethod
    def _merchant_row(row) -> dict | None:
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "display_name": row[2],
            "contact": row[3],
            "password_hash": row[4],
            "created": row[5],
            "disabled": bool(row[6]),
            "quota": row[7] if row[7] is not None else 0,
        }

    # ------------------------------------------------------------------- garments

    def create_garment(self, merchant_id: str, metrics: dict) -> dict:
        """Insert one garment, refusing to pass the merchant's quota."""
        with self._lock, self.connect() as conn:
            merchant = conn.execute(
                "SELECT quota FROM merchants WHERE id = ?", (merchant_id,)
            ).fetchone()
            if merchant is None:
                raise ValueError("商家不存在")
            quota = merchant[0] if merchant[0] is not None else 0
            used = conn.execute(
                "SELECT COUNT(*) FROM garments WHERE merchant_id = ?", (merchant_id,)
            ).fetchone()[0]
            if used >= quota:
                raise QuotaExceeded(f"商品数量已达配额上限（{quota} 件）")
            if metrics.get("sku"):
                clash = conn.execute(
                    "SELECT 1 FROM garments WHERE merchant_id = ? AND "
                    "json_extract(metrics, '$.sku') = ?",
                    (merchant_id, metrics["sku"]),
                ).fetchone()
                if clash:
                    raise AlreadyExists("该货号已存在")
            garment_id = uuid4().hex
            created = time.time()
            conn.execute(
                "INSERT INTO garments (id, merchant_id, metrics, status, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    garment_id,
                    merchant_id,
                    json.dumps(metrics, ensure_ascii=False),
                    metrics.get("status"),
                    created,
                    created,
                ),
            )
        return self.garment(garment_id)

    def garment(self, garment_id: str) -> dict | None:
        if not garment_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, merchant_id, metrics, status, created, updated FROM garments "
                "WHERE id = ?",
                (garment_id,),
            ).fetchone()
        return self._garment_row(row)

    def garment_for(self, merchant_id: str, garment_id: str) -> dict | None:
        """Ownership-scoped read: another merchant's garment is simply absent."""
        garment = self.garment(garment_id)
        if not garment or garment["merchant_id"] != merchant_id:
            return None
        return garment

    def count_garments(self, merchant_id: str) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM garments WHERE merchant_id = ?", (merchant_id,)
            ).fetchone()[0]

    def list_garments(
        self,
        merchant_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[int, list[dict]]:
        clauses = ["merchant_id = ?"]
        params: list[Any] = [merchant_id]
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM garments WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, merchant_id, metrics, status, created, updated FROM garments "
                f"WHERE {where} ORDER BY created DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return total, [self._garment_row(row) for row in rows]

    def list_published_garments(
        self,
        *,
        style: str | None = None,
        season: str | None = None,
        occasion: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        clauses = ["status = 'published'"]
        params: list[Any] = []
        for column, value in (
            ("style", style),
            ("season", season),
            ("occasion", occasion),
            ("category", category),
        ):
            if value:
                clauses.append(f"json_extract(metrics, '$.{column}') = ?")
                params.append(value)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM garments WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, merchant_id, metrics, status, created, updated FROM garments "
                f"WHERE {where} ORDER BY created DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return total, [self._garment_row(row) for row in rows]

    def update_garment(self, merchant_id: str, garment_id: str, metrics: dict) -> dict | None:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT merchant_id FROM garments WHERE id = ?", (garment_id,)
            ).fetchone()
            if row is None or row[0] != merchant_id:
                return None
            if metrics.get("sku"):
                clash = conn.execute(
                    "SELECT 1 FROM garments WHERE merchant_id = ? AND id != ? AND "
                    "json_extract(metrics, '$.sku') = ?",
                    (merchant_id, garment_id, metrics["sku"]),
                ).fetchone()
                if clash:
                    raise AlreadyExists("该货号已存在")
            conn.execute(
                "UPDATE garments SET metrics = ?, status = ?, updated = ? WHERE id = ?",
                (
                    json.dumps(metrics, ensure_ascii=False),
                    metrics.get("status"),
                    time.time(),
                    garment_id,
                ),
            )
        return self.garment(garment_id)

    def delete_garment(self, merchant_id: str, garment_id: str) -> list[str] | None:
        """Delete a garment, its image rows and its look memberships.

        Returns the asset ids whose files the caller should remove, or ``None``
        when the garment is not this merchant's.
        """
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT merchant_id FROM garments WHERE id = ?", (garment_id,)
            ).fetchone()
            if row is None or row[0] != merchant_id:
                return None
            asset_ids = [
                item[0]
                for item in conn.execute(
                    "SELECT asset_id FROM garment_images WHERE garment_id = ?", (garment_id,)
                ).fetchall()
            ]
            conn.execute("DELETE FROM garment_images WHERE garment_id = ?", (garment_id,))
            conn.execute("DELETE FROM look_items WHERE garment_id = ?", (garment_id,))
            conn.execute("DELETE FROM garments WHERE id = ?", (garment_id,))
        return asset_ids

    @staticmethod
    def _garment_row(row) -> dict | None:
        if row is None:
            return None
        return {
            "id": row[0],
            "merchant_id": row[1],
            "metrics": json.loads(row[2]),
            "status": row[3],
            "created": row[4],
            "updated": row[5],
        }

    # -------------------------------------------------------------- garment images

    def add_image(self, garment_id: str, asset_id: str) -> dict:
        with self._lock, self.connect() as conn:
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM garment_images WHERE garment_id = ?",
                (garment_id,),
            ).fetchone()[0]
            image_id = uuid4().hex
            created = time.time()
            conn.execute(
                "INSERT INTO garment_images (id, garment_id, asset_id, position, created) "
                "VALUES (?, ?, ?, ?, ?)",
                (image_id, garment_id, asset_id, position, created),
            )
        return self.image(image_id)

    def image(self, image_id: str) -> dict | None:
        if not _ID_PATTERN.match(image_id or ""):
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, garment_id, asset_id, position, created FROM garment_images "
                "WHERE id = ?",
                (image_id,),
            ).fetchone()
        return self._image_row(row)

    def images_for(self, garment_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, garment_id, asset_id, position, created FROM garment_images "
                "WHERE garment_id = ? ORDER BY position, created",
                (garment_id,),
            ).fetchall()
        return [self._image_row(row) for row in rows]

    def images_for_many(self, garment_ids: list[str]) -> dict[str, list[dict]]:
        if not garment_ids:
            return {}
        placeholders = ",".join("?" for _ in garment_ids)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, garment_id, asset_id, position, created FROM garment_images "
                f"WHERE garment_id IN ({placeholders}) ORDER BY garment_id, position, created",
                garment_ids,
            ).fetchall()
        grouped: dict[str, list[dict]] = {garment_id: [] for garment_id in garment_ids}
        for row in rows:
            image = self._image_row(row)
            grouped[image["garment_id"]].append(image)
        return grouped

    def delete_image(self, merchant_id: str, garment_id: str, image_id: str) -> str | None:
        """Delete one image row; returns its asset id for file cleanup."""
        with self._lock, self.connect() as conn:
            owner = conn.execute(
                "SELECT merchant_id FROM garments WHERE id = ?", (garment_id,)
            ).fetchone()
            if owner is None or owner[0] != merchant_id:
                return None
            row = conn.execute(
                "SELECT asset_id FROM garment_images WHERE id = ? AND garment_id = ?",
                (image_id, garment_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM garment_images WHERE id = ?", (image_id,))
        return row[0]

    @staticmethod
    def _image_row(row) -> dict | None:
        if row is None:
            return None
        return {
            "id": row[0],
            "garment_id": row[1],
            "asset_id": row[2],
            "position": row[3],
            "created": row[4],
        }

    # ---------------------------------------------------------------------- looks

    def create_look(self, merchant_id: str, look: dict) -> dict:
        items = list(look.get("items") or [])
        self._check_members(merchant_id, items)
        look_id = uuid4().hex
        created = time.time()
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO looks (id, merchant_id, name, story, style, season, occasion, "
                "palette, status, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    look_id,
                    merchant_id,
                    look["name"],
                    look.get("story"),
                    look.get("style"),
                    look.get("season"),
                    look.get("occasion"),
                    json.dumps(look.get("palette"), ensure_ascii=False),
                    look.get("status"),
                    created,
                ),
            )
            for position, garment_id in enumerate(items):
                conn.execute(
                    "INSERT INTO look_items (look_id, garment_id, position) VALUES (?, ?, ?)",
                    (look_id, garment_id, position),
                )
        return self.look(look_id)

    def look(self, look_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, merchant_id, name, story, style, season, occasion, palette, "
                "status, created FROM looks WHERE id = ?",
                (look_id,),
            ).fetchone()
            look = self._look_row(row)
            if look is None:
                return None
            look["items"] = self._look_items(conn, look_id)
        return look

    def look_for(self, merchant_id: str, look_id: str) -> dict | None:
        look = self.look(look_id)
        if not look or look["merchant_id"] != merchant_id:
            return None
        return look

    def list_looks(
        self,
        merchant_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM looks WHERE merchant_id = ?", (merchant_id,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, merchant_id, name, story, style, season, occasion, palette, "
                "status, created FROM looks WHERE merchant_id = ? "
                "ORDER BY created DESC, id DESC LIMIT ? OFFSET ?",
                (merchant_id, limit, offset),
            ).fetchall()
            looks = []
            for row in rows:
                look = self._look_row(row)
                look["items"] = self._look_items(conn, look["id"])
                looks.append(look)
        return total, looks

    def list_published_looks(
        self,
        *,
        style: str | None = None,
        season: str | None = None,
        occasion: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[dict]]:
        clauses = ["status = 'published'"]
        params: list[Any] = []
        for column, value in (("style", style), ("season", season), ("occasion", occasion)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM looks WHERE {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, merchant_id, name, story, style, season, occasion, palette, "
                f"status, created FROM looks WHERE {where} "
                "ORDER BY created DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            looks = []
            for row in rows:
                look = self._look_row(row)
                look["items"] = self._look_items(conn, look["id"])
                looks.append(look)
        return total, looks

    def update_look(self, merchant_id: str, look_id: str, look: dict) -> dict | None:
        items = list(look.get("items") or [])
        with self._lock, self.connect() as conn:
            owner = conn.execute(
                "SELECT merchant_id FROM looks WHERE id = ?", (look_id,)
            ).fetchone()
            if owner is None or owner[0] != merchant_id:
                return None
        self._check_members(merchant_id, items)
        with self._lock, self.connect() as conn:
            conn.execute(
                "UPDATE looks SET name = ?, story = ?, style = ?, season = ?, occasion = ?, "
                "palette = ?, status = ? WHERE id = ?",
                (
                    look["name"],
                    look.get("story"),
                    look.get("style"),
                    look.get("season"),
                    look.get("occasion"),
                    json.dumps(look.get("palette"), ensure_ascii=False),
                    look.get("status"),
                    look_id,
                ),
            )
            conn.execute("DELETE FROM look_items WHERE look_id = ?", (look_id,))
            for position, garment_id in enumerate(items):
                conn.execute(
                    "INSERT INTO look_items (look_id, garment_id, position) VALUES (?, ?, ?)",
                    (look_id, garment_id, position),
                )
        return self.look(look_id)

    def delete_look(self, merchant_id: str, look_id: str) -> bool:
        with self._lock, self.connect() as conn:
            owner = conn.execute(
                "SELECT merchant_id FROM looks WHERE id = ?", (look_id,)
            ).fetchone()
            if owner is None or owner[0] != merchant_id:
                return False
            conn.execute("DELETE FROM look_items WHERE look_id = ?", (look_id,))
            conn.execute("DELETE FROM looks WHERE id = ?", (look_id,))
        return True

    def _check_members(self, merchant_id: str, items: list[str]) -> None:
        if not items:
            return
        placeholders = ",".join("?" for _ in items)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, merchant_id FROM garments "
                f"WHERE id IN ({placeholders})",
                items,
            ).fetchall()
        owners = {row[0]: row[1] for row in rows}
        for garment_id in items:
            if garment_id not in owners:
                raise ValueError("穿搭成员商品不存在")
            if owners[garment_id] != merchant_id:
                raise ValueError("穿搭成员必须是自己的商品")

    def garments_by_ids(
        self, garment_ids: list[str], *, published_only: bool = False
    ) -> list[dict]:
        if not garment_ids:
            return []
        placeholders = ",".join("?" for _ in garment_ids)
        clause = f"id IN ({placeholders})"
        if published_only:
            clause += " AND status = 'published'"
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, merchant_id, metrics, status, created, updated FROM garments "
                f"WHERE {clause}",
                garment_ids,
            ).fetchall()
        found = {row[0]: self._garment_row(row) for row in rows}
        return [found[item] for item in garment_ids if item in found]

    def _look_items(self, conn, look_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT garment_id FROM look_items WHERE look_id = ? ORDER BY position",
            (look_id,),
        ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _look_row(row) -> dict | None:
        if row is None:
            return None
        palette = json.loads(row[7]) if row[7] else None
        return {
            "id": row[0],
            "merchant_id": row[1],
            "name": row[2],
            "story": row[3],
            "style": row[4],
            "season": row[5],
            "occasion": row[6],
            "palette": palette,
            "status": row[8],
            "created": row[9],
        }

    # ------------------------------------------------------------- body profiles

    def save_body_profile(self, payload: dict, job_id: str | None = None) -> dict:
        now = time.time()
        with self._lock, self.connect() as conn:
            if job_id:
                row = conn.execute(
                    "SELECT id FROM body_profiles WHERE job_id = ?", (job_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM body_profiles WHERE job_id IS NULL ORDER BY updated DESC"
                ).fetchone()
            if row:
                conn.execute(
                    "UPDATE body_profiles SET payload = ?, updated = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), now, row[0]),
                )
                profile_id = row[0]
            else:
                profile_id = uuid4().hex
                conn.execute(
                    "INSERT INTO body_profiles (id, job_id, payload, created, updated) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        profile_id,
                        job_id,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        return self._load_body_profile(profile_id)

    def body_profile(self, job_id: str | None = None, *, fallback: bool = True) -> dict | None:
        """Read one profile: the job's own row, else the newest default.

        Without a ``job_id`` the default profile *is* the row whose ``job_id``
        is null, so it is returned whether or not ``fallback`` is set.  With a
        ``job_id``, ``fallback=False`` returns only that job's own row, which
        the update path uses as its merge base so a partial write never copies
        the default profile into a job-specific one.
        """
        with self.connect() as conn:
            if job_id:
                row = conn.execute(
                    "SELECT id FROM body_profiles WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None and fallback:
                    row = conn.execute(
                        "SELECT id FROM body_profiles WHERE job_id IS NULL ORDER BY updated DESC"
                    ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM body_profiles WHERE job_id IS NULL ORDER BY updated DESC"
                ).fetchone()
            if row is None:
                return None
        return self._load_body_profile(row[0])

    def _load_body_profile(self, profile_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id, job_id, payload, created, updated FROM body_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[2]) if row[2] else {}
        document = {
            "id": row[0],
            "job_id": row[1],
            "created": row[3],
            "updated": row[4],
        }
        document.update({field: payload.get(field) for field in BODY_PROFILE_FIELDS})
        return document
