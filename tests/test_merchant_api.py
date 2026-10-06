"""HTTP tests for merchant accounts, garment import, looks and body profiles."""

import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp.api import MERCHANT_BODY_LIMIT, create_app
from itp.config import Settings
from itp.garments import MerchantStore
from itp.merchant_auth import (
    decode_token,
    encode_token,
    hash_password,
    resolve_jwt_secret,
    verify_password,
)

PASSWORD = "secret-password"


@pytest.fixture
def env(tmp_path):
    """A live app with its own data directory and env file."""
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        segmentation_model=tmp_path / "missing.onnx",
        poll_seconds=0.05,
    )
    config_path = tmp_path / ".env"
    app = create_app(settings, start_worker=False, config_path=config_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client, settings, config_path


def png_bytes(size=(256, 256), color="orange"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def register(client, name="shop-one", password=PASSWORD, **overrides):
    body = {
        "name": name,
        "display_name": "示例商家",
        "contact": "owner@example.com",
        "password": password,
    }
    body.update(overrides)
    return client.post("/api/merchant/register", json=body)


def login(client, name="shop-one", password=PASSWORD):
    return client.post("/api/merchant/login", json={"name": name, "password": password})


def token_for(client, name="shop-one", password=PASSWORD):
    """Log in, creating this account first when the test did not register one."""
    response = login(client, name, password)
    if response.status_code == 401:
        assert register(client, name=name, password=password).status_code == 201
        response = login(client, name, password)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- changing a password -----------------------------------------------------

NEW_PASSWORD = "second-password"


def change_password(client, token, current, new):
    return client.post("/api/merchant/password", headers=auth(token),
                       json={"current_password": current, "new_password": new})


def test_changing_the_password_swaps_which_one_works(env):
    client, _settings, _config_path = env
    register(client)
    token = token_for(client)

    response = change_password(client, token, PASSWORD, NEW_PASSWORD)

    assert response.status_code == 200
    assert response.json() == {"changed": True, "tokens_revoked": True}
    assert login(client, password=NEW_PASSWORD).status_code == 200
    assert login(client, password=PASSWORD).status_code == 401


def test_the_old_token_stops_working_after_the_change(env):
    client, _settings, _config_path = env
    register(client)
    token = token_for(client)
    assert client.get("/api/merchant/me", headers=auth(token)).status_code == 200

    assert change_password(client, token, PASSWORD, NEW_PASSWORD).status_code == 200

    # The very token that made the change is refused afterwards.
    refused = client.get("/api/merchant/me", headers=auth(token))
    assert refused.status_code == 401
    assert "密码已修改" in refused.json()["detail"]
    fresh = login(client, password=NEW_PASSWORD).json()["access_token"]
    assert client.get("/api/merchant/me", headers=auth(fresh)).status_code == 200


def test_a_password_change_needs_the_current_password(env):
    client, _settings, _config_path = env
    register(client)
    token = token_for(client)

    response = change_password(client, token, "not-the-password", NEW_PASSWORD)

    assert response.status_code == 401
    assert response.json()["detail"] == "当前密码不正确"
    assert login(client).status_code == 200  # nothing changed


def test_a_password_change_needs_a_token(env):
    client, _settings, _config_path = env
    register(client)

    response = client.post("/api/merchant/password",
                           json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})

    assert response.status_code == 401


@pytest.mark.parametrize("weak", ["short", "a" * 129])
def test_the_new_password_obeys_the_registration_rule(env, weak):
    client, _settings, _config_path = env
    register(client)
    token = token_for(client)

    response = change_password(client, token, PASSWORD, weak)

    assert response.status_code == 422
    assert "8-128" in response.json()["detail"]


def test_the_new_password_must_differ_from_the_current_one(env):
    client, _settings, _config_path = env
    register(client)
    token = token_for(client)

    response = change_password(client, token, PASSWORD, PASSWORD)

    assert response.status_code == 422
    assert response.json()["detail"] == "新密码不能与当前密码相同"


def test_the_password_body_rejects_unknown_fields(env):
    client, _settings, _config_path = env
    register(client)
    token = token_for(client)

    response = client.post("/api/merchant/password", headers=auth(token),
                           json={"current_password": PASSWORD, "new_password": NEW_PASSWORD,
                                 "merchant_id": "someone-else"})

    assert response.status_code == 422


def test_a_password_change_only_touches_that_merchant(env):
    client, _settings, _config_path = env
    register(client, name="shop-one")
    register(client, name="shop-two")
    first = token_for(client, name="shop-one")
    second = token_for(client, name="shop-two")

    assert change_password(client, first, PASSWORD, NEW_PASSWORD).status_code == 200

    # The other shop's session and password are untouched.
    assert client.get("/api/merchant/me", headers=auth(second)).status_code == 200
    assert login(client, name="shop-two").status_code == 200


def garment_payload(**overrides):
    document = {"category": "上装", "name": "落肩针织开衫", "status": "draft"}
    document.update(overrides)
    return document


def create_garment(client, token, images=(), **overrides):
    files = [("images", (f"image-{index}.png", data, "image/png"))
             for index, data in enumerate(images)]
    return client.post(
        "/api/merchant/garments",
        headers=auth(token),
        data={"payload": json.dumps(garment_payload(**overrides), ensure_ascii=False)},
        files=files or None,
    )


def asset_files(settings):
    root = settings.data_dir / "assets"
    return sorted(path.name for path in root.glob("*")) if root.is_dir() else []


# ------------------------------------------------------------------- accounts


def test_register_login_and_me(env):
    client, settings, config_path = env
    created = register(client)
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"merchant_id", "name", "display_name"}
    assert body["name"] == "shop-one"
    # Registration itself must not need (or create) the token secret.
    assert not config_path.exists()

    logged_in = login(client)
    assert logged_in.status_code == 200
    tokens = logged_in.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == settings.merchant_token_hours * 3600
    claims = decode_token(resolve_jwt_secret(client.app), tokens["access_token"])
    assert claims["sub"] == body["merchant_id"]
    assert claims["exp"] - claims["iat"] == settings.merchant_token_hours * 3600

    me = client.get("/api/merchant/me", headers=auth(tokens["access_token"]))
    assert me.status_code == 200
    assert set(me.json()) == {
        "merchant_id", "name", "display_name", "contact", "created", "quota", "garment_count",
    }
    assert me.json()["garment_count"] == 0
    assert me.json()["quota"] == settings.merchant_quota
    assert me.json()["contact"] == "owner@example.com"


def test_register_validates_every_field(env):
    client, _, _ = env
    assert register(client, name="ab").status_code == 422
    assert register(client, name="with space").status_code == 422
    assert register(client, name="a" * 33).status_code == 422
    assert register(client, name="with.dot").status_code == 422
    assert register(client, display_name="").status_code == 422
    assert register(client, display_name="名" * 41).status_code == 422
    assert register(client, contact="c" * 81).status_code == 422
    assert register(client, password="short12").status_code == 422
    assert register(client, password="p" * 129).status_code == 422
    assert register(client, extra_field="x").status_code == 422
    invalid = register(client, name="ab")
    assert "商家账号" in invalid.json()["detail"]
    assert register(client, name="ok-name").status_code == 201
    duplicate = register(client, name="ok-name")
    assert duplicate.status_code == 409
    assert "已被占用" in duplicate.json()["detail"]


def test_login_failures_are_indistinguishable(env):
    client, _, _ = env
    register(client)
    wrong = login(client, password="wrong-password")
    missing = login(client, name="nobody")
    assert wrong.status_code == 401 and missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]
    assert "账号或密码不正确" in wrong.json()["detail"]
    # A short or empty password is simply a wrong password on login: the
    # endpoint must not reveal the registration policy.
    assert login(client, password="shorter").status_code == 401
    assert login(client, password="").status_code == 401
    assert client.post("/api/merchant/login", json={"name": "shop-one"}).status_code == 422
    assert client.post("/api/merchant/login", json={"name": "shop-one", "x": 1}).status_code == 422


def test_tokens_must_be_valid_and_unexpired(env):
    client, _, _ = env
    merchant_id = register(client).json()["merchant_id"]
    token = token_for(client)
    assert client.get("/api/merchant/me").status_code == 401
    assert client.get("/api/merchant/me", headers={"Authorization": "Basic x"}).status_code == 401
    assert client.get("/api/merchant/me", headers=auth("not-a-token")).status_code == 401
    assert client.get("/api/merchant/me", headers=auth(token + "x")).status_code == 401
    assert client.get("/api/merchant/me", headers=auth("a" * 64)).status_code == 401

    secret = resolve_jwt_secret(client.app)
    other_secret, _ = encode_token("b" * 64, merchant_id, hours=1)
    assert client.get("/api/merchant/me", headers=auth(other_secret)).status_code == 401

    expired, _ = encode_token(secret, merchant_id, hours=1, now=time.time() - 7200)
    assert client.get("/api/merchant/me", headers=auth(expired)).status_code == 401

    unknown, _ = encode_token(secret, "0" * 32, hours=1)
    assert client.get("/api/merchant/me", headers=auth(unknown)).status_code == 401


def test_disabled_merchant_is_refused(env):
    client, settings, _ = env
    merchant_id = register(client).json()["merchant_id"]
    token = token_for(client)
    assert client.get("/api/merchant/me", headers=auth(token)).status_code == 200

    store = MerchantStore(settings.data_dir)
    store.set_disabled(merchant_id, True)
    refused = client.get("/api/merchant/me", headers=auth(token))
    assert refused.status_code == 403
    assert "已被禁用" in refused.json()["detail"]
    assert login(client).status_code == 403
    assert create_garment(client, token).status_code == 403


def test_jwt_secret_is_persisted_once_and_never_echoed(env):
    client, _, config_path = env
    config_path.write_text("# keep this comment\nITP_DATA_DIR=./data\n", encoding="utf-8")
    merchant_id = register(client).json()["merchant_id"]
    token = token_for(client)
    assert config_path.exists()
    text = config_path.read_text(encoding="utf-8")
    assert text.startswith("# keep this comment")
    secret_line = next(line for line in text.splitlines() if line.startswith("ITP_JWT_SECRET="))
    secret = json.loads(secret_line.split("=", 1)[1])
    assert len(secret) == 64
    assert all(char in "0123456789abcdef" for char in secret)
    assert secret not in client.get("/api/merchant/me", headers=auth(token)).text
    assert secret not in client.get("/api/settings").text
    # A second login reuses the stored secret: the first token keeps working and
    # the file still holds exactly one secret line.
    second = token_for(client)
    assert decode_token(secret, second)["sub"] == merchant_id
    assert client.get("/api/merchant/me", headers=auth(token)).status_code == 200
    assert config_path.read_text(encoding="utf-8").count("ITP_JWT_SECRET=") == 1


def test_jwt_secret_from_the_environment_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("ITP_JWT_SECRET", "c" * 64)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    config_path = tmp_path / ".env"
    app = create_app(settings, start_worker=False, config_path=config_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        register(client, name="env-shop")
        token = token_for(client, name="env-shop")
        assert client.get("/api/merchant/me", headers=auth(token)).status_code == 200
    assert not config_path.exists()
    assert decode_token("c" * 64, token)["sub"]


def test_jwt_secret_falls_back_when_the_env_file_cannot_be_written(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    config_path = tmp_path / "config-dir"
    config_path.mkdir()  # a directory can never be replaced by the settings write
    app = create_app(settings, start_worker=False, config_path=config_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        register(client, name="fallback-shop")
        token = token_for(client, name="fallback-shop")
        assert client.get("/api/merchant/me", headers=auth(token)).status_code == 200
    assert config_path.is_dir()
    assert len(app.state.jwt_secret) == 64


# ------------------------------------------------------------------- garments


def test_garment_upload_list_and_detail(env):
    client, settings, _ = env
    token = token_for(client)
    created = create_garment(
        client, token, images=[png_bytes(), png_bytes(color="navy")],
        sku="ITP-001", measurements={"bust_cm": 92}, attributes={"color": "#4a5b52"},
        style="通勤", season="四季", occasion="通勤办公",
    )
    assert created.status_code == 201, created.text
    garment = created.json()
    assert garment["status"] == "draft"
    assert garment["metrics"]["sku"] == "ITP-001"
    assert garment["metrics"]["measurements"]["bust_cm"] == 92
    assert garment["metrics"]["measurements"]["waist_cm"] is None
    assert [image["position"] for image in garment["images"]] == [0, 1]

    listed = client.get("/api/merchant/garments", headers=auth(token)).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == garment["id"]

    detail = client.get(f"/api/merchant/garments/{garment['id']}", headers=auth(token))
    assert detail.status_code == 200 and len(detail.json()["images"]) == 2

    filtered = client.get(
        "/api/merchant/garments?status=published", headers=auth(token)
    ).json()
    assert filtered == {"total": 0, "items": []}
    assert client.get(
        "/api/merchant/garments?status=live", headers=auth(token)
    ).status_code == 422
    assert client.get(
        "/api/merchant/garments?limit=0", headers=auth(token)
    ).status_code == 422
    assert asset_files(settings)


def test_garment_without_images_and_image_endpoints(env):
    client, settings, _ = env
    token = token_for(client)
    created = create_garment(client, token)
    assert created.status_code == 201
    garment = created.json()
    assert garment["images"] == []

    added = client.post(
        f"/api/merchant/garments/{garment['id']}/images",
        headers=auth(token),
        files=[("images", ("a.png", png_bytes(), "image/png"))],
    )
    assert added.status_code == 201, added.text
    images = added.json()
    assert len(images) == 1 and images[0]["position"] == 0
    image_id = images[0]["id"]

    fetched = client.get(images[0]["url"])
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["cache-control"] == "public, max-age=604800"
    assert Image.open(io.BytesIO(fetched.content)).size == (256, 256)
    fetched.close()

    empty = client.post(
        f"/api/merchant/garments/{garment['id']}/images", headers=auth(token), files=None
    )
    assert empty.status_code == 422
    assert client.post(
        f"/api/merchant/garments/{garment['id']}/images",
        headers=auth(token),
        files=[("images", ("a.png", b"not an image", "image/png"))],
    ).status_code == 422
    assert client.post(
        f"/api/merchant/garments/{garment['id']}/images",
        headers=auth(token),
        files=[("images", ("a.png", png_bytes((10, 10)), "image/png"))],
    ).status_code == 422

    removed = client.delete(
        f"/api/merchant/garments/{garment['id']}/images/{image_id}", headers=auth(token)
    )
    assert removed.status_code == 204
    assert client.get(f"/api/garment-images/{image_id}").status_code == 404
    assert client.delete(
        f"/api/merchant/garments/{garment['id']}/images/{image_id}", headers=auth(token)
    ).status_code == 404
    assert asset_files(settings) == []


def test_garment_patch_merges_and_deletes_clean_up(env):
    client, settings, _ = env
    token = token_for(client)
    garment = create_garment(
        client, token, images=[png_bytes()],
        measurements={"bust_cm": 90, "waist_cm": 70}, style="通勤",
    ).json()
    patched = client.patch(
        f"/api/merchant/garments/{garment['id']}",
        headers=auth(token),
        json={"status": "published", "measurements": {"bust_cm": 95}},
    )
    assert patched.status_code == 200
    metrics = patched.json()["metrics"]
    assert metrics["status"] == "published"
    assert metrics["measurements"]["bust_cm"] == 95
    assert metrics["measurements"]["waist_cm"] == 70
    assert metrics["style"] == "通勤"

    cleared = client.patch(
        f"/api/merchant/garments/{garment['id']}",
        headers=auth(token),
        json={"measurements": None},
    )
    assert all(value is None for value in cleared.json()["metrics"]["measurements"].values())
    assert client.patch(
        f"/api/merchant/garments/{garment['id']}", headers=auth(token), json={"category": "帽子"}
    ).status_code == 422
    assert client.patch(
        f"/api/merchant/garments/{garment['id']}", headers=auth(token), json={"nope": 1}
    ).status_code == 422

    assert client.delete(
        f"/api/merchant/garments/{garment['id']}", headers=auth(token)
    ).status_code == 204
    assert client.get(
        f"/api/merchant/garments/{garment['id']}", headers=auth(token)
    ).status_code == 404
    assert asset_files(settings) == []


@pytest.mark.parametrize("document", [
    {"category": "帽子", "name": "分类错", "status": "draft"},
    {"name": "缺分类", "status": "draft"},
    {"category": "上装", "status": "draft"},
    {"category": "上装", "name": "缺状态"},
    {"category": "上装", "name": "状态错", "status": "live"},
    {"category": "上装", "name": "未知字段", "status": "draft", "colour": "red"},
    {"category": "上装", "name": "尺寸越界", "status": "draft", "measurements": {"bust_cm": 301}},
    {"category": "上装", "name": "区间倒置", "status": "draft",
     "fit_ranges": {"height_cm": [190, 150]}},
    {"category": "上装", "name": "颜色错", "status": "draft",
     "attributes": {"color": "#AABBCC"}},
    {"category": "上装", "name": "枚举错", "status": "draft", "attributes": {"stretch": "低弹"}},
    {"category": "上装", "name": "克重错", "status": "draft", "attributes": {"weight_gsm": 5}},
    {"category": "上装", "name": "介绍过长", "status": "draft", "description": "长" * 1001},
    {"category": "上装", "name": "价格负", "status": "draft", "price_cents": -1},
])
def test_invalid_metrics_are_rejected_without_storing_anything(env, document):
    client, settings, _ = env
    token = token_for(client)
    before_assets = asset_files(settings)
    response = client.post(
        "/api/merchant/garments",
        headers=auth(token),
        data={"payload": json.dumps(document, ensure_ascii=False)},
        files=[("images", ("a.png", png_bytes(), "image/png"))],
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    assert client.get("/api/merchant/garments", headers=auth(token)).json()["total"] == 0
    assert asset_files(settings) == before_assets


def test_payload_must_be_json_object(env):
    client, settings, _ = env
    token = token_for(client)
    for raw in ("not json", "[1, 2]"):
        response = client.post(
            "/api/merchant/garments", headers=auth(token), data={"payload": raw}
        )
        assert response.status_code == 422
        assert "payload" in response.json()["detail"]
    assert client.post(
        "/api/merchant/garments", headers=auth(token), data={}
    ).status_code == 422
    assert client.get("/api/merchant/garments", headers=auth(token)).json()["total"] == 0
    assert asset_files(settings) == []


def test_quota_and_sku_conflicts(env):
    client, settings, _ = env
    merchant_id = register(client).json()["merchant_id"]
    token = token_for(client)
    MerchantStore(settings.data_dir).set_quota(merchant_id, 2)
    assert create_garment(client, token, name="一").status_code == 201
    assert create_garment(client, token, name="二").status_code == 201
    blocked = create_garment(client, token, name="三")
    assert blocked.status_code == 409
    assert "配额上限" in blocked.json()["detail"]

    MerchantStore(settings.data_dir).set_quota(merchant_id, 5)
    assert create_garment(client, token, name="四", sku="SKU-1").status_code == 201
    clash = create_garment(client, token, name="五", sku="SKU-1")
    assert clash.status_code == 409
    assert "货号" in clash.json()["detail"]


def test_cross_merchant_access_is_a_404(env):
    client, _, _ = env
    register(client, name="shop-one")
    first_token = token_for(client, name="shop-one")
    register(client, name="shop-two")
    second_token = token_for(client, name="shop-two")
    garment = create_garment(client, first_token, images=[png_bytes()]).json()
    image_id = garment["images"][0]["id"]

    headers = auth(second_token)
    assert client.get(f"/api/merchant/garments/{garment['id']}", headers=headers).status_code == 404
    assert client.patch(
        f"/api/merchant/garments/{garment['id']}", headers=headers, json={"name": "偷改"}
    ).status_code == 404
    assert client.delete(
        f"/api/merchant/garments/{garment['id']}", headers=headers
    ).status_code == 404
    assert client.post(
        f"/api/merchant/garments/{garment['id']}/images",
        headers=headers,
        files=[("images", ("a.png", png_bytes(), "image/png"))],
    ).status_code == 404
    assert client.delete(
        f"/api/merchant/garments/{garment['id']}/images/{image_id}", headers=headers
    ).status_code == 404
    assert client.get("/api/merchant/garments", headers=headers).json()["total"] == 0
    # The owner still sees everything.
    assert client.get(
        f"/api/merchant/garments/{garment['id']}", headers=auth(first_token)
    ).status_code == 200


# ---------------------------------------------------------------------- looks


def test_merchant_looks_lifecycle(env):
    client, _, _ = env
    token = token_for(client)
    first = create_garment(client, token, name="上装", category="上装").json()
    second = create_garment(client, token, name="下装", category="下装").json()

    created = client.post("/api/merchant/looks", headers=auth(token), json={
        "name": "通勤两件套", "story": "把锋利收进软结构里。", "style": "通勤", "season": "四季",
        "occasion": "通勤办公", "palette": ["#c9d6bd", "#4a5b52"],
        "items": [first["id"], second["id"]], "status": "draft",
    })
    assert created.status_code == 201, created.text
    look = created.json()
    assert [item["id"] for item in look["items"]] == [first["id"], second["id"]]
    assert look["palette"] == ["#c9d6bd", "#4a5b52"]

    listed = client.get("/api/merchant/looks", headers=auth(token)).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == look["id"]

    patched = client.patch(f"/api/merchant/looks/{look['id']}", headers=auth(token), json={
        "status": "published", "items": [second["id"]],
    })
    assert patched.status_code == 200
    assert [item["id"] for item in patched.json()["items"]] == [second["id"]]
    assert patched.json()["name"] == "通勤两件套"

    assert client.post("/api/merchant/looks", headers=auth(token), json={
        "name": "越权", "status": "draft", "items": ["0" * 32],
    }).status_code == 422
    assert client.post("/api/merchant/looks", headers=auth(token), json={
        "name": "非法", "status": "draft", "palette": ["red"],
    }).status_code == 422
    removed_look = client.delete(f"/api/merchant/looks/{look['id']}", headers=auth(token))
    assert removed_look.status_code == 204
    assert client.patch(
        f"/api/merchant/looks/{look['id']}", headers=auth(token), json={"name": "x"}
    ).status_code == 404


def test_looks_are_scoped_to_their_merchant(env):
    client, _, _ = env
    first_token = token_for(client)
    garment = create_garment(client, first_token).json()
    look = client.post("/api/merchant/looks", headers=auth(first_token), json={
        "name": "我的穿搭", "status": "published", "items": [garment["id"]],
    }).json()
    register(client, name="shop-two")
    other = auth(token_for(client, name="shop-two"))
    assert client.get("/api/merchant/looks", headers=other).json()["total"] == 0
    assert client.patch(
        f"/api/merchant/looks/{look['id']}", headers=other, json={"name": "偷改"}
    ).status_code == 404
    assert client.delete(f"/api/merchant/looks/{look['id']}", headers=other).status_code == 404


def test_public_catalogue_exposes_only_published(env):
    client, _, _ = env
    token = token_for(client)
    published = create_garment(
        client, token, name="已发布", status="published", style="通勤", season="四季",
        occasion="通勤办公", images=[png_bytes()],
    ).json()
    draft = create_garment(client, token, name="草稿", status="draft", style="通勤").json()
    listable = create_garment(client, token, name="另一件", status="published", style="街头").json()
    public_look = client.post("/api/merchant/looks", headers=auth(token), json={
        "name": "公开穿搭", "status": "published", "style": "通勤",
        "items": [published["id"], draft["id"]],
    }).json()
    client.post("/api/merchant/looks", headers=auth(token), json={
        "name": "草稿穿搭", "status": "draft", "items": [published["id"]],
    })

    listing = client.get("/api/garments").json()
    assert listing["total"] == 2
    assert {item["id"] for item in listing["items"]} == {published["id"], listable["id"]}
    assert client.get("/api/garments", params={"style": "通勤"}).json()["total"] == 1
    assert client.get("/api/garments", params={"category": "下装"}).json()["total"] == 0
    assert client.get(f"/api/garments/{published['id']}").status_code == 200
    assert client.get(f"/api/garments/{draft['id']}").status_code == 404
    assert client.get(f"/api/garments/{'0' * 32}").status_code == 404

    looks = client.get("/api/looks").json()
    assert looks["total"] == 1
    exposed = looks["items"][0]
    assert exposed["id"] == public_look["id"]
    # A published look never leaks a draft member.
    assert [item["id"] for item in exposed["items"]] == [published["id"]]
    assert exposed["items"][0]["images"][0]["url"].startswith("/api/garment-images/")
    assert client.get(f"/api/looks/{public_look['id']}").status_code == 200
    draft_look = client.get("/api/merchant/looks", headers=auth(token)).json()["items"]
    hidden = next(item for item in draft_look if item["status"] == "draft")
    assert client.get(f"/api/looks/{hidden['id']}").status_code == 404
    assert client.get(f"/api/looks/{'0' * 32}").status_code == 404
    assert client.get("/api/looks", params={"style": "街头"}).json()["total"] == 0


def test_garment_image_route_refuses_traversal(env):
    client, _, _ = env
    token = token_for(client)
    image_id = create_garment(client, token, images=[png_bytes()]).json()["images"][0]["id"]
    for candidate in (
        "..%2f..%2fetc%2fpasswd",
        "%2e%2e%2f%2e%2e%2fmerchants.sqlite3",
        "....//....//merchants.sqlite3",
        "a" * 31,
        "A" * 32,
        "z" * 32,
        "0" * 32,
    ):
        response = client.get(f"/api/garment-images/{candidate}")
        assert response.status_code == 404, candidate
    assert client.get(f"/api/garment-images/{image_id}").status_code == 200


# --------------------------------------------------------------- body profile


def test_body_profile_is_a_local_single_user_api(env):
    client, settings, _ = env
    fields = ("height_cm", "weight_kg", "shoulder_cm", "bust_cm", "waist_cm", "hip_cm")

    # The 人体建模 page calls these without any token; they must not 401.
    empty = client.get("/api/body-profile")
    assert empty.status_code == 200
    assert set(empty.json()) == {
        "job_id", "updated", "height_cm", "weight_kg", "shoulder_cm",
        "bust_cm", "waist_cm", "hip_cm",
    }
    assert empty.json()["job_id"] is None
    assert all(empty.json()[field] is None for field in fields)

    saved = client.put("/api/body-profile", json={
        "height_cm": 172, "weight_kg": 62, "waist_cm": 74,
    })
    assert saved.status_code == 200
    assert saved.json()["height_cm"] == 172 and saved.json()["bust_cm"] is None
    assert client.get("/api/body-profile").json()["waist_cm"] == 74

    # A merchant token is allowed too, and both callers share one profile.
    token = token_for(client)
    assert client.get("/api/body-profile", headers=auth(token)).status_code == 200
    assert client.put(
        "/api/body-profile", headers=auth(token), json={"hip_cm": 96}
    ).status_code == 200
    assert client.get("/api/body-profile").json()["hip_cm"] == 96

    job = client.put("/api/body-profile", json={"job_id": "job-alpha", "height_cm": 180})
    assert job.status_code == 200 and job.json()["job_id"] == "job-alpha"
    assert client.get(
        "/api/body-profile", params={"job_id": "job-alpha"}
    ).json()["height_cm"] == 180
    # An unknown job falls back to the default profile.
    fallback = client.get("/api/body-profile", params={"job_id": "job-beta"}).json()
    assert fallback["height_cm"] == 172 and fallback["job_id"] is None
    # A partial write for a job keeps that job's own values, not the default's.
    assert client.put("/api/body-profile", json={
        "job_id": "job-alpha", "weight_kg": 70,
    }).json()["height_cm"] == 180

    # An explicit null clears one field.
    cleared = client.put("/api/body-profile", json={"waist_cm": None})
    assert cleared.status_code == 200 and cleared.json()["waist_cm"] is None

    # Out-of-range values are refused and change nothing in the database.
    before = client.get("/api/body-profile").json()
    with MerchantStore(settings.data_dir).connect() as conn:
        rows_before = conn.execute("SELECT COUNT(*) FROM body_profiles").fetchone()[0]
    assert client.put("/api/body-profile", json={"height_cm": 99}).status_code == 422
    assert client.put("/api/body-profile", json={"weight_kg": 301}).status_code == 422
    assert client.put("/api/body-profile", json={"chest_cm": 90}).status_code == 422
    assert client.put("/api/body-profile", json={"job_id": ""}).status_code == 422
    assert client.put("/api/body-profile", json={"height_cm": "172"}).status_code == 422
    assert client.get("/api/body-profile").json() == before
    with MerchantStore(settings.data_dir).connect() as conn:
        rows_after = conn.execute("SELECT COUNT(*) FROM body_profiles").fetchone()[0]
    assert rows_after == rows_before


# ------------------------------------------------------- middleware behaviour


def test_upload_routes_keep_the_size_guards(env, image_bytes):
    client, _, _ = env
    token = token_for(client)
    oversized = client.post(
        "/api/merchant/garments",
        headers={"Content-Length": str(MERCHANT_BODY_LIMIT + 1), **auth(token)},
        content=b"",
    )
    assert oversized.status_code == 413
    single = client.post(
        "/api/assets",
        headers={"Content-Length": str(MERCHANT_BODY_LIMIT + 1)},
        content=b"",
    )
    assert single.status_code == 413


def test_upload_without_content_length_is_refused(env):
    client, _, _ = env
    chunked = client.post(
        "/api/merchant/garments", content=iter([b"payload=%7B%7D"])
    )
    assert chunked.status_code == 411
    assert "Content-Length" in chunked.json()["detail"]


def test_foreign_origin_is_refused_for_merchant_writes(env):
    client, _, _ = env
    token = token_for(client)
    refused = client.post(
        "/api/merchant/looks",
        headers={"Origin": "https://evil.example", **auth(token)},
        json={"name": "越权来源", "status": "draft"},
    )
    assert refused.status_code == 403
    assert client.post(
        "/api/merchant/garments",
        headers={"Origin": "https://evil.example", **auth(token)},
        data={"payload": json.dumps(garment_payload())},
    ).status_code == 403


# --------------------------------------------------------------------- hashing


def test_password_hashing_is_salted_and_verified():
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second
    assert first.startswith(f"scrypt${2**14}$8$1$")
    assert verify_password(PASSWORD, first) is True
    assert verify_password("another-password", first) is False
    for broken in ("", "plain", "scrypt$1$2$3$4", "bcrypt$1$2$3$aa$bb", "scrypt$x$8$1$aa$bb"):
        assert verify_password(PASSWORD, broken) is False


def test_token_encoding_round_trip():
    token, expires_in = encode_token("f" * 64, "m" * 32, hours=3)
    assert expires_in == 3 * 3600
    claims = decode_token("f" * 64, token)
    assert claims["sub"] == "m" * 32
    with pytest.raises(ValueError):
        decode_token("e" * 64, token)
    # Tamper in the middle of the signature: the trailing base64 character has
    # spare bits, so changing it may decode to the same 32 bytes.
    header, payload, signature = token.split(".")
    flipped = ("A" if signature[5] != "A" else "B") + signature[6:]
    with pytest.raises(ValueError):
        decode_token("f" * 64, f"{header}.{payload}.{flipped}")
    with pytest.raises(ValueError):
        decode_token("f" * 64, "only.two")
    with pytest.raises(ValueError):
        decode_token("f" * 64, f"{header}.{payload}.")


def test_the_api_document_declares_the_bearer_scheme(env):
    """The OpenAPI document must carry the scheme, or /docs cannot authorise.

    Without it the docs page offers no Authorize button, which would leave the
    whole merchant import surface usable only by writing raw HTTP by hand.
    """
    client, _settings, _config_path = env
    document = client.get("/openapi.json").json()

    scheme = document["components"]["securitySchemes"]["HTTPBearer"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"

    protected = (
        ("/api/merchant/me", "get"),
        ("/api/merchant/garments", "post"),
        ("/api/merchant/garments", "get"),
        ("/api/merchant/looks", "post"),
    )
    for path, method in protected:
        assert document["paths"][path][method]["security"] == [{"HTTPBearer": []}], path

    # Signing up, logging in and the public catalogue must stay open.
    for path, method in (
        ("/api/merchant/register", "post"),
        ("/api/merchant/login", "post"),
        ("/api/garments", "get"),
        ("/api/looks", "get"),
        ("/api/body-profile", "get"),
    ):
        assert not document["paths"][path][method].get("security"), path
