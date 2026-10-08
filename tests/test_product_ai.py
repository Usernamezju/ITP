"""Product link import: guarded fetching, structured description, safe failures."""

import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp import product_ai
from itp.api import create_app
from itp.config import Settings
from itp.product_ai import (
    ProductAiError,
    ProductDescriber,
    checked_url,
    fetch_product_image,
    normalize,
    preview_jpeg,
)

PASSWORD = "merchant-password-123"
ANSWER = json.dumps({
    "name": "轻薄立领羽绒服",
    "category": "外套",
    "color": "#1f2a44",
    "color_name": "藏蓝",
    "style": "通勤",
    "season": "冬",
    "occasion": "通勤办公",
    "silhouette": "标准",
    "stretch": "微弹",
    "length_type": "常规",
    "description": "轻薄立领羽绒服，防风面料配抽绳下摆，适合通勤与日常出行。",
    "tags": ["立领", "防泼水", "抽绳"],
    "confidence": 0.86,
    "uncertain": ["请核对填充物成分"],
}, ensure_ascii=False)


def photo(size=(800, 1000), color="navy", fmt="PNG") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=fmt)
    return stream.getvalue()


def ai_transport(answer=ANSWER, status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "boom"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def public_hosts(monkeypatch):
    """Keep the guard's real DNS out of the offline tests."""
    monkeypatch.setattr(product_ai, "_public_host", lambda host: True)


# --- link guards -------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "ftp://example.com/a.png",
    "https://user:secret@shop.example.com/a.png",
    "",
    "x" * 3000,
])
def test_only_plain_public_http_links_are_accepted(url):
    with pytest.raises(ProductAiError):
        checked_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/secret",
    "http://localhost/admin",
    "http://10.0.0.5/a.png",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/a.png",
])
def test_internal_addresses_are_refused_with_the_real_resolver(url, monkeypatch):
    monkeypatch.undo()  # exercise the real check for this test
    with pytest.raises(ProductAiError, match="内网"):
        checked_url(url)


def test_a_shop_page_is_read_through_its_social_card_image():
    page = (b'<html><head><meta content="https://cdn.example.com/main.jpg" '
            b'property="og:image"></head></html>')
    image = photo()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/product":
            return httpx.Response(200, content=page, headers={"content-type": "text/html"})
        return httpx.Response(200, content=image, headers={"content-type": "image/jpeg"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    data, content_type, source = fetch_product_image("https://shop.example.com/product",
                                                     client=client)
    assert data == image and content_type == "image/jpeg"
    assert source.endswith("/product")


def test_an_image_link_skips_the_page_parsing():
    image = photo(fmt="JPEG")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=image, headers={"content-type": "image/jpeg"}))
    data, content_type, _ = fetch_product_image(
        "https://cdn.example.com/a.jpg", client=httpx.Client(transport=transport))
    assert data == image and content_type == "image/jpeg"


@pytest.mark.parametrize("handler,message", [
    (lambda request: httpx.Response(200, content=b"<html>no image</html>",
                                    headers={"content-type": "text/html"}), "没有可用的主图"),
    (lambda request: httpx.Response(200, content=b"{}",
                                    headers={"content-type": "application/json"}),
     "既不是图片也不是商品"),
    (lambda request: httpx.Response(404), "无法打开该链接"),
])
def test_unusable_links_explain_themselves(handler, message):
    with pytest.raises(ProductAiError, match=message):
        fetch_product_image("https://shop.example.com/x",
                            client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_oversized_and_looping_links_are_stopped():
    big = httpx.MockTransport(lambda request: httpx.Response(
        200, content=b"x" * (product_ai.MAX_LINK_BYTES + 1), headers={"content-type": "image/png"}))
    with pytest.raises(ProductAiError, match="8 MiB"):
        fetch_product_image("https://cdn.example.com/big.png", client=httpx.Client(transport=big))

    loop = httpx.MockTransport(lambda request: httpx.Response(
        302, headers={"location": "https://shop.example.com/again"}))
    with pytest.raises(ProductAiError, match="跳转"):
        fetch_product_image("https://shop.example.com/again", client=httpx.Client(transport=loop))


# --- preview and normalization ----------------------------------------------

def test_preview_is_bounded_square_free_and_metadata_free():
    source = io.BytesIO()
    image = Image.new("RGB", (2400, 1200), "orange")
    exif = image.getexif()
    exif[34853] = {1: "N"}
    image.save(source, format="JPEG", exif=exif)
    preview, width, height = preview_jpeg(source.getvalue())
    assert width == product_ai.PREVIEW_SIZE and height == 512
    with Image.open(io.BytesIO(preview)) as result:
        assert result.format == "JPEG" and not result.getexif()
    with pytest.raises(ProductAiError):
        preview_jpeg(b"not an image")


def test_only_platform_vocabulary_survives_normalization():
    fields = normalize({
        "name": "  " + "很长的名字" * 20,
        "category": "裤子",          # not a platform category
        "style": "通勤",
        "season": "第五季",
        "color": "blue",             # not a hex value
        "color_name": "藏蓝",
        "occasion": "通勤办公与出差见客户",
        "silhouette": "修身",
        "stretch": "弹力",           # not a stretch value
        "length_type": "常规",
        "description": "x" * 500,
        "tags": ["立领", "", 3, "防泼水", "抽绳", "透气", "轻量", "第七个", "第八个"],
        "confidence": "high",        # not a number
        "uncertain": ["请核对成分", "", None],
    })
    assert fields["category"] == "" and fields["style"] == "通勤" and fields["season"] is None
    assert fields["color"] is None and fields["color_name"] == "藏蓝"
    assert len(fields["occasion"]) == 8
    assert fields["silhouette"] == "修身" and fields["stretch"] is None
    assert len(fields["description"]) == product_ai.SHORT_TEXT_MAX
    # Six keywords at most, blanks and non-strings dropped.
    assert fields["tags"] == ["立领", "防泼水", "抽绳", "透气", "轻量", "第七个"]
    assert fields["uncertain"] == ["请核对成分"]
    assert fields["confidence"] is None


def test_the_model_may_wrap_its_json_in_prose_or_a_fence():
    for answer in (f"好的，这是结果：\n{ANSWER}\n希望有帮助",
                   f"```json\n{ANSWER}\n```"):
        fields = ProductDescriber(
            Settings(_env_file=None, pose_api_key="k" * 8), transport=ai_transport(answer)
        ).describe(photo())
        assert fields["name"] == "轻薄立领羽绒服"
    with pytest.raises(ProductAiError, match="无法识别"):
        ProductDescriber(Settings(_env_file=None, pose_api_key="k" * 8),
                         transport=ai_transport("抱歉，我看不出这张图")).describe(photo())


def test_the_image_travels_to_the_model_and_the_key_stays_server_side():
    seen: list[httpx.Request] = []
    settings = Settings(_env_file=None, pose_api_key="server-only-key")
    ProductDescriber(settings, transport=ai_transport(seen=seen)).describe(photo())
    request = seen[0]
    assert request.headers["authorization"] == "Bearer server-only-key"
    assert str(request.url) == settings.product_ai_endpoint
    body = json.loads(request.content)
    image_url = body["messages"][0]["content"][0]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    assert body["model"] == settings.product_ai_model


@pytest.mark.parametrize("status,message", [
    (401, "API Key 未被接受"),
    (403, "账号欠费或该模型未授权"),
    (404, "尚未在服务商控制台开通"),
    (429, "额度已用完"),
    (500, "服务商暂时不可用"),
])
def test_a_refusal_points_at_the_likely_cause(status, message):
    settings = Settings(_env_file=None, pose_api_key="k" * 8)
    with pytest.raises(ProductAiError, match=message):
        ProductDescriber(settings, transport=ai_transport(status=status)).describe(photo())


def test_a_failing_or_missing_model_is_reported_in_chinese():
    settings = Settings(_env_file=None, pose_api_key="k" * 8)
    with pytest.raises(ProductAiError, match="稍后重试"):
        ProductDescriber(settings, transport=ai_transport(status=408)).describe(photo())
    without_key = Settings(_env_file=None)
    with pytest.raises(ProductAiError, match="尚未配置"):
        ProductDescriber(without_key, transport=ai_transport()).describe(photo())


# --- the merchant endpoint ---------------------------------------------------

@pytest.fixture
def shop(tmp_path, monkeypatch):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret="a" * 64,
                 environment="test", pose_api_key="server-only-key"),
        start_worker=False, config_path=tmp_path / ".env",
        product_ai_transport=ai_transport())
    monkeypatch.setattr("itp.api.fetch_product_image",
                        lambda url: (photo(), "image/jpeg", url))
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.post("/api/auth/register", json={"name": "shop-ai", "display_name": "AI 店铺",
                                                "password": PASSWORD, "role": "merchant"})
        login = client.post("/api/auth/login", json={"name": "shop-ai", "password": PASSWORD})
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        user = app.state.merchants.merchant_by_name("shop-ai")
        plan = next(p for p in app.state.commerce.prices()["plans"] if p["id"] == "merchant_basic")
        with app.state.merchants.connect() as conn:
            app.state.commerce.grant_subscription(conn, user["id"], plan, "test-paid-shop")
        client.headers["Idempotency-Key"] = "product-ai-test-key"
        yield client, monkeypatch


def test_a_merchant_gets_structured_fields_and_a_preview(shop):
    client, _ = shop
    response = client.post("/api/merchant/import-link",
                           json={"url": "https://item.example.com/123"})
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["fields"]["category"] == "外套"
    assert document["fields"]["color"] == "#1F2A44"
    assert document["fields"]["tags"][:2] == ["立领", "防泼水"]
    assert document["image"]["data_url"].startswith("data:image/jpeg;base64,")
    assert document["image"]["source_url"] == "https://item.example.com/123"
    assert document["model"] == "qwen-vl-max"
    assert "server-only-key" not in response.text


def test_only_a_merchant_may_import_and_only_with_a_link(shop):
    client, _ = shop
    anonymous = TestClient(client.app, base_url="http://localhost:8000")
    assert anonymous.post("/api/merchant/import-link",
                          json={"url": "https://a.example.com/x"}).status_code == 401
    assert client.post("/api/merchant/import-link", json={}).status_code == 422
    assert client.post("/api/merchant/import-link",
                       json={"url": "x", "extra": 1}).status_code == 422


def test_a_refused_link_keeps_its_chinese_reason(shop):
    client, monkeypatch = shop
    def refuse(url):
        raise ProductAiError("该链接指向内网或不可访问的地址")
    monkeypatch.setattr("itp.api.fetch_product_image", refuse)
    response = client.post("/api/merchant/import-link", json={"url": "http://127.0.0.1/x"})
    assert response.status_code == 422
    assert "内网" in response.json()["detail"]
