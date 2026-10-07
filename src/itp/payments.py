"""Official Alipay RSA2 / WeChat Native v3 and restricted local test provider.

No provider trusts a browser payment-success flag. Verified events are produced
only after signature checks, merchant/application checks and exact amount parsing.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, quote
from zoneinfo import ZoneInfo

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from itp.commerce import cents
from itp.manual_qr import ManualQrStore

# Manual collection: the two personal codes an operator can upload.  The ids
# are the provider names stored on the order and on the credit transaction.
MANUAL_CHANNELS = {"manual_wechat": "微信", "manual_alipay": "支付宝"}
MANUAL_QR_FIELDS = {
    "manual_wechat": "payment_manual_wechat_qr",
    "manual_alipay": "payment_manual_alipay_qr",
}


def manual_qr_dir(settings):
    """Where uploaded collection codes live; inside the (ignored) data dir."""
    return Path(settings.data_dir) / "payment" / "manual"


class PaymentError(ValueError):
    """Safe public message; never include provider bodies, secrets or payer data."""


@dataclass(frozen=True)
class VerifiedPayment:
    provider: str
    order_id: str
    transaction_id: str
    amount_cents: int
    currency: str
    app_id: str
    merchant_id: str


def json_document(raw):
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise PaymentError("支付数据存在重复字段")
            data[key] = value
        return data

    try:
        document = json.loads(raw, parse_float=Decimal, object_pairs_hook=unique)
        if not isinstance(document, dict):
            raise PaymentError("支付数据必须为对象")
        return document
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PaymentError("支付数据格式无效") from exc


def yuan(amount_cents):
    cents(amount_cents, positive=True)
    return f"{amount_cents // 100}.{amount_cents % 100:02d}"


def amount_from_yuan(value):
    if type(value) not in {str, int, Decimal}:
        raise PaymentError("支付金额无效")
    try:
        value = Decimal(value) * 100
        if not value.is_finite() or value != value.to_integral_value():
            raise PaymentError("支付金额必须精确到分")
        return cents(int(value), positive=True)
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise PaymentError("支付金额无效") from exc


def _pem(secret):
    return secret.get_secret_value().replace("\\n", "\n").encode()


def private_key(secret):
    try:
        key = serialization.load_pem_private_key(_pem(secret), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("RSA key required")
        return key
    except (ValueError, TypeError) as exc:
        raise PaymentError("平台支付签名配置无效") from exc


def public_key(secret):
    try:
        data = _pem(secret)
        if data.startswith(b"-----BEGIN CERTIFICATE-----"):
            certificate = x509.load_pem_x509_certificate(data)
            if (
                not certificate.not_valid_before_utc.timestamp()
                <= time.time()
                <= certificate.not_valid_after_utc.timestamp()
            ):
                raise ValueError("Expired certificate")
            key = certificate.public_key()
        else:
            key = serialization.load_pem_public_key(data)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError("RSA key required")
        return key
    except (ValueError, TypeError) as exc:
        raise PaymentError("平台支付验签配置无效") from exc


def rsa_sign(key, raw):
    return base64.b64encode(key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()


def rsa_verify(key, signature, raw):
    try:
        key.verify(
            base64.b64decode(signature, validate=True), raw, padding.PKCS1v15(), hashes.SHA256()
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise PaymentError("支付签名校验失败") from exc


class PaymentProvider(ABC):
    name: str
    app_id: str
    merchant_id: str

    @abstractmethod
    def create(self, order: dict, notify_url: str) -> dict: ...

    @abstractmethod
    def query(self, order: dict) -> VerifiedPayment | None: ...

    @abstractmethod
    def callback(self, raw: bytes, headers) -> VerifiedPayment | None: ...


class AlipayProvider(PaymentProvider):
    name = "alipay"
    gateway = "https://openapi.alipay.com/gateway.do"

    def __init__(self, settings, *, transport=None):
        self.app_id, self.merchant_id = settings.alipay_app_id, settings.alipay_seller_id
        self.private, self.public = (
            private_key(settings.alipay_private_key),
            public_key(settings.alipay_public_key),
        )
        self.transport = transport
        if not self.app_id or not self.merchant_id:
            raise PaymentError("支付宝商户配置不完整")

    @staticmethod
    def canonical(parameters, *, callback=False):
        excluded = {"sign", "sign_type"} if callback else {"sign"}
        return "&".join(
            f"{key}={parameters[key]}"
            for key in sorted(parameters)
            if key not in excluded and parameters[key] not in ("", None)
        ).encode("utf-8")

    @staticmethod
    def signed_response(raw: str, response_key: str):
        """Extract the original inner JSON bytes; re-serializing breaks RSA2."""
        document = json_document(raw)
        decoder = json.JSONDecoder(parse_float=Decimal)
        index = raw.index("{") + 1
        payload = None
        while True:
            while index < len(raw) and raw[index].isspace():
                index += 1
            if raw[index] == "}":
                break
            key, index = decoder.raw_decode(raw, index)
            while raw[index].isspace():
                index += 1
            if raw[index] != ":":
                raise PaymentError("支付宝响应格式无效")
            index += 1
            while raw[index].isspace():
                index += 1
            start = index
            _, index = decoder.raw_decode(raw, index)
            if key == response_key:
                payload = raw[start:index].encode("utf-8")
            while raw[index].isspace():
                index += 1
            if raw[index] == "}":
                break
            if raw[index] != ",":
                raise PaymentError("支付宝响应格式无效")
            index += 1
        if (
            payload is None
            or not isinstance(document.get(response_key), dict)
            or not document.get("sign")
        ):
            raise PaymentError("支付宝响应缺少签名")
        return document[response_key], document["sign"], payload

    def rpc(self, method, business, *, notify_url=None):
        params = {
            "app_id": self.app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(business, ensure_ascii=False, separators=(",", ":")),
        }
        if notify_url:
            params["notify_url"] = notify_url
        params["sign"] = rsa_sign(self.private, self.canonical(params))
        try:
            with httpx.Client(
                timeout=20, trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                response = client.post(self.gateway, data=params)
            response.raise_for_status()
            if len(response.content) > 128 * 1024:
                raise PaymentError("支付响应过大")
            data, signature, signed = self.signed_response(
                response.text, method.replace(".", "_") + "_response"
            )
            rsa_verify(self.public, signature, signed)
            if data.get("code") != "10000":
                if method == "alipay.trade.query" and data.get("sub_code") == "ACQ.TRADE_NOT_EXIST":
                    return None
                raise PaymentError("支付宝暂未接受请求，请稍后查询订单")
            return data
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            if isinstance(exc, PaymentError):
                raise
            raise PaymentError("支付宝请求未确认，请稍后查询订单") from exc

    def create(self, order, notify_url):
        data = self.rpc(
            "alipay.trade.precreate",
            {
                "out_trade_no": order["id"],
                "seller_id": self.merchant_id,
                "total_amount": yuan(order["amount_cents"]),
                "subject": order["description"],
                "timeout_express": "30m",
            },
            notify_url=notify_url,
        )
        if data.get("out_trade_no") != order["id"] or not isinstance(data.get("qr_code"), str):
            raise PaymentError("支付宝订单响应不匹配")
        return {"qr_code": data["qr_code"]}

    def _paid(self, data, *, callback):
        if callback and (
            data.get("app_id") != self.app_id or data.get("seller_id") != self.merchant_id
        ):
            raise PaymentError("支付商户或应用不匹配")
        if data.get("trade_status") not in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
            return None
        if not data.get("out_trade_no") or not data.get("trade_no"):
            raise PaymentError("支付流水缺失")
        # Query is authenticated using this app's private key over official TLS.
        # Official trade.query does not return app_id/seller_id in its response.
        return VerifiedPayment(
            self.name,
            data["out_trade_no"],
            data["trade_no"],
            amount_from_yuan(data["total_amount"]),
            "CNY",
            self.app_id,
            self.merchant_id,
        )

    def query(self, order):
        data = self.rpc("alipay.trade.query", {"out_trade_no": order["id"]})
        return self._paid(data, callback=False) if data else None

    def verify_credentials(self):
        """Prove both keys and the gateway round trip with a signed probe query.

        A random order id must come back as ACQ.TRADE_NOT_EXIST: that answer is
        signed by Alipay, so a successful call means the platform accepted this
        app's signature and the stored Alipay public key verified the reply.
        """
        probe = "itp-credential-probe-" + secrets.token_hex(8)
        if self.rpc("alipay.trade.query", {"out_trade_no": probe}) is not None:
            raise PaymentError("支付宝返回了意外的探测结果")

    def callback(self, raw, headers):
        try:
            pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
            data = dict(pairs)
            if len(data) != len(pairs) or data.get("sign_type") != "RSA2":
                raise PaymentError("支付宝通知格式无效")
            rsa_verify(self.public, data.get("sign", ""), self.canonical(data, callback=True))
            return self._paid(data, callback=True)
        except (ValueError, KeyError, UnicodeError) as exc:
            if isinstance(exc, PaymentError):
                raise
            raise PaymentError("支付宝通知无效") from exc


class WechatProvider(PaymentProvider):
    name = "wechat"
    base_url = "https://api.mch.weixin.qq.com"

    def __init__(self, settings, *, transport=None):
        self.app_id, self.merchant_id = settings.wechat_app_id, settings.wechat_mch_id
        self.serial = settings.wechat_merchant_serial
        self.private = private_key(settings.wechat_private_key)
        self.keys = {
            identifier: public_key(key) for identifier, key in settings.wechat_platform_keys.items()
        }
        self.api_key = settings.wechat_api_v3_key.get_secret_value().encode()
        self.transport = transport
        if (
            not self.app_id
            or not self.merchant_id
            or not self.serial
            or not self.keys
            or len(self.api_key) != 32
        ):
            raise PaymentError("微信支付商户配置不完整")

    def verify(self, raw, headers):
        headers = {key.lower(): value for key, value in headers.items()}
        timestamp, nonce = (
            headers.get("wechatpay-timestamp", ""),
            headers.get("wechatpay-nonce", ""),
        )
        key = self.keys.get(headers.get("wechatpay-serial", ""))
        if (
            not timestamp.isdigit()
            or abs(time.time() - int(timestamp)) > 300
            or not nonce
            or len(nonce) > 128
            or not key
        ):
            raise PaymentError("微信支付通知时间或证书无效")
        signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw + b"\n"
        rsa_verify(key, headers.get("wechatpay-signature", ""), signed)

    def request(self, method, target, body=None, tolerate=()):
        raw = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            if body is not None
            else b""
        )
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        signed = f"{method}\n{target}\n{timestamp}\n{nonce}\n".encode() + raw + b"\n"
        signature = rsa_sign(self.private, signed)
        authorization = (
            f'WECHATPAY2-SHA256-RSA2048 mchid="{self.merchant_id}",nonce_str="{nonce}",'
            f'timestamp="{timestamp}",serial_no="{self.serial}",signature="{signature}"'
        )
        try:
            with httpx.Client(
                timeout=20, follow_redirects=False, trust_env=False, transport=self.transport
            ) as client:
                response = client.request(
                    method,
                    self.base_url + target,
                    content=raw,
                    headers={
                        "Authorization": authorization,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
            if len(response.content) > 128 * 1024:
                raise PaymentError("支付响应过大")
            self.verify(response.content, response.headers)
            if response.status_code not in tolerate:
                response.raise_for_status()
            return json_document(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, PaymentError):
                raise
            raise PaymentError("微信支付请求未确认，请稍后查询订单") from exc

    def create(self, order, notify_url):
        data = self.request(
            "POST",
            "/v3/pay/transactions/native",
            {
                "appid": self.app_id,
                "mchid": self.merchant_id,
                "description": order["description"],
                "out_trade_no": order["id"],
                "notify_url": notify_url,
                "time_expire": datetime.fromtimestamp(
                    order.get("expires", int(time.time()) + 1800), ZoneInfo("Asia/Shanghai")
                ).isoformat(),
                "amount": {"total": cents(order["amount_cents"], positive=True), "currency": "CNY"},
            },
        )
        if not isinstance(data.get("code_url"), str) or not data["code_url"].startswith(
            "weixin://"
        ):
            raise PaymentError("微信支付二维码响应无效")
        return {"qr_code": data["code_url"]}

    def _paid(self, data):
        if data.get("appid") != self.app_id or data.get("mchid") != self.merchant_id:
            raise PaymentError("支付商户或应用不匹配")
        if data.get("trade_state") != "SUCCESS":
            return None
        amount = data.get("amount", {})
        if not isinstance(amount, dict):
            raise PaymentError("支付金额格式无效")
        if (
            amount.get("currency") != "CNY"
            or not data.get("out_trade_no")
            or not data.get("transaction_id")
        ):
            raise PaymentError("支付流水或币种无效")
        return VerifiedPayment(
            self.name,
            data["out_trade_no"],
            data["transaction_id"],
            cents(amount["total"], positive=True),
            "CNY",
            self.app_id,
            self.merchant_id,
        )

    def query(self, order):
        target = (
            f"/v3/pay/transactions/out-trade-no/{quote(order['id'], safe='')}"
            f"?mchid={quote(self.merchant_id, safe='')}"
        )
        return self._paid(self.request("GET", target))

    def verify_credentials(self):
        """Prove the merchant key, serial and platform key with a signed probe.

        WeChat answers a rejected signature with 401/403 and signs every other
        reply, so a verified ORDER_NOT_EXIST for a random order id shows that
        this merchant certificate was accepted and the stored platform key is
        the one signing the answers.
        """
        probe = "itp-credential-probe-" + secrets.token_hex(8)
        target = (
            f"/v3/pay/transactions/out-trade-no/{quote(probe, safe='')}"
            f"?mchid={quote(self.merchant_id, safe='')}"
        )
        data = self.request("GET", target, tolerate=(404,))
        code = data.get("code")
        if code and code not in {"ORDER_NOT_EXIST", "RESOURCE_NOT_EXISTS"}:
            raise PaymentError("微信支付返回了意外的探测结果")

    def callback(self, raw, headers):
        self.verify(raw, headers)
        try:
            body = json_document(raw)
            if body.get("event_type") != "TRANSACTION.SUCCESS":
                return None
            resource = body["resource"]
            if not isinstance(resource, dict):
                raise PaymentError("微信通知资源格式无效")
            if resource.get("algorithm") != "AEAD_AES_256_GCM":
                raise PaymentError("微信通知加密算法无效")
            decrypted = AESGCM(self.api_key).decrypt(
                resource["nonce"].encode(),
                base64.b64decode(resource["ciphertext"], validate=True),
                resource.get("associated_data", "").encode(),
            )
            return self._paid(json_document(decrypted))
        except (KeyError, ValueError, TypeError, InvalidTag) as exc:
            if isinstance(exc, PaymentError):
                raise
            raise PaymentError("微信支付通知解密失败") from exc


class ManualQrProvider(PaymentProvider):
    """A personal collection code the operator confirms by hand.

    Deliberately passive, and the only provider that cannot verify anything:
    the money moves between two personal accounts, so the platform has no
    signed event and no query to trust.  ``create`` merely points the payer at
    the operator's own picture, ``query`` always reports "still pending", and
    there is no callback to forge — only an administrator, after checking the
    real account statement, may mark the order paid.
    """

    def __init__(self, settings, channel: str, *, root):
        if channel not in MANUAL_CHANNELS:
            raise PaymentError("未知的人工收款渠道")
        field = MANUAL_QR_FIELDS[channel]
        name = getattr(settings, field)
        store = ManualQrStore(root)
        path = store.path(name)
        if not path:
            raise PaymentError(f"尚未上传{MANUAL_CHANNELS[channel]}收款码")
        self.name = channel
        self.qr_name = name
        self.qr_path = path
        # A manual transfer carries no merchant application; the order row
        # still records who owns it so fulfillment can re-check the pair.
        self.app_id = self.merchant_id = "manual-collection"

    def create(self, order, notify_url):
        return {
            "qr_image": f"/api/payments/manual/qr/{self.qr_name}",
            # The frontend shows the collection code, never a payment result.
            "manual": True,
        }

    def query(self, order):
        """Never confirm anything: only an operator can verify a transfer."""
        return None

    def callback(self, raw, headers):
        """Manual collection has no callback; any posted event is meaningless."""
        return None

    def verify_credentials(self):
        """The picture exists (the constructor proved it); nothing else to check."""
        return None


class MockProvider(PaymentProvider):
    """Explicit local development/test only. Durable signed simulated events."""

    name = "mock"
    app_id = "local-development"
    merchant_id = "local-development"

    def __init__(self, settings, accounts):
        if (
            settings.environment not in {"development", "test"}
            or not settings.payment_mock_enabled
            or settings.public_origin
            or len(settings.payment_mock_secret.get_secret_value()) < 32
        ):
            raise PaymentError("生产环境禁止模拟支付")
        self.secret = settings.payment_mock_secret.get_secret_value().encode()
        self.accounts = accounts
        with accounts.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS mock_payment_events "
                "(order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, signature TEXT NOT NULL)"
            )

    def create(self, order, notify_url):
        return {"mock": True}

    def simulate(self, order):
        raw = json.dumps(
            {
                "order_id": order["id"],
                "transaction_id": "mock-" + order["id"],
                "amount_cents": order["amount_cents"],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        signature = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
        with self.accounts.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mock_payment_events VALUES (?,?,?)",
                (order["id"], raw, signature),
            )

    def query(self, order):
        with self.accounts.connect() as conn:
            row = conn.execute(
                "SELECT payload,signature FROM mock_payment_events WHERE order_id=?", (order["id"],)
            ).fetchone()
        if not row:
            return None
        return self.callback(row[0].encode(), {"x-itp-mock-signature": row[1]})

    def callback(self, raw, headers):
        expected = hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("x-itp-mock-signature", "")):
            raise PaymentError("模拟支付签名无效")
        data = json_document(raw)
        return VerifiedPayment(
            self.name,
            data["order_id"],
            data["transaction_id"],
            cents(data["amount_cents"], positive=True),
            "CNY",
            self.app_id,
            self.merchant_id,
        )
