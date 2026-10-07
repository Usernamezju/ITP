"""The operator console owns the product-image model configuration."""

import io
import json
import logging
import stat

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp.api import create_app
from itp.config import Settings
from itp.merchant_auth import encode_token, hash_password, resolve_jwt_secret
from itp.product_settings import validate_product_update

PASSWORD = "admin-password-123"
ARK = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def ai_transport(status=200, answer="正常"):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": {"code": "AccountOverdueError"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
    return httpx.MockTransport(handler)


@pytest.fixture
def admin(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret="a" * 64,
                 environment="test", pose_api_key="pose-key-value"),
        start_worker=False, config_path=tmp_path / ".env",
        product_ai_transport=ai_transport())
    with TestClient(app, base_url="http://localhost:8000") as client:
        store = app.state.merchants
        account = store.create_merchant(
            name="ops", display_name="平台管理员", contact="",
            password_hash=hash_password(PASSWORD), quota=0, role="admin")
        token, _ = encode_token(resolve_jwt_secret(app), account["id"], hours=12,
                                password_hash=account["password_hash"])
        client.headers["Authorization"] = "Bearer " + token
        yield client, tmp_path


def test_only_an_admin_may_read_or_write_the_model_configuration(admin):
    client, tmp_path = admin
    anonymous = TestClient(client.app, base_url="http://localhost:8000")
    assert anonymous.get("/api/admin/product-ai").status_code == 401
    assert anonymous.post("/api/admin/product-ai/config",
                          json={"product_ai_model": "x"}).status_code == 401

    store = client.app.state.merchants
    customer = store.create_merchant(name="alice", display_name="顾客", contact="",
                                     password_hash=hash_password(PASSWORD), quota=0,
                                     role="customer")
    token, _ = encode_token(resolve_jwt_secret(client.app), customer["id"], hours=12,
                            password_hash=customer["password_hash"])
    refused = client.post("/api/admin/product-ai/config", json={"product_ai_model": "x"},
                          headers={"Authorization": "Bearer " + token})
    assert refused.status_code == 403
    assert not (tmp_path / ".env").exists()


def test_the_operator_switches_to_another_endpoint_model_and_key(admin):
    client, tmp_path = admin
    before = client.get("/api/admin/product-ai").json()["settings"]
    assert before["ready"] is True          # the pose key is the documented fallback
    assert before["key_set"] is False and before["key_from_pose"] is True

    response = client.post("/api/admin/product-ai/config", json={
        "product_ai_endpoint": ARK,
        "product_ai_model": "doubao-seed-2-1-lite-260915",
        "product_ai_api_key": "ark-secret-value",
    })
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["settings"] == {
        "endpoint": ARK, "model": "doubao-seed-2-1-lite-260915",
        "key_set": True, "key_from_pose": False, "ready": True,
    }
    assert document["check"]["ok"] is True and "已返回" in document["check"]["message"]
    assert "ark-secret-value" not in response.text

    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f'ITP_PRODUCT_AI_ENDPOINT="{ARK}"' in written
    assert 'ITP_PRODUCT_AI_API_KEY="ark-secret-value"' in written
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    # The running app answers with the new model immediately.
    assert client.app.state.settings.product_ai_model == "doubao-seed-2-1-lite-260915"
    assert client.get("/api/admin/settings").json()["product_ai_model"] == (
        "doubao-seed-2-1-lite-260915")


@pytest.mark.parametrize("body,message", [
    ({"product_ai_endpoint": "http://insecure.example.com/v1/chat/completions"}, "HTTPS"),
    ({"product_ai_endpoint": "https://example.com/v1/images"}, "chat/completions"),
    ({"product_ai_endpoint": "https://example.com/v1/chat/completions?x=1"}, "HTTPS"),
    ({"product_ai_model": "model with spaces"}, "模型名"),
    ({"product_ai_model": "m" * 81}, "80"),
    ({"product_ai_api_key": "key\u0000with-control"}, "控制字符"),
    ({"unknown_field": "x"}, "unknown_field"),
])
def test_bad_values_are_refused_before_anything_is_written(admin, body, message):
    client, tmp_path = admin
    response = client.post("/api/admin/product-ai/config", json=body)
    assert response.status_code == 422, body
    assert message in response.text
    assert not (tmp_path / ".env").exists()


def test_an_empty_update_is_refused(admin):
    client, _ = admin
    response = client.post("/api/admin/product-ai/config", json={})
    assert response.status_code == 422 and "没有需要保存的改动" in response.json()["detail"]


def test_a_failing_provider_keeps_the_configuration_and_says_why(admin):
    client, tmp_path = admin
    client.app.state.product_ai.transport = ai_transport(status=403)
    response = client.post("/api/admin/product-ai/config", json={"product_ai_model": "gpt-4o"})
    assert response.status_code == 200
    document = response.json()
    assert document["settings"]["model"] == "gpt-4o"
    assert document["check"]["ok"] is False
    assert "稍后重试" in document["check"]["message"]
    # The settings file kept what the operator asked for; only the check failed.
    assert 'ITP_PRODUCT_AI_MODEL="gpt-4o"' in (tmp_path / ".env").read_text(encoding="utf-8")


def test_clearing_the_key_falls_back_to_the_pose_credential(admin):
    client, _ = admin
    client.post("/api/admin/product-ai/config", json={"product_ai_api_key": "temporary"})
    cleared = client.post("/api/admin/product-ai/config", json={"product_ai_api_key": ""})
    assert cleared.status_code == 200
    assert cleared.json()["settings"]["key_set"] is False
    assert cleared.json()["settings"]["key_from_pose"] is True
    assert cleared.json()["settings"]["ready"] is True


def test_process_environment_wins_over_the_console(admin, monkeypatch):
    client, tmp_path = admin
    monkeypatch.setenv("ITP_PRODUCT_AI_MODEL", "from-the-environment")
    response = client.post("/api/admin/product-ai/config", json={"product_ai_model": "gpt-4o"})
    assert response.status_code == 409
    assert not (tmp_path / ".env").exists()


def test_the_key_never_reaches_the_log(admin, caplog):
    client, _ = admin
    with caplog.at_level(logging.DEBUG):
        client.post("/api/admin/product-ai/config", json={"product_ai_api_key": "ark-secret-value"})
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "product_ai_api_key" in logged      # field names are audited
    assert "ark-secret-value" not in logged


def test_the_validator_keeps_the_endpoint_and_model_rules_in_one_place():
    settings = Settings(_env_file=None, pose_api_key="k" * 8)
    for endpoint in ("http://x.example.com/v1/chat/completions",
                     "https://x.example.com/v1/embeddings"):
        with pytest.raises(ValueError):
            validate_product_update(settings, _body(product_ai_endpoint=endpoint))
    updated, changes = validate_product_update(
        settings, _body(product_ai_endpoint=ARK, product_ai_model="gpt-4o"))
    assert changes == {"product_ai_endpoint": ARK, "product_ai_model": "gpt-4o"}
    assert updated.product_ai_endpoint == ARK and updated.product_ai_model == "gpt-4o"


def _body(**values):
    from itp.product_settings import ProductSettingsUpdate
    return ProductSettingsUpdate(**values)


def test_the_probe_sends_a_real_image_and_reports_the_answer(admin):
    client, _ = admin
    seen = []
    client.app.state.product_ai.transport = httpx.MockTransport(
        lambda request: (seen.append(request), httpx.Response(
            200, json={"choices": [{"message": {"content": "正常"}}]}))[1])
    document = client.post("/api/admin/product-ai/config",
                           json={"product_ai_model": "qwen-vl-max"}).json()
    assert document["check"]["ok"] is True and "qwen-vl-max 已返回" in document["check"]["message"]
    payload = json.loads(seen[0].content)
    image = payload["messages"][0]["content"][0]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(__import__("base64").b64decode(image.split(",", 1)[1]))) as probe:
        assert probe.size == (32, 32)
