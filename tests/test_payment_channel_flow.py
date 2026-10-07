"""End-to-end recharge through the real Alipay and WeChat protocol code.

The order, QR generation, official callback verification, active query,
idempotent fulfillment and wallet credit all run through ``AlipayProvider`` and
``WechatProvider``; only the HTTPS peer is a signed local gateway, so this needs
no merchant account and touches no live payment API.
"""

import base64
import json
import time
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.payments import AlipayProvider, rsa_sign, rsa_verify

NOTIFY = "https://pay.example.org"
PASSWORD = "recharge-password-123"
V3_KEY = "v" * 32
PLATFORM_KEY_ID = "PUB_KEY_ID_INTEGRATION"


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
    return merchant, platform


def wechat_headers(raw, platform):
    timestamp, nonce = str(int(time.time())), "integration-nonce"
    signature = rsa_sign(
        platform, timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
    )
    return {"Wechatpay-Timestamp": timestamp, "Wechatpay-Nonce": nonce,
            "Wechatpay-Serial": PLATFORM_KEY_ID, "Wechatpay-Signature": signature}


class Gateway:
    """A signed stand-in for both official gateways, driven by the test."""

    def __init__(self, keys):
        self.merchant, self.platform = keys
        self.paid = set()
        self.requests = []

    def alipay(self, payload):
        inner = json.dumps(payload, ensure_ascii=False)
        return ("{\"alipay_trade_query_response\":"
                if "sub_code" in payload or "trade_status" in payload
                else "{\"alipay_trade_precreate_response\":") + inner + ",\"sign\":" \
            + json.dumps(rsa_sign(self.platform, inner.encode())) + "}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "openapi.alipay.com":
            values = dict(parse_qsl(request.content.decode()))
            rsa_verify(self.merchant.public_key(), values["sign"],
                       AlipayProvider.canonical(values))
            business = json.loads(values["biz_content"])
            order_id = business["out_trade_no"]
            self.requests.append((values["method"], order_id))
            if values["method"] == "alipay.trade.precreate":
                payload = {"code": "10000", "out_trade_no": order_id,
                           "qr_code": f"https://qr.alipay.com/{order_id}"}
                body = self.alipay(payload).replace("alipay_trade_query_response",
                                                    "alipay_trade_precreate_response")
            elif order_id in self.paid:
                payload = {"code": "10000", "out_trade_no": order_id,
                           "trade_no": "alipay-" + order_id, "trade_status": "TRADE_SUCCESS",
                           "total_amount": "50.00"}
                body = self.alipay(payload)
            else:
                payload = {"code": "40004", "msg": "Business Failed",
                           "sub_code": "ACQ.TRADE_NOT_EXIST", "sub_msg": "交易不存在"}
                body = self.alipay(payload)
            return httpx.Response(200, content=body.encode())
        authorization = request.headers["Authorization"].split(" ", 1)[1]
        values = {key: value.strip('"') for key, value in
                  (piece.split("=", 1) for piece in authorization.split(","))}
        signed = (f"{request.method}\n{request.url.raw_path.decode()}\n"
                  f"{values['timestamp']}\n{values['nonce_str']}\n").encode() \
            + request.content + b"\n"
        rsa_verify(self.merchant.public_key(), values["signature"], signed)
        if request.method == "POST":
            order_id = json.loads(request.content)["out_trade_no"]
            self.requests.append(("native", order_id))
            raw = json.dumps({"code_url": f"weixin://wxpay/{order_id}"}).encode()
            return httpx.Response(200, content=raw, headers=wechat_headers(raw, self.platform))
        order_id = request.url.path.rsplit("/", 1)[-1]
        self.requests.append(("query", order_id))
        if order_id in self.paid:
            raw = json.dumps({"appid": "wx-itp-test", "mchid": "1900000000",
                              "out_trade_no": order_id, "transaction_id": "wechat-" + order_id,
                              "trade_state": "SUCCESS",
                              "amount": {"total": 5000, "currency": "CNY"}}).encode()
            return httpx.Response(200, content=raw, headers=wechat_headers(raw, self.platform))
        raw = json.dumps({"code": "ORDER_NOT_EXIST", "message": "订单不存在"}).encode()
        return httpx.Response(404, content=raw, headers=wechat_headers(raw, self.platform))


@pytest.fixture
def shop(tmp_path, keys):
    gateway = Gateway(keys)
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", jwt_secret="d" * 64,
        environment="test", public_origin=NOTIFY,
        alipay_app_id="2021000000000000", alipay_seller_id="2088000000000000",
        alipay_private_key=pem_private(keys[0]), alipay_public_key=pem_public(keys[1]),
        wechat_app_id="wx-itp-test", wechat_mch_id="1900000000",
        wechat_merchant_serial="4A5B6C7D8E9F00112233445566778899AABBCCDD",
        wechat_private_key=pem_private(keys[0]), wechat_api_v3_key=V3_KEY,
        wechat_platform_keys={PLATFORM_KEY_ID: pem_public(keys[1])},
    )
    app = create_app(settings, start_worker=False, config_path=tmp_path / ".env",
                     payment_transport=httpx.MockTransport(gateway))
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.post("/api/auth/register", json={"name": "buyer", "display_name": "充值顾客",
                                                "password": PASSWORD, "role": "customer"})
        login = client.post("/api/auth/login", json={"name": "buyer", "password": PASSWORD})
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        client.headers["Idempotency-Key"] = "recharge-key-" + str(time.time())
        yield gateway, client


def recharge(client, provider, cents=5000):
    response = client.post("/api/account/orders",
                           json={"kind": "recharge", "provider": provider,
                                 "amount_cents": cents})
    assert response.status_code == 201, response.text
    return response.json()


def balance(client):
    return client.get("/api/account/commerce").json()["balance_cents"]


def alipay_notification(keys, order_id, *, amount="50.00", status="TRADE_SUCCESS"):
    values = {"app_id": "2021000000000000", "seller_id": "2088000000000000",
              "out_trade_no": order_id, "trade_no": "alipay-" + order_id,
              "total_amount": amount, "trade_status": status, "sign_type": "RSA2"}
    values["sign"] = rsa_sign(keys[1], AlipayProvider.canonical(values, callback=True))
    return urlencode(values).encode()


def wechat_notification(keys, order_id, *, amount=5000):
    paid = {"appid": "wx-itp-test", "mchid": "1900000000", "trade_state": "SUCCESS",
            "out_trade_no": order_id, "transaction_id": "wechat-" + order_id,
            "amount": {"total": amount, "currency": "CNY"}}
    nonce, aad = b"123456789012", b"transaction"
    cipher = AESGCM(V3_KEY.encode()).encrypt(nonce, json.dumps(paid).encode(), aad)
    raw = json.dumps({"event_type": "TRANSACTION.SUCCESS", "resource": {
        "algorithm": "AEAD_AES_256_GCM", "ciphertext": base64.b64encode(cipher).decode(),
        "nonce": nonce.decode(), "associated_data": aad.decode()}}).encode()
    return raw, wechat_headers(raw, keys[1])


def test_alipay_order_qr_official_callback_and_idempotent_credit(shop, keys):
    gateway, client = shop
    order = recharge(client, "alipay")
    # A real precreate happened and the browser got a QR image, not a success flag.
    assert order["state"] == "pending" and order["checkout"]["qr_image"].startswith(
        "data:image/png;base64,")
    assert gateway.requests[-1][0] == "alipay.trade.precreate"
    assert balance(client) == 0

    before = balance(client)
    notification = alipay_notification(keys, order["id"])
    first = client.post("/api/payments/callbacks/alipay", content=notification)
    assert first.status_code == 200 and first.text == "success"
    assert balance(client) == before + 5000
    ledger = client.get("/api/account/ledger").json()["items"]
    assert [entry["kind"] for entry in ledger] == ["recharge"]
    assert client.get(f"/api/account/orders/{order['id']}").json()["state"] == "paid"

    # The official platform retries notifications: the balance must not move twice.
    replay = client.post("/api/payments/callbacks/alipay", content=notification)
    assert replay.status_code == 200 and balance(client) == before + 5000
    assert len(client.get("/api/account/ledger").json()["items"]) == 1


def test_a_tampered_or_wrong_amount_notification_never_credits(shop, keys):
    gateway, client = shop
    order = recharge(client, "alipay")
    before = balance(client)
    forged = alipay_notification(keys, order["id"], amount="0.01")
    assert client.post("/api/payments/callbacks/alipay", content=forged).status_code == 400
    tampered = alipay_notification(keys, order["id"]).replace(b"50.00", b"99.00")
    assert client.post("/api/payments/callbacks/alipay", content=tampered).status_code == 400
    assert balance(client) == before
    assert client.get(f"/api/account/orders/{order['id']}").json()["state"] == "pending"


def test_wechat_native_callback_and_active_query_both_credit_once(shop, keys):
    gateway, client = shop
    first = recharge(client, "wechat")
    assert first["checkout"]["qr_image"].startswith("data:image/png;base64,")
    raw, headers = wechat_notification(keys, first["id"])
    response = client.post("/api/payments/callbacks/wechat", content=raw, headers=headers)
    assert response.status_code == 200 and response.json()["code"] == "SUCCESS"
    assert balance(client) == 5000

    # An answered-but-unnotified order is settled by the active query instead.
    client.headers["Idempotency-Key"] = "second-recharge-key"
    second = recharge(client, "wechat")
    assert balance(client) == 5000
    gateway.paid.add(second["id"])
    refreshed = client.post(f"/api/account/orders/{second['id']}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["state"] == "paid"
    assert balance(client) == 10000
    # Querying again changes nothing.
    client.post(f"/api/account/orders/{second['id']}/refresh")
    assert balance(client) == 10000


def test_an_unpaid_alipay_query_leaves_the_order_pending(shop):
    gateway, client = shop
    order = recharge(client, "alipay")
    refreshed = client.post(f"/api/account/orders/{order['id']}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["state"] == "pending"
    assert balance(client) == 0
    assert ("alipay.trade.query", order["id"]) in gateway.requests
