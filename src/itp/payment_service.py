"""Durable unified orders and atomic fulfillment of verified provider events."""

import base64
import hashlib
import io
import json
import logging
import sqlite3
import threading
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

import qrcode

from itp.commerce import CommerceError, IdempotencyConflict, cents
from itp.payments import (
    MANUAL_CHANNELS,
    MANUAL_QR_FIELDS,
    AlipayProvider,
    ManualQrProvider,
    MockProvider,
    PaymentError,
    VerifiedPayment,
    WechatProvider,
    manual_qr_dir,
)

logger = logging.getLogger(__name__)

# A collection code is scanned whenever the payer gets to it, so a manual
# order stays payable far longer than the 30 minutes an online channel allows.
MANUAL_ORDER_TTL = 24 * 3600
# States in which a manual order is still waiting for an operator decision.
MANUAL_OPEN_STATES = ("created", "pending", "uncertain")


class PaymentService:
    def __init__(self, accounts, settings, *, transport=None):
        self.accounts, self.commerce = accounts, accounts.commerce
        self.transport = transport
        self.providers = {}
        self.unavailable = {}
        self._locks = [threading.Lock() for _ in range(64)]
        self.reload(settings)
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

    def reload(self, settings):
        """Rebuild the channel registry from new credentials.

        This is the in-process equivalent of restarting the payment service: the
        operator console calls it right after writing new credentials, and it
        touches no order, transaction or wallet row.
        """
        self.settings = settings
        self.notify_origin = settings.payment_notify_origin or settings.public_origin
        self.manual_dir = manual_qr_dir(settings)
        self.providers = {}
        self.unavailable = {}
        for name, factory, configured in (
            ("alipay", AlipayProvider, settings.alipay_app_id),
            ("wechat", WechatProvider, settings.wechat_mch_id),
        ):
            if not configured:
                self.unavailable[name] = "尚未填写商户参数"
                continue
            if not self.notify_origin:
                self.unavailable[name] = "尚未配置支付回调公网地址（ITP_PUBLIC_ORIGIN）"
                continue
            try:
                self.providers[name] = factory(settings, transport=self.transport)
            except PaymentError:
                self.unavailable[name] = "支付凭据未通过服务端校验"
                logger.warning("Payment provider %s configuration is invalid", name)
        # Manual collection needs no gateway, no callback origin and no
        # credentials — only the operator's own uploaded picture.
        for channel, label in MANUAL_CHANNELS.items():
            if not settings.payment_manual_enabled:
                self.unavailable[channel] = "平台未启用人工收款"
                continue
            try:
                self.providers[channel] = ManualQrProvider(settings, channel, root=self.manual_dir)
            except PaymentError as exc:
                self.unavailable[channel] = str(exc)
        if settings.payment_mock_enabled:
            self.providers["mock"] = MockProvider(settings, self.accounts)
        return self.status()

    def status(self):
        """Per-channel readiness and callback URLs for the operator console."""
        channels = [
            {
                "id": name,
                "ready": name in self.providers,
                "reason": "" if name in self.providers else self.unavailable.get(
                    name, "平台未启用该支付方式"
                ),
            }
            for name in ("alipay", "wechat")
        ]
        if self.settings.payment_manual_enabled:
            # Listed only once the operator switched manual collection on;
            # ``manual_document`` always reports the codes either way.
            for name, label in MANUAL_CHANNELS.items():
                ready = name in self.providers
                channels.append({
                    "id": name,
                    "ready": ready,
                    "reason": "" if ready else self.unavailable.get(
                        name, "平台未启用该支付方式"
                    ),
                    "label": f"{label}收款码（人工确认）",
                })
        if "mock" in self.providers:
            channels.append({"id": "mock", "ready": True, "reason": "仅开发测试环境可用"})
        return {
            "notify_origin": self.notify_origin,
            "callbacks": {
                name: f"{self.notify_origin}/api/payments/callbacks/{name}"
                if self.notify_origin
                else ""
                for name in ("alipay", "wechat")
            },
            "channels": channels,
        }

    def manual_document(self):
        """What the operator console shows for the manual collection codes."""
        channels = {}
        for name, label in MANUAL_CHANNELS.items():
            key = getattr(self.settings, MANUAL_QR_FIELDS[name]) or ""
            path = self.manual_dir / key if key else None
            exists = bool(path and path.is_file())
            channels[name] = {
                "label": label,
                "qr_set": exists,
                "updated": int(path.stat().st_mtime) if exists else None,
                # The key is what a rotation replaces; it is not a secret.
                "qr_key": key if exists else "",
                "ready": name in self.providers,
                "reason": "" if name in self.providers
                else self.unavailable.get(name, "平台未启用该支付方式"),
            }
        return {"enabled": bool(self.settings.payment_manual_enabled), "channels": channels}

    def probe(self, name):
        """Ask the channel itself whether the stored credentials really work."""
        if name == "mock":
            return {"channel": name, "ok": "mock" in self.providers, "message": "模拟支付无需校验"}
        if name in MANUAL_CHANNELS:
            ready = name in self.providers
            return {
                "channel": name,
                "ok": ready,
                "message": "收款码已就绪，等待人工确认到账"
                if ready
                else self.unavailable.get(name, "平台未启用该支付方式"),
            }
        provider = self.providers.get(name)
        if provider is None:
            return {
                "channel": name,
                "ok": False,
                "message": self.unavailable.get(name, "平台尚未启用该支付方式"),
            }
        try:
            provider.verify_credentials()
        except PaymentError as exc:
            return {"channel": name, "ok": False, "message": f"渠道校验未通过：{exc}"}
        except Exception as exc:  # noqa: BLE001 - never surface internals to the console
            logger.warning(
                "Payment credential probe failed for %s: %s", name, type(exc).__name__
            )
            return {
                "channel": name,
                "ok": False,
                "message": "渠道校验未完成，请检查服务器网络与该商户的接口权限",
            }
        return {"channel": name, "ok": True, "message": "凭据已通过官方接口校验"}

    def probe_all(self):
        names = ["alipay", "wechat"]
        if self.settings.payment_manual_enabled:
            names.extend(MANUAL_CHANNELS)
        return [self.probe(name) for name in names]

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
        ] + [
            # Offered to payers only once its picture is uploaded and the
            # operator has switched manual collection on.
            {"id": name, "name": f"{label}收款码（人工确认）", "ready": True}
            for name, label in MANUAL_CHANNELS.items()
            if name in self.providers
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

    def list_all(self, *, limit=50, offset=0):
        """Admin console view: every order with its account name, newest first."""
        with self.accounts.connect() as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) FROM payment_orders").fetchone()[0]
            rows = conn.execute(
                "SELECT o.*, m.name AS account_name FROM payment_orders o "
                "LEFT JOIN merchants m ON m.id = o.user_id "
                "ORDER BY o.created DESC, o.rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return total, [
            self.public(self._order(row))
            | {"user_id": row["user_id"], "account_name": row["account_name"]}
            for row in rows
        ]

    def order_counts(self):
        """Admin console aggregate: orders per state, paid amount in integer cents."""
        with self.accounts.connect() as conn:
            counts = {
                state: {"count": count, "amount_cents": amount}
                for state, count, amount in conn.execute(
                    "SELECT state, COUNT(*), COALESCE(SUM(amount_cents), 0) "
                    "FROM payment_orders GROUP BY state"
                )
            }
        return {
            state: counts.get(state, {"count": 0, "amount_cents": 0})
            for state in ("created", "submitting", "pending", "paid", "uncertain", "rejected")
        }

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
                description = "ClothiNation 钱包充值"
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
            ttl = MANUAL_ORDER_TTL if provider_name in MANUAL_CHANNELS else 1800
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
                    now + ttl,
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
                if "qr_image" in intent:
                    # A manual collection code is a picture the operator
                    # uploaded; the payer is shown that same picture.
                    image = intent["qr_image"]
                    if (
                        not isinstance(image, str)
                        or not image.startswith("/api/payments/manual/qr/")
                        or len(image) > 2048
                    ):
                        raise PaymentError("收款码响应无效")
                elif "qr_code" in intent:
                    if len(intent["qr_code"].encode()) > 2048:
                        raise PaymentError("支付二维码响应过大")
                    stream = io.BytesIO()
                    qrcode.make(intent["qr_code"]).save(stream, format="PNG")
                    intent["qr_image"] = (
                        "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
                    )
                elif not intent.get("mock"):
                    # The local test provider deliberately has no scannable entry.
                    raise PaymentError("支付渠道未返回可扫描的支付入口")
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

    @staticmethod
    def manual_transaction_id(order_id: str) -> str:
        """One stable id per order, so a repeated confirmation is the same credit.

        Derived from the order instead of drawn at random: a second click (or a
        second operator) reproduces the same transaction row, which is what
        makes the fulfillment below idempotent rather than merely guarded.
        """
        return "manual_" + str(uuid5(NAMESPACE_URL, "itp-manual-payment:" + order_id))

    def confirm_manual(self, order_id: str):
        """Credit one manually collected order; only an operator may call this."""
        order = self.get(order_id)
        if not order:
            raise KeyError(order_id)
        if order["provider"] not in MANUAL_CHANNELS:
            raise PaymentError("该订单不是人工收款订单")
        if order["state"] == "rejected":
            raise PaymentError("该订单已被拒绝，不能再确认到账")
        if order["state"] == "paid":
            # Already credited once; confirming again changes nothing.
            return self.public(order)
        # Reuse the audited fulfillment path: it re-checks provider, amount,
        # currency and owner, writes payment_transactions, credits the wallet
        # or grants the membership, and only then marks the order paid — all
        # inside one transaction, and idempotent on the transaction id below.
        return self.fulfill(VerifiedPayment(
            provider=order["provider"],
            order_id=order["id"],
            transaction_id=self.manual_transaction_id(order["id"]),
            amount_cents=order["amount_cents"],
            currency=order["currency"],
            app_id=order["app_id"],
            merchant_id=order["merchant_id"],
        ))

    def reject_manual(self, order_id: str):
        """Refuse one unconfirmed order; nothing is credited and the code goes."""
        with self._lock(order_id):
            with self.accounts.connect() as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                order = self._order(
                    conn.execute(
                        "SELECT * FROM payment_orders WHERE id=?", (order_id,)
                    ).fetchone()
                )
                if not order:
                    raise KeyError(order_id)
                if order["provider"] not in MANUAL_CHANNELS:
                    raise PaymentError("该订单不是人工收款订单")
                if order["state"] == "paid":
                    raise PaymentError("已确认到账的订单不能拒绝")
                # The stored code is dropped: a refused order must not invite
                # the payer to transfer money after the decision.
                conn.execute(
                    "UPDATE payment_orders SET state='rejected',checkout=NULL,updated=? WHERE id=?",
                    (int(time.time()), order_id),
                )
        return self.public(self.get(order_id))

    def manual_orders(self, *, limit=50, offset=0):
        """Unconfirmed manual orders, newest first, for the operator console."""
        placeholders = ",".join("?" for _ in MANUAL_CHANNELS)
        with self.accounts.connect() as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                f"SELECT COUNT(*) FROM payment_orders WHERE provider IN ({placeholders}) "
                f"AND state IN ({','.join('?' for _ in MANUAL_OPEN_STATES)})",
                (*MANUAL_CHANNELS, *MANUAL_OPEN_STATES),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT o.*, m.name AS account_name FROM payment_orders o "
                "LEFT JOIN merchants m ON m.id = o.user_id "
                f"WHERE o.provider IN ({placeholders}) "
                f"AND o.state IN ({','.join('?' for _ in MANUAL_OPEN_STATES)}) "
                "ORDER BY o.created DESC, o.rowid DESC LIMIT ? OFFSET ?",
                (*MANUAL_CHANNELS, *MANUAL_OPEN_STATES, limit, offset),
            ).fetchall()
        return total, [
            self.public(self._order(row)) | {
                "user_id": row["user_id"],
                "account_name": row["account_name"],
            }
            for row in rows
        ]

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
