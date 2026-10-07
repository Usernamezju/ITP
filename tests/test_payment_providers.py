"""Official protocol checks with generated test keys and offline HTTP transports."""

import base64
import json
import time
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from itp.config import Settings
from itp.payments import AlipayProvider, PaymentError, WechatProvider, rsa_sign, rsa_verify


@pytest.fixture(scope="module")
def keys():
    merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = merchant.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = (
        platform.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return merchant, platform, private, public


def settings_for(tmp_path, keys):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        payment_notify_origin="https://payments.example.org",
        alipay_app_id="test-app",
        alipay_seller_id="test-seller",
        alipay_private_key=keys[2],
        alipay_public_key=keys[3],
        wechat_app_id="test-app",
        wechat_mch_id="test-merchant",
        wechat_merchant_serial="TEST-SERIAL",
        wechat_private_key=keys[2],
        wechat_api_v3_key="k" * 32,
        wechat_platform_keys={"PUB_KEY_ID_TEST": keys[3]},
    )


def alipay_notification(provider, platform, **overrides):
    values = {
        "app_id": provider.app_id,
        "seller_id": provider.merchant_id,
        "out_trade_no": "test-order",
        "trade_no": "official-tx",
        "total_amount": "30.00",
        "trade_status": "TRADE_SUCCESS",
        "sign_type": "RSA2",
    }
    values.update(overrides)
    values["sign"] = rsa_sign(platform, provider.canonical(values, callback=True))
    return urlencode(values).encode()


def test_alipay_callback_signature_identity_and_amount(tmp_path, keys):
    provider = AlipayProvider(settings_for(tmp_path, keys))
    raw = alipay_notification(provider, keys[1])
    event = provider.callback(raw, {})
    assert event.amount_cents == 3000 and event.transaction_id == "official-tx"
    for changed in ({"app_id": "other-app"}, {"seller_id": "other-seller"}, {"sign_type": "RSA"}):
        with pytest.raises(PaymentError):
            provider.callback(alipay_notification(provider, keys[1], **changed), {})
    with pytest.raises(PaymentError):
        provider.callback(raw.replace(b"30.00", b"99.00"), {})
    with pytest.raises(PaymentError):
        provider.callback(raw + b"&total_amount=30.00", {})


def test_alipay_precreate_query_verify_original_json_and_signed_request(tmp_path, keys):
    seen = []

    def transport(request):
        values = dict(parse_qsl(request.content.decode()))
        assert request.url == "https://openapi.alipay.com/gateway.do"
        rsa_verify(keys[0].public_key(), values["sign"], AlipayProvider.canonical(values))
        business = json.loads(values["biz_content"])
        seen.append(values["method"])
        if values["method"].endswith("precreate"):
            data = {
                "code": "10000",
                "out_trade_no": business["out_trade_no"],
                "qr_code": "https://qr.alipay.com/test",
            }
        else:
            data = {
                "code": "10000",
                "out_trade_no": business["out_trade_no"],
                "trade_no": "official-tx",
                "trade_status": "TRADE_SUCCESS",
                "total_amount": 29.99,
            }
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        name = values["method"].replace(".", "_") + "_response"
        body = (
            "{"
            + json.dumps(name)
            + ":"
            + raw
            + ',"sign":'
            + json.dumps(rsa_sign(keys[1], raw.encode()))
            + "}"
        )
        return httpx.Response(200, content=body.encode())

    provider = AlipayProvider(
        settings_for(tmp_path, keys), transport=httpx.MockTransport(transport)
    )
    order = {"id": "test-order", "amount_cents": 2999, "description": "测试会员"}
    assert provider.create(order, "https://payments.example.org/callback")["qr_code"]
    assert provider.query(order).amount_cents == 2999
    assert seen == ["alipay.trade.precreate", "alipay.trade.query"]


def wechat_headers(raw, platform, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce = "test-response-nonce"
    signature = rsa_sign(
        platform, timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
    )
    return {
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": nonce,
        "Wechatpay-Serial": "PUB_KEY_ID_TEST",
        "Wechatpay-Signature": signature,
    }


def wechat_callback(provider, keys, **overrides):
    paid = {
        "appid": provider.app_id,
        "mchid": provider.merchant_id,
        "trade_state": "SUCCESS",
        "out_trade_no": "test-order",
        "transaction_id": "official-tx",
        "amount": {"total": 3000, "currency": "CNY"},
    }
    paid.update(overrides)
    nonce, aad = b"123456789012", b"transaction"
    cipher = AESGCM(provider.api_key).encrypt(nonce, json.dumps(paid).encode(), aad)
    raw = json.dumps(
        {
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": base64.b64encode(cipher).decode(),
                "nonce": nonce.decode(),
                "associated_data": aad.decode(),
            },
        }
    ).encode()
    return raw, wechat_headers(raw, keys[1])


def test_wechat_callback_rsa_aes_gcm_time_identity_and_currency(tmp_path, keys):
    provider = WechatProvider(settings_for(tmp_path, keys))
    raw, headers = wechat_callback(provider, keys)
    assert provider.callback(raw, headers).amount_cents == 3000
    with pytest.raises(PaymentError):
        provider.callback(raw + b" ", headers)
    with pytest.raises(PaymentError):
        provider.callback(raw, wechat_headers(raw, keys[1], timestamp=int(time.time()) - 301))
    with pytest.raises(PaymentError):
        provider.callback(
            raw, {**headers, "Wechatpay-Signature": "WECHATPAY/SIGNTEST/not-a-signature"}
        )
    with pytest.raises(PaymentError):
        provider.callback(raw, {**headers, "Wechatpay-Serial": "UNKNOWN"})
    for changed in (
        {"mchid": "other"},
        {"appid": "other"},
        {"amount": {"total": 3000, "currency": "USD"}},
    ):
        bad, signed = wechat_callback(provider, keys, **changed)
        with pytest.raises(PaymentError):
            provider.callback(bad, signed)
    altered = json.loads(raw)
    altered["resource"]["ciphertext"] = base64.b64encode(b"invalid ciphertext").decode()
    invalid = json.dumps(altered).encode()
    with pytest.raises(PaymentError):
        provider.callback(invalid, wechat_headers(invalid, keys[1]))


def test_wechat_native_and_query_verify_signed_requests_and_responses(tmp_path, keys):
    calls = []

    def transport(request):
        authorization = request.headers["Authorization"].split(" ", 1)[1]
        values = dict(piece.split("=", 1) for piece in authorization.split(","))
        values = {key: value.strip('"') for key, value in values.items()}
        message = (
            f"{request.method}\n{request.url.raw_path.decode()}\n{values['timestamp']}\n{values['nonce_str']}\n".encode()
            + request.content
            + b"\n"
        )
        rsa_verify(keys[0].public_key(), values["signature"], message)
        calls.append(request.method)
        if request.method == "POST":
            data = {"code_url": "weixin://wxpay/test"}
            assert json.loads(request.content)["amount"] == {"total": 3000, "currency": "CNY"}
        else:
            data = {
                "appid": "test-app",
                "mchid": "test-merchant",
                "out_trade_no": "test-order",
                "transaction_id": "official-tx",
                "trade_state": "SUCCESS",
                "amount": {"total": 3000, "currency": "CNY"},
            }
        raw = json.dumps(data).encode()
        return httpx.Response(200, content=raw, headers=wechat_headers(raw, keys[1]))

    provider = WechatProvider(
        settings_for(tmp_path, keys), transport=httpx.MockTransport(transport)
    )
    order = {"id": "test-order", "amount_cents": 3000, "description": "ITP test"}
    assert provider.create(order, "https://payments.example.org/callback")["qr_code"].startswith(
        "weixin://"
    )
    assert provider.query(order).amount_cents == 3000
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("provider", ["alipay", "wechat"])
def test_unsigned_or_badly_signed_query_response_is_rejected(tmp_path, keys, provider):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"state": "paid"}))
    cls = AlipayProvider if provider == "alipay" else WechatProvider
    engine = cls(settings_for(tmp_path, keys), transport=transport)
    with pytest.raises(PaymentError):
        engine.query({"id": "test-order"})


def test_alipay_credential_probe_accepts_trade_not_exist_and_rejects_answers(tmp_path, keys):
    """A signed ACQ.TRADE_NOT_EXIST proves app id, private key and public key."""
    seen = []

    def transport(request):
        values = dict(parse_qsl(request.content.decode()))
        rsa_verify(keys[0].public_key(), values["sign"], AlipayProvider.canonical(values))
        business = json.loads(values["biz_content"])
        seen.append(business["out_trade_no"])
        assert business["out_trade_no"].startswith("itp-credential-probe-")
        if len(seen) == 1:
            payload = {"code": "40004", "msg": "Business Failed",
                       "sub_code": "ACQ.TRADE_NOT_EXIST", "sub_msg": "交易不存在"}
        else:  # A probe must never come back as a real payment.
            payload = {"code": "10000", "out_trade_no": business["out_trade_no"],
                       "trade_no": "official-tx", "trade_status": "TRADE_SUCCESS",
                       "total_amount": "1.00"}
        raw = json.dumps(payload, ensure_ascii=False)
        body = ('{"alipay_trade_query_response":' + raw + ',"sign":'
                + json.dumps(rsa_sign(keys[1], raw.encode())) + "}")
        return httpx.Response(200, content=body.encode())

    provider = AlipayProvider(
        settings_for(tmp_path, keys), transport=httpx.MockTransport(transport)
    )
    provider.verify_credentials()
    with pytest.raises(PaymentError):
        provider.verify_credentials()
    assert len(seen) == 2 and seen[0] != seen[1]

    unsigned = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"alipay_trade_query_response": {}})
    )
    with pytest.raises(PaymentError):
        AlipayProvider(settings_for(tmp_path, keys), transport=unsigned).verify_credentials()


def test_wechat_credential_probe_accepts_signed_404_and_rejects_rejections(tmp_path, keys):
    def transport(request):
        assert request.method == "GET" and "out-trade-no/itp-credential-probe-" in str(request.url)
        assert "mchid=test-merchant" in str(request.url)
        raw = json.dumps({"code": "ORDER_NOT_EXIST", "message": "订单不存在"}).encode()
        return httpx.Response(404, content=raw, headers=wechat_headers(raw, keys[1]))

    provider = WechatProvider(
        settings_for(tmp_path, keys), transport=httpx.MockTransport(transport)
    )
    provider.verify_credentials()

    def rejected(request):
        # WeChat answers a rejected signature with 401, still signed by the platform.
        raw = json.dumps({"code": "SIGN_ERROR", "message": "签名错误"}).encode()
        return httpx.Response(401, content=raw, headers=wechat_headers(raw, keys[1]))

    with pytest.raises(PaymentError):
        WechatProvider(
            settings_for(tmp_path, keys), transport=httpx.MockTransport(rejected)
        ).verify_credentials()


def test_wechat_credential_probe_rejects_an_unsigned_answer(tmp_path, keys):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"code": "ORDER_NOT_EXIST"})
    )
    provider = WechatProvider(settings_for(tmp_path, keys), transport=transport)
    with pytest.raises(PaymentError):
        provider.verify_credentials()
