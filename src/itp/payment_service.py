"""Durable unified orders and atomic fulfillment of verified provider events."""

import base64
import hashlib
import io
import json
import logging
import sqlite3
import threading
import time
from uuid import uuid4

import qrcode

from itp.commerce import CommerceError, IdempotencyConflict, cents
from itp.payments import AlipayProvider, MockProvider, PaymentError, VerifiedPayment, WechatProvider

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, accounts, settings):
        self.accounts, self.commerce, self.settings = accounts, accounts.commerce, settings
        self.providers = {}
        self.unavailable = {}
        self.notify_origin = settings.payment_notify_origin or settings.public_origin
        self._locks = [threading.Lock() for _ in range(64)]
        for name, factory, configured in (
            ("alipay", AlipayProvider, settings.alipay_app_id),
            ("wechat", WechatProvider, settings.wechat_mch_id),
        ):
            if not configured or not self.notify_origin:
                self.unavailable[name] = "平台未启用该支付方式"
                continue
            try:
                self.providers[name] = factory(settings)
            except PaymentError:
                self.unavailable[name] = "平台支付配置未就绪"
                logger.warning("Payment provider %s configuration is invalid", name)
        if settings.payment_mock_enabled:
            self.providers["mock"] = MockProvider(settings, accounts)
        with accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS payment_orders (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                    provider TEXT NOT NULL, amount_cents INTEGER NOT NULL
                        CHECK(typeof(amount_cents)='integer' AND amount_cents>=0),
                    currency TEXT NOT NULL, state TEXT NOT NULL, created INTEGER NOT NULL,
                    updated INTEGER NOT NULL, expires INTEGER NOT NULL, paid_at INTEGER,
                    idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL, plan TEXT,
                    app_id TEXT NOT NULL, merchant_id TEXT NOT NULL, description TEXT NOT NULL,
                    checkout TEXT, UNIQUE(user_id,idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS orders_user ON payment_orders(user_id,created);
                CREATE TABLE IF NOT EXISTS payment_transactions (
                    provider TEXT NOT NULL, transaction_id TEXT NOT NULL,
                    order_id TEXT UNIQUE NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer'),
                    verified_at INTEGER NOT NULL, PRIMARY KEY(provider,transaction_id)
                );
            """)

    def methods(self):
        return [
            {
                "id": name,
                "name": {
                    "alipay": "支付宝",
                    "wechat": "微信支付",
                    "mock": "模拟支付（仅开发测试）",
                }[name],
                "ready": name in self.providers,
            }
            for name in ("alipay", "wechat", *(["mock"] if "mock" in self.providers else []))
        ]

    def provider(self, name):
        if name not in self.providers:
            raise PaymentError("平台尚未启用所选支付方式")
        return self.providers[name]

    @staticmethod
    def _order(row):
        if row is None:
            return None
        document = dict(row)
        document["plan"] = json.loads(document["plan"]) if document["plan"] else None
        document["checkout"] = json.loads(document["checkout"]) if document["checkout"] else None
        return document

    def get(self, order_id, user_id=None):
        with self.accounts.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM payment_orders WHERE id=?", (order_id,)).fetchone()
        result = self._order(row)
        return result if result and (user_id is None or result["user_id"] == user_id) else None

    def list(self, user_id, *, limit=50, offset=0):
        with self.accounts.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM payment_orders WHERE user_id=? "
                "ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        return [self.public(self._order(row)) for row in rows]

    @staticmethod
    def public(order):
        return {
            key: order[key]
            for key in (
                "id",
                "kind",
                "provider",
                "amount_cents",
                "currency",
                "state",
                "created",
                "updated",
                "expires",
                "paid_at",
                "description",
                "checkout",
            )
        } | {"plan_id": order["plan"]["id"] if order["plan"] else None}

    def create(self, user_id, body, idempotency_key):
        order = self.reserve(user_id, body, idempotency_key)
        if order["state"] == "created":
            return self.checkout(order)
        return self.public(order)

    def reserve(self, user_id, body, idempotency_key):
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
            raise CommerceError("支付订单需要 8 至 128 字符的幂等键")
        kind = body["kind"]
        if kind not in {"recharge", "membership"}:
            raise CommerceError("订单类型无效")
        fingerprint = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.accounts.connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT * FROM payment_orders WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise IdempotencyConflict("同一幂等键不能用于不同的支付订单")
                return self._order(old)
            plan = None
            if kind == "recharge":
                if body.get("plan_id") is not None:
                    raise CommerceError("充值订单不能包含会员套餐")
                amount = cents(body.get("amount_cents"), positive=True)
                # Provider purchase products have practical lower bounds than
                # the wallet's lifetime amount bound; reject before a paid call.
                if amount > 10000000:
                    raise CommerceError("单次充值不能超过 100000 元")
                description = "ITP 钱包充值"
            else:
                if body.get("amount_cents") is not None:
                    raise CommerceError("会员价格只能由平台确定")
                plan = self.commerce._plan(
                    conn.execute(
                        "SELECT * FROM commerce_plans WHERE id=?", (body.get("plan_id"),)
                    ).fetchone()
                )
                user = conn.execute("SELECT role FROM merchants WHERE id=?", (user_id,)).fetchone()
                if not plan or not plan["active"] or not plan["purchasable"]:
                    raise CommerceError("该会员套餐不可购买")
                if not user or (plan["audience"] == "merchant" and user[0] != "merchant"):
                    raise CommerceError("需要商家身份购买该会员")
                amount, description = cents(plan["price_cents"]), plan["name"]
            provider_name = body.get("provider") if amount else "free"
            provider = self.provider(provider_name) if amount else None
            app_id, merchant_id = (
                (provider.app_id, provider.merchant_id)
                if provider
                else ("platform-free", "platform-free")
            )
            now, order_id = int(time.time()), uuid4().hex
            conn.execute(
                "INSERT INTO payment_orders VALUES (?,?,?,?,?,?,?, ?,?,?,NULL, ?,?,?,?,?,?,NULL)",
                (
                    order_id,
                    user_id,
                    kind,
                    provider_name,
                    amount,
                    "CNY",
                    "created",
                    now,
                    now,
                    now + 1800,
                    idempotency_key,
                    fingerprint,
                    json.dumps(plan) if plan else None,
                    app_id,
                    merchant_id,
                    description,
                ),
            )
            if not amount:
                self.commerce.grant_subscription(conn, user_id, plan, order_id, now=now)
                conn.execute(
                    "UPDATE payment_orders SET state='paid',paid_at=? WHERE id=?", (now, order_id)
                )
        return self.get(order_id)

    def _lock(self, order_id):
        return self._locks[
            int(hashlib.sha256(order_id.encode()).hexdigest()[:8], 16) % len(self._locks)
        ]

    def checkout(self, order):
        """Submit at most once; uncertain submissions must use status query."""
        with self._lock(order["id"]):
            with self.accounts.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                changed = conn.execute(
                    "UPDATE payment_orders SET state='submitting',updated=? "
                    "WHERE id=? AND state='created'",
                    (int(time.time()), order["id"]),
                ).rowcount
            if not changed:
                return self.public(self.get(order["id"]))
            try:
                intent = self.provider(order["provider"]).create(
                    order, self.notify_origin + "/api/payments/callbacks/" + order["provider"]
                )
                if "qr_code" in intent:
                    if len(intent["qr_code"].encode()) > 2048:
                        raise PaymentError("支付二维码响应过大")
                    stream = io.BytesIO()
                    qrcode.make(intent["qr_code"]).save(stream, format="PNG")
                    intent["qr_image"] = (
                        "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
                    )
                with self.accounts.connect() as conn:
                    # A valid asynchronous callback may have already set paid.
                    conn.execute(
                        "UPDATE payment_orders SET checkout=?,"
                        "state=CASE WHEN state='paid' THEN state ELSE 'pending' END,"
                        "updated=? WHERE id=?",
                        (json.dumps(intent), int(time.time()), order["id"]),
                    )
            except (PaymentError, qrcode.exceptions.DataOverflowError):
                with self.accounts.connect() as conn:
                    conn.execute(
                        "UPDATE payment_orders SET state='uncertain',updated=? "
                        "WHERE id=? AND state='submitting'",
                        (int(time.time()), order["id"]),
                    )
                logger.warning("Payment checkout %s is unconfirmed", order["id"])
            return self.public(self.get(order["id"]))

    def fulfill(self, event: VerifiedPayment):
        if not isinstance(event, VerifiedPayment):
            raise PaymentError("支付结果必须先通过服务端验证")
        cents(event.amount_cents, positive=True)
        if (
            not isinstance(event.transaction_id, str)
            or not 1 <= len(event.transaction_id) <= 128
            or not isinstance(event.order_id, str)
            or not 1 <= len(event.order_id) <= 64
        ):
            raise PaymentError("支付业务编号无效")
        with self.accounts.connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            order = self._order(
                conn.execute(
                    "SELECT * FROM payment_orders WHERE id=?", (event.order_id,)
                ).fetchone()
            )
            if not order:
                raise PaymentError("支付订单不存在")
            expected = (
                order["provider"],
                order["amount_cents"],
                order["currency"],
                order["app_id"],
                order["merchant_id"],
            )
            actual = (
                event.provider,
                event.amount_cents,
                event.currency,
                event.app_id,
                event.merchant_id,
            )
            if actual != expected:
                raise PaymentError("支付金额、币种或商户信息不匹配")
            transaction = conn.execute(
                "SELECT order_id FROM payment_transactions WHERE provider=? AND transaction_id=?",
                (event.provider, event.transaction_id),
            ).fetchone()
            if transaction:
                if transaction["order_id"] != order["id"]:
                    raise PaymentError("支付交易号已用于另一个订单")
                return self.public(order)
            if order["state"] == "paid":
                raise PaymentError("已完成订单收到不同的支付交易号")
            now = int(time.time())
            try:
                conn.execute(
                    "INSERT INTO payment_transactions VALUES (?,?,?,?,?)",
                    (event.provider, event.transaction_id, order["id"], event.amount_cents, now),
                )
            except sqlite3.IntegrityError as exc:
                raise PaymentError("支付交易重复或不匹配") from exc
            if order["kind"] == "recharge":
                self.commerce.credit_verified_order(
                    conn, order["user_id"], order["amount_cents"], order["id"]
                )
            else:
                self.commerce.grant_subscription(
                    conn, order["user_id"], order["plan"], order["id"], now=now
                )
            conn.execute(
                "UPDATE payment_orders SET state='paid',paid_at=?,updated=? WHERE id=?",
                (now, now, order["id"]),
            )
        logger.info(
            "Verified payment fulfilled: provider=%s order=%s", event.provider, event.order_id
        )
        return self.public(self.get(event.order_id))

    def query(self, order_id, user_id):
        order = self.get(order_id, user_id)
        if not order:
            raise KeyError(order_id)
        if order["state"] == "paid":
            return self.public(order)
        with self._lock(order_id):
            event = self.provider(order["provider"]).query(order)
            if event:
                if event.order_id != order_id:
                    raise PaymentError("支付查询返回了另一个订单")
                return self.fulfill(event)
            return self.public(self.get(order_id))

    def simulate(self, order_id, user_id):
        order = self.get(order_id, user_id)
        provider = self.providers.get("mock")
        if not order:
            raise KeyError(order_id)
        if not isinstance(provider, MockProvider) or order["provider"] != "mock":
            raise PaymentError("该订单不能使用模拟支付")
        provider.simulate(order)
        return self.query(order_id, user_id)
