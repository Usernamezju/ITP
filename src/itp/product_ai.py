"""Read a product picture from a merchant link and describe it for the card.

The merchant pastes a shop or image URL; the server fetches it under strict
guards (http(s) only, no private addresses, bounded size, bounded redirects),
downscales it into a metadata-free preview, asks the configured vision model for
structured fields, and keeps only values the product API would accept anyway.
Nothing is stored here: the console fills its form and the merchant decides what
to publish.
"""

import base64
import io
import ipaddress
import json
import logging
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from itp.config import Settings
from itp.garments import (
    CATEGORIES,
    LENGTH_TYPES,
    SEASONS,
    SHORT_TEXT_MAX,
    SILHOUETTES,
    STRETCHES,
    STYLES,
)

logger = logging.getLogger(__name__)

MAX_LINK_BYTES = 8 * 1024 * 1024
MAX_HTML_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
PREVIEW_SIZE = 1024
PREVIEW_QUALITY = 88
FETCH_TIMEOUT = 12.0
AI_TIMEOUT = 45.0
REDIRECTS = 3
DESCRIBE_MAX_TOKENS = 700
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}
SOCIAL_IMAGE = {"og:image", "og:image:url", "og:image:secure_url", "twitter:image",
                "twitter:image:src"}
META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTRIBUTE = re.compile(r"""([A-Za-z:_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")

PROMPT = (
    "你是中文电商平台的商品录入助手。请阅读这张商品图片，只输出一个 JSON 对象，"
    "不要输出解释或代码块。字段与取值要求：\n"
    "name：商品中文名称，不超过 40 字，例如「轻薄立领羽绒服」；\n"
    f"category：只能是 {'/'.join(CATEGORIES)} 之一；\n"
    "color_name：主色的中文名称；color：主色的十六进制值，形如 #1F2A44；\n"
    f"style：只能是 {'/'.join(STYLES)} 之一；\n"
    f"season：只能是 {'/'.join(SEASONS)} 之一；\n"
    "occasion：中文场合词，不超过 8 字，例如「通勤办公」「日常休闲」；\n"
    f"silhouette：只能是 {'/'.join(SILHOUETTES)} 之一；\n"
    f"stretch：只能是 {'/'.join(STRETCHES)} 之一；\n"
    f"length_type：只能是 {'/'.join(LENGTH_TYPES)} 之一；\n"
    "description：一句话卖点，40-80 字，客观描述材质、版型与适用场景；\n"
    "tags：3-6 个中文关键词数组，例如 [\"立领\", \"防泼水\"]；\n"
    "confidence：0 到 1 的数字，表示你对判断的把握；\n"
    "uncertain：需要商家核对的地方，中文短句数组，没有就返回空数组。\n"
    "看不出来的信息请给出最接近的推测，不要编造品牌或价格。"
)


class ProductAiError(ValueError):
    """A safe Chinese message for the console; never a provider body or key."""


def _public_host(host: str) -> bool:
    """Refuse anything that is not a plain public address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def checked_url(url: str) -> str:
    """One http(s) URL on a public host, or a Chinese explanation."""
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise ProductAiError("链接无效")
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ProductAiError("只支持 http:// 或 https:// 链接")
    if parts.username or parts.password:
        raise ProductAiError("链接不能包含账号密码")
    if not _public_host(parts.hostname):
        raise ProductAiError("该链接指向内网或不可访问的地址")
    return url.strip()


def _get(client: httpx.Client, url: str) -> httpx.Response:
    """Follow at most a few redirects, checking every hop's host."""
    for _ in range(REDIRECTS + 1):
        current = checked_url(url)
        response = client.get(current, headers={"User-Agent": "ITP-product-import/1.0"})
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            if not location:
                raise ProductAiError("链接跳转失败")
            url = urljoin(current, location)
            continue
        response.raise_for_status()
        if len(response.content) > MAX_LINK_BYTES:
            raise ProductAiError("商品图片不能超过 8 MiB")
        return response
    raise ProductAiError("链接跳转次数过多")


def fetch_product_image(url: str, *, client: httpx.Client | None = None) -> tuple[bytes, str, str]:
    """Return ``(image bytes, image content type, source url)`` for a link."""
    own = client is None
    client = client or httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=False,
                                    trust_env=False)
    try:
        response = _get(client, url)
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type.startswith("image/"):
            data, image_type, source = response.content, content_type, str(response.url)
        elif content_type in {"text/html", "application/xhtml+xml", ""}:
            data, image_type = _image_from_html(client, response)
            source = str(response.url)
        else:
            raise ProductAiError("该链接既不是图片也不是商品页面")
    except ProductAiError:
        raise
    except httpx.HTTPError as exc:
        raise ProductAiError("无法打开该链接，请检查网络或换一个链接") from exc
    finally:
        if own:
            client.close()
    if not data:
        raise ProductAiError("链接里没有找到商品图片")
    return data, image_type, source


def _image_from_html(client: httpx.Client, response: httpx.Response) -> tuple[bytes, str]:
    """Pick the social-card image a shop page advertises, without running its JS."""
    if len(response.content) > MAX_HTML_BYTES:
        raise ProductAiError("商品页面过大，请改用图片直链")
    for tag in META_TAG.findall(response.text):
        attributes = {
            name.lower(): double or single for name, double, single in ATTRIBUTE.findall(tag)
        }
        key = attributes.get("property", attributes.get("name", "")).lower()
        candidate = attributes.get("content", "").strip()
        if key not in SOCIAL_IMAGE or not candidate:
            continue
        try:
            image = _get(client, urljoin(str(response.url), candidate))
        except (ProductAiError, httpx.HTTPError):
            continue
        content_type = (image.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type.startswith("image/"):
            return image.content, content_type
    raise ProductAiError("商品页面里没有可用的主图，请改用图片直链")


def preview_jpeg(data: bytes) -> tuple[bytes, int, int]:
    """A bounded, metadata-free copy of the picture for the preview and the model."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ProductAiError("商品图片像素过多")
            image = source.convert("RGB")
            image.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
            stream = io.BytesIO()
            image.save(stream, format="JPEG", quality=PREVIEW_QUALITY, optimize=True)
            return stream.getvalue(), image.width, image.height
    except (UnidentifiedImageError, OSError) as exc:
        raise ProductAiError("链接里的文件不是有效图片") from exc


def _enum(value, allowed: tuple[str, ...], default=None):
    if isinstance(value, str) and value.strip() in allowed:
        return value.strip()
    return default


def _text(value, limit: int, default=None):
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text[:limit]
    return default


def _string_list(value, *, limit: int, count: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = _text(item, limit)
        if text:
            items.append(text)
    return items[:count]


def _hex_color(value):
    if isinstance(value, str):
        text = value.strip().upper()
        if len(text) == 7 and text.startswith("#"):
            try:
                int(text[1:], 16)
                return text
            except ValueError:
                return None
    return None


def normalize(ai_fields: dict) -> dict:
    """Keep only what the product API would accept, so the form is always valid."""
    if not isinstance(ai_fields, dict):
        raise ProductAiError("AI 返回的内容无法识别")
    confidence = ai_fields.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    return {
        "name": _text(ai_fields.get("name"), 40, ""),
        "category": _enum(ai_fields.get("category"), CATEGORIES, ""),
        "color": _hex_color(ai_fields.get("color")),
        "color_name": _text(ai_fields.get("color_name"), 16),
        "style": _enum(ai_fields.get("style"), STYLES),
        "season": _enum(ai_fields.get("season"), SEASONS),
        "occasion": _text(ai_fields.get("occasion"), 8),
        "silhouette": _enum(ai_fields.get("silhouette"), SILHOUETTES),
        "stretch": _enum(ai_fields.get("stretch"), STRETCHES),
        "length_type": _enum(ai_fields.get("length_type"), LENGTH_TYPES),
        "description": _text(ai_fields.get("description"), SHORT_TEXT_MAX),
        "tags": _string_list(ai_fields.get("tags"), limit=12, count=6),
        "uncertain": _string_list(ai_fields.get("uncertain"), limit=60, count=4),
        "confidence": round(confidence, 2) if confidence is not None else None,
    }


def _json_object(answer) -> dict:
    """Models like to wrap JSON in prose or a code fence; take the object itself."""
    if not isinstance(answer, str):
        raise ProductAiError("AI 返回的内容无法识别")
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        raise ProductAiError("AI 返回的内容无法识别")
    try:
        document = json.loads(answer[start:end + 1])
    except ValueError as exc:
        raise ProductAiError("AI 返回的内容无法识别") from exc
    if not isinstance(document, dict):
        raise ProductAiError("AI 返回的内容无法识别")
    return document


# The status code alone is enough to point at the usual causes; provider bodies
# may carry account details, so they are never surfaced or logged.
REFUSAL_REASONS = {
    400: "接口拒绝了这次请求，请检查接口地址与模型名是否正确",
    401: "API Key 未被接受，请检查密钥是否填写正确",
    402: "服务商提示账户余额不足，请先充值",
    403: "服务商拒绝了请求，通常是账号欠费或该模型未授权，请检查服务商控制台",
    404: "接口地址或模型名不正确，或该模型尚未在服务商控制台开通",
    429: "调用过于频繁或额度已用完，请稍后再试",
}


def _refusal_reason(status: int) -> str:
    return REFUSAL_REASONS.get(
        status, "图片识别服务暂时不可用，请稍后重试" if status < 500
        else "服务商暂时不可用（服务端错误），请稍后重试"
    )


def _probe_image() -> bytes:
    """A tiny opaque square: enough for the model to answer, cheap to send."""
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 255, 255)).save(stream, format="JPEG", quality=70)
    return stream.getvalue()


class ProductDescriber:
    """The configured vision model, called with the operator's server-side key."""

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def ready(self) -> bool:
        return self.settings.product_ai_ready

    def describe(self, image: bytes) -> dict:
        """Ask the model about one already-downscaled JPEG."""
        return normalize(_json_object(self._complete(image, PROMPT, DESCRIBE_MAX_TOKENS)))

    def probe(self) -> dict:
        """One tiny call that proves endpoint, model and key work together."""
        if not self.ready():
            return {"ok": False, "message": "尚未配置完整的接口地址、模型与密钥"}
        try:
            answer = self._complete(_probe_image(), "只回复两个字：正常", 16)
        except ProductAiError as exc:
            return {"ok": False, "message": str(exc)}
        reply = answer.strip()[:20]
        return {"ok": True, "message": f"{self.settings.product_ai_model} 已返回：{reply}"}

    def _complete(self, image: bytes, prompt: str, max_tokens: int) -> str:
        """Send one image question and return the model's text answer."""
        if not self.ready():
            raise ProductAiError("平台尚未配置图片识别模型，请联系运维")
        payload = {
            "model": self.settings.product_ai_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {
                            "url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            # Gateways such as HaiJing stream by default; ask for the whole
            # answer so one code path reads every provider's reply.
            "stream": False,
        }
        try:
            with httpx.Client(timeout=AI_TIMEOUT, trust_env=False,
                              transport=self.transport) as client:
                response = client.post(
                    self.settings.product_ai_endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.settings.product_ai_key}"},
                )
            if response.status_code != 200:
                logger.warning("Product AI refused the request: status=%s", response.status_code)
                raise ProductAiError(_refusal_reason(response.status_code))
            answer = response.json()["choices"][0]["message"]["content"]
        except ProductAiError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Product AI call failed: %s", type(exc).__name__)
            raise ProductAiError("图片识别服务暂时不可用，请稍后重试") from exc
        if not isinstance(answer, str):
            raise ProductAiError("AI 返回的内容无法识别")
        return answer
