"""Operator-managed payment credentials: admin-only, validated, never echoed.

Every test here runs offline: the Alipay and WeChat gateways are signed local
transports, so the probes exercise the real protocol code without a network.
"""

import json
import logging
import stat
import time
from urllib.parse import parse_qsl

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.merchant_auth import encode_token, hash_password, resolve_jwt_secret
from itp.payments import AlipayProvider, rsa_sign, rsa_verify

SECRET = "c" * 64
NOTIFY = "https://payments.example.org"
PASSWORD = "original-password"
# The identifier WeChat sends in `Wechatpay-Serial`, also the stored key name.
PLATFORM_KEY_ID = "PUB_KEY_ID_0123456789"


def signed_headers(raw: bytes, key, serial: str = PLATFORM_KEY_ID) -> dict:
    timestamp, nonce = str(int(time.time())), "test-response-nonce"
    signature = rsa_sign(
        key, timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
    )
    return {"Wechatpay-Timestamp": timestamp, "Wechatpay-Nonce": nonce,
            "Wechatpay-Serial": serial, "Wechatpay-Signature": signature}


def pem_private(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def pem_public(key):
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


@pytest.fixture(scope="module")
def keys():
    merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return merchant, platform, stranger


@pytest.fixture(scope="module")
def material(keys):
    return {
        "alipay_app_id": "2021000000000000",
        "alipay_seller_id": "2088000000000000",
        "alipay_private_key": pem_private(keys[0]),
        "alipay_public_key": pem_public(keys[1]),
        "wechat_app_id": "wx0000000000000000",
        "wechat_mch_id": "1900000000",
        "wechat_merchant_serial": "4A5B6C7D8E9F00112233445566778899AABBCCDD",
        "wechat_private_key": pem_private(keys[0]),
        "wechat_api_v3_key": "k" * 32,
        "wechat_platform_key_id": PLATFORM_KEY_ID,
        "wechat_platform_public_key": pem_public(keys[1]),
    }


def gateway(keys, *, reject=False):
    """Signed stand-ins for both official gateways; ``reject`` breaks the answers."""
    signing = keys[2] if reject else keys[1]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openapi.alipay.com":
            values = dict(parse_qsl(request.content.decode()))
            if not reject:
                rsa_verify(keys[0].public_key(), values["sign"],
                           AlipayProvider.canonical(values))
            payload = {"code": "40004", "msg": "Business Failed",
                       "sub_code": "ACQ.TRADE_NOT_EXIST", "sub_msg": "交易不存在"}
            inner = json.dumps(payload, ensure_ascii=False)
            body = ("{\"alipay_trade_query_response\":" + inner + ",\"sign\":"
                    + json.dumps(rsa_sign(signing, inner.encode())) + "}")
            return httpx.Response(200, content=body.encode())
        authorization = request.headers["Authorization"].split(" ", 1)[1]
        values = {key: value.strip('"') for key, value in
                  (piece.split("=", 1) for piece in authorization.split(","))}
        if not reject:
            message = (f"{request.method}\n{request.url.raw_path.decode()}\n"
                       f"{values['timestamp']}\n{values['nonce_str']}\n").encode() \
                + request.content + b"\n"
            rsa_verify(keys[0].public_key(), values["signature"], message)
        raw = json.dumps({"code": "ORDER_NOT_EXIST", "message": "订单不存在"}).encode()
        return httpx.Response(404, content=raw, headers=signed_headers(raw, signing))

    return httpx.MockTransport(handler)


@pytest.fixture
def build(tmp_path, keys):
    """Create an app whose payment channels talk to the signed local gateway."""
    created = []

    def factory(*, reject=False):
        app = create_app(
            Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                     environment="test", public_origin=NOTIFY),
            start_worker=False, config_path=tmp_path / ".env",
            payment_transport=gateway(keys, reject=reject),
        )
        client = TestClient(app, base_url="http://localhost:8000")
        created.append(client)
        return app, client

    yield factory
    for client in created:
        client.close()


def auth_headers(client, *, role="admin", name=None):
    store = client.app.state.merchants
    name = name or role
    account = store.merchant_by_name(name)
    if not account:
        account = store.create_merchant(
            name=name, display_name=f"测试{role}", contact="",
            password_hash=hash_password(PASSWORD), quota=0, role=role,
        )
    token, _ = encode_token(resolve_jwt_secret(client.app), account["id"], hours=12,
                            password_hash=account["password_hash"])
    return {"Authorization": "Bearer " + token}


def test_only_an_admin_may_read_or_write_payment_credentials(build, material, tmp_path):
    app, client = build()
    assert client.get("/api/admin/payments").status_code == 401
    assert client.post("/api/admin/payments/config", json=material).status_code == 401
    for role in ("customer", "merchant"):
        headers = auth_headers(client, role=role)
        assert client.get("/api/admin/payments", headers=headers).status_code == 403
        blocked = client.post("/api/admin/payments/config", json=material, headers=headers)
        assert blocked.status_code == 403
    assert not app.state.settings.alipay_app_id
    assert not (tmp_path / ".env").exists()


def test_admin_saves_credentials_and_the_reply_never_echoes_them(build, material, tmp_path):
    _, client = build()
    headers = auth_headers(client)
    (tmp_path / ".env").write_text("ITP_JWT_SECRET=keep-me\n", encoding="utf-8")
    response = client.post("/api/admin/payments/config", json=material, headers=headers)
    assert response.status_code == 200, response.text
    for secret in (material["alipay_private_key"], material["wechat_private_key"],
                   material["wechat_platform_public_key"], material["wechat_api_v3_key"]):
        assert secret not in response.text
        assert secret.replace("\n", "\\n") not in response.text
    document = response.json()
    assert document["settings"]["alipay"] == {
        "app_id_set": True, "seller_id_set": True,
        "private_key_set": True, "public_key_set": True,
    }
    assert document["settings"]["wechat"]["platform_key_ids"] == [PLATFORM_KEY_ID]
    assert document["settings"]["wechat"]["api_v3_key_set"] is True
    assert {channel["id"]: channel["ready"] for channel in document["status"]["channels"]} == {
        "alipay": True, "wechat": True,
    }
    assert document["status"]["callbacks"]["alipay"] == NOTIFY + "/api/payments/callbacks/alipay"
    assert [check["ok"] for check in document["checks"]] == [True, True]

    written = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "ITP_JWT_SECRET=keep-me" in written
    assert f"ITP_ALIPAY_APP_ID={json.dumps(material['alipay_app_id'])}" in written
    platform = {material["wechat_platform_key_id"]: material["wechat_platform_public_key"]}
    assert f"ITP_WECHAT_PLATFORM_KEYS={json.dumps(platform, ensure_ascii=False)}" in written
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    # The reload is in-process: the same app now answers with ready channels.
    assert client.get("/api/payments/methods").json()["methods"][0]["ready"] is True


@pytest.mark.parametrize("field,value", [
    ("alipay_private_key", "not-a-private-key"),
    ("alipay_public_key", "not-a-public-key"),
    ("wechat_private_key",
     pem_private(rsa.generate_private_key(public_exponent=65537, key_size=1024))),
    ("wechat_api_v3_key", "too-short"),
    ("wechat_merchant_serial", "serial with spaces"),
    ("wechat_platform_key_id", "id\u0000with-control"),
])
def test_unusable_material_is_refused_before_anything_is_written(build, material, tmp_path,
                                                                 field, value):
    app, client = build()
    body = {**material, field: value}
    response = client.post("/api/admin/payments/config", json=body, headers=auth_headers(client))
    assert response.status_code == 422
    assert not (tmp_path / ".env").exists()
    assert not app.state.settings.alipay_app_id


def test_a_half_filled_platform_key_pair_is_refused(build, material):
    _, client = build()
    body = {**material, "wechat_platform_key_id": "PUB_KEY_ID_NEW",
            "wechat_platform_public_key": ""}
    response = client.post("/api/admin/payments/config", json=body, headers=auth_headers(client))
    assert response.status_code == 422
    assert "同时填写" in response.json()["detail"]


def test_rotating_and_removing_platform_keys_keeps_the_others(build, material):
    app, client = build()
    headers = auth_headers(client)
    created = client.post("/api/admin/payments/config", json=material, headers=headers)
    assert created.status_code == 200, created.text
    rotated = pem_public(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    second = client.post("/api/admin/payments/config", headers=headers, json={
        "wechat_platform_key_id": "PUB_KEY_ID_ROTATED", "wechat_platform_public_key": rotated})
    assert second.status_code == 200, second.text
    assert second.json()["settings"]["wechat"]["platform_key_ids"] == [
        PLATFORM_KEY_ID, "PUB_KEY_ID_ROTATED"]
    removed = client.post("/api/admin/payments/config", headers=headers,
                          json={"wechat_platform_key_remove": PLATFORM_KEY_ID})
    assert removed.json()["settings"]["wechat"]["platform_key_ids"] == ["PUB_KEY_ID_ROTATED"]
    assert set(app.state.settings.wechat_platform_keys) == {"PUB_KEY_ID_ROTATED"}


def test_a_rejected_gateway_keeps_credentials_but_reports_the_failure(build, material):
    _, client = build(reject=True)
    response = client.post("/api/admin/payments/config", json=material,
                           headers=auth_headers(client))
    assert response.status_code == 200, response.text
    document = response.json()
    # The material is a valid RSA pair, so the channel is enabled…
    assert all(channel["ready"] for channel in document["status"]["channels"])
    # …but the official probe says the credentials are not accepted.
    checks = {check["channel"]: check for check in document["checks"]}
    assert checks["alipay"]["ok"] is False and checks["wechat"]["ok"] is False
    assert "渠道校验未通过" in checks["alipay"]["message"]


def test_process_environment_wins_over_the_console(build, material, monkeypatch, tmp_path):
    _, client = build()
    monkeypatch.setenv("ITP_ALIPAY_APP_ID", "from-the-environment")
    response = client.post("/api/admin/payments/config", json=material,
                           headers=auth_headers(client))
    assert response.status_code == 409
    assert not (tmp_path / ".env").exists()


def test_secrets_never_reach_the_log(build, material, caplog):
    _, client = build()
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/admin/payments/config", json=material,
                               headers=auth_headers(client))
    assert response.status_code == 200
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "alipay_private_key" in logged  # field names are audited
    for secret in (material["alipay_private_key"], material["wechat_private_key"],
                   material["wechat_api_v3_key"], material["wechat_platform_public_key"]):
        assert secret not in logged


def test_reading_the_configuration_reports_only_configured_flags(build, material):
    _, client = build()
    headers = auth_headers(client)
    empty = client.get("/api/admin/payments", headers=headers).json()
    assert empty["settings"]["alipay"]["app_id_set"] is False
    assert empty["status"]["notify_origin"] == NOTIFY
    assert "app_id" not in empty["settings"]["alipay"]
    client.post("/api/admin/payments/config", json=material, headers=headers)
    filled = client.get("/api/admin/payments", headers=headers).json()
    assert filled["settings"]["wechat"]["merchant_serial_set"] is True
    assert "1900000000" not in json.dumps(filled)


def test_an_empty_update_is_refused_and_changes_nothing(build, tmp_path):
    app, client = build()
    response = client.post("/api/admin/payments/config", json={}, headers=auth_headers(client))
    assert response.status_code == 422
    assert "没有需要保存的改动" in response.json()["detail"]
    assert not (tmp_path / ".env").exists()
    assert not app.state.settings.alipay_app_id
