"""Transactional money, configurable plans and calendar-period usage.

All durable state shares the existing account/product database. This module
never accepts or persists customer photos, model paths or body measurements.
Payment verification belongs to PaymentProvider; no browser-facing credit API
is provided here.
"""

import calendar
import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

MAX_CENTS = 10**12


class CommerceError(ValueError):
    pass


class InsufficientFunds(CommerceError):
    pass


class IdempotencyConflict(CommerceError):
    pass


def cents(value: int, *, positive=False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > MAX_CENTS:
        raise CommerceError("金额必须是合法的整数分值")
    return value


def add_months(timestamp: int, months: int) -> int:
    """UTC calendar anniversary; clip month end, never use a 30-day month."""
    date = datetime.fromtimestamp(timestamp, UTC)
    index = date.year * 12 + date.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return int(
        date.replace(
            year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1])
        ).timestamp()
    )


def period_at(anchor: int, months: int, now: int) -> tuple[int, int]:
    if type(months) is not int or not 1 <= months <= 120:
        raise CommerceError("周期必须为 1 至 120 个日历月")
    a = datetime.fromtimestamp(anchor, UTC)
    n = datetime.fromtimestamp(max(now, anchor), UTC)
    index = max(0, ((n.year - a.year) * 12 + n.month - a.month) // months)
    while index and add_months(anchor, index * months) > now:
        index -= 1
    while add_months(anchor, (index + 1) * months) <= now:
        index += 1
    return add_months(anchor, index * months), add_months(anchor, (index + 1) * months)


class CommerceStore:
    def __init__(self, accounts, settings=None):
        self.accounts = accounts
        self.model_price_cents = 1500
        with self.accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS commerce_plans (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, audience TEXT NOT NULL,
                    price_cents INTEGER NOT NULL
                        CHECK(typeof(price_cents)='integer' AND price_cents>=0),
                    period_months INTEGER NOT NULL CHECK(period_months BETWEEN 1 AND 120),
                    entitlements TEXT NOT NULL, active INTEGER NOT NULL,
                    purchasable INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                    starts INTEGER NOT NULL, ends INTEGER NOT NULL, entitlements TEXT NOT NULL,
                    order_id TEXT UNIQUE NOT NULL
                );
                CREATE INDEX IF NOT EXISTS subscriptions_user
                    ON subscriptions(user_id, starts, ends);
                CREATE TABLE IF NOT EXISTS usage_buckets (
                    user_id TEXT NOT NULL, feature TEXT NOT NULL, scope TEXT NOT NULL,
                    starts INTEGER NOT NULL, ends INTEGER NOT NULL,
                    used INTEGER NOT NULL CHECK(typeof(used)='integer' AND used>=0),
                    PRIMARY KEY(user_id, feature, scope, starts)
                );
                CREATE TABLE IF NOT EXISTS wallets (
                    user_id TEXT PRIMARY KEY,
                    balance_cents INTEGER NOT NULL
                        CHECK(typeof(balance_cents)='integer' AND balance_cents>=0)
                );
                CREATE TABLE IF NOT EXISTS wallet_ledger (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    delta_cents INTEGER NOT NULL CHECK(typeof(delta_cents)='integer'),
                    balance_cents INTEGER NOT NULL
                        CHECK(typeof(balance_cents)='integer' AND balance_cents>=0),
                    kind TEXT NOT NULL, reference TEXT NOT NULL, created INTEGER NOT NULL,
                    UNIQUE(user_id, kind, reference)
                );
                CREATE INDEX IF NOT EXISTS ledger_user ON wallet_ledger(user_id, created);
                CREATE TABLE IF NOT EXISTS model_charges (
                    job_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL
                        CHECK(typeof(amount_cents)='integer' AND amount_cents>=0),
                    idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','completed','refunded')),
                    created INTEGER NOT NULL, updated INTEGER NOT NULL,
                    UNIQUE(user_id,idempotency_key)
                );
            """)
            self._default(
                conn,
                "customer_annual",
                "个性化推荐年会员",
                "customer",
                3000,
                12,
                {"personalized_recommendation": True},
                True,
            )
            self._default(
                conn, "merchant_free", "免费商家", "merchant", 0, 1, {"garment_upload": 5}, False
            )
        if settings is not None:
            self.configure(settings)
        # Seed surviving legacy uploads once. Old deletion history was not
        # recorded, so it cannot be reconstructed from the old product count.
        with self.accounts.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS commerce_migrations (version TEXT PRIMARY KEY)"
            )
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM commerce_migrations WHERE version='period_usage_v1'"
            ).fetchone():
                free = self._plan(
                    conn.execute("SELECT * FROM commerce_plans WHERE id='merchant_free'").fetchone()
                )
                for user_id, created in conn.execute(
                    "SELECT id,created FROM merchants WHERE role='merchant'"
                ).fetchall():
                    start, end = period_at(int(created), free["period_months"], int(time.time()))
                    used = conn.execute(
                        "SELECT COUNT(*) FROM garments "
                        "WHERE merchant_id=? AND created>=? AND created<?",
                        (user_id, start, end),
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT OR IGNORE INTO usage_buckets "
                        "VALUES (?, 'garment_upload', 'merchant_free', ?, ?, ?)",
                        (user_id, start, end, used),
                    )
                conn.execute("INSERT INTO commerce_migrations VALUES ('period_usage_v1')")

    def _default(self, conn, plan_id, name, audience, price, months, rights, purchasable):
        conn.execute(
            "INSERT OR IGNORE INTO commerce_plans VALUES (?,?,?,?,?,?,1,?)",
            (plan_id, name, audience, price, months, json.dumps(rights), int(purchasable)),
        )

    def configure(self, settings):
        self.model_price_cents = cents(settings.model_price_cents)
        self.configure_plan(
            "customer_annual",
            name="个性化推荐年会员",
            audience="customer",
            price_cents=settings.customer_membership_price_cents,
            period_months=12,
            entitlements={"personalized_recommendation": True},
            purchasable=True,
        )
        self.configure_plan(
            "merchant_free",
            name="免费商家",
            audience="merchant",
            price_cents=0,
            period_months=settings.merchant_free_period_months,
            entitlements={"garment_upload": settings.merchant_free_upload_limit},
            purchasable=False,
        )
        for plan in settings.commercial_plans:
            if plan.get("id") in {"merchant_free", "customer_annual"}:
                raise CommerceError("商业套餐不得覆盖内置套餐标识")
            self.configure_plan(**plan)

    def configure_plan(
        self,
        id: str,
        *,
        name: str,
        audience: str,
        price_cents: int,
        period_months: int,
        entitlements: dict,
        active=True,
        purchasable=True,
    ):
        cents(price_cents)
        if (
            not isinstance(id, str)
            or not id
            or len(id) > 80
            or not isinstance(name, str)
            or not name
            or audience not in {"customer", "merchant"}
            or type(period_months) is not int
            or not 1 <= period_months <= 120
            or type(active) is not bool
            or type(purchasable) is not bool
            or not isinstance(entitlements, dict)
        ):
            raise CommerceError("套餐配置无效")
        for feature, allowance in entitlements.items():
            if not isinstance(feature, str) or not feature or len(feature) > 80:
                raise CommerceError("权益名称无效")
            if feature == "garment_upload" and type(allowance) is not int:
                raise CommerceError("上传次数必须为整数额度")
            if type(allowance) not in {int, bool} or (
                type(allowance) is int and not 0 <= allowance <= 100000
            ):
                raise CommerceError("权益必须为布尔开关或非负整数额度")
        with self.accounts.connect() as conn:
            conn.execute(
                "INSERT INTO commerce_plans VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,audience=excluded.audience,"
                "price_cents=excluded.price_cents,period_months=excluded.period_months,"
                "entitlements=excluded.entitlements,active=excluded.active,purchasable=excluded.purchasable",
                (
                    id,
                    name,
                    audience,
                    price_cents,
                    period_months,
                    json.dumps(entitlements),
                    int(active),
                    int(purchasable),
                ),
            )

    @staticmethod
    def _plan(row):
        if row is None:
            return None
        keys = (
            "id",
            "name",
            "audience",
            "price_cents",
            "period_months",
            "entitlements",
            "active",
            "purchasable",
        )
        result = dict(zip(keys, row, strict=False))
        result["entitlements"] = json.loads(result["entitlements"])
        result["active"], result["purchasable"] = (
            bool(result["active"]),
            bool(result["purchasable"]),
        )
        return result

    def prices(self):
        with self.accounts.connect() as conn:
            plans = [
                self._plan(row)
                for row in conn.execute("SELECT * FROM commerce_plans WHERE active=1 ORDER BY id")
            ]
        return {"currency": "CNY", "model_price_cents": self.model_price_cents, "plans": plans}

    def _active_subscriptions(self, conn, user_id, now):
        return conn.execute(
            "SELECT id,plan_id,starts,ends,entitlements FROM subscriptions "
            "WHERE user_id=? AND starts<=? AND ends>? ORDER BY starts DESC",
            (user_id, now, now),
        ).fetchall()

    def grant_subscription(self, conn, user_id, plan: dict, order_id: str, *, now=None):
        """Internal fulfillment, called only from a verified payment transaction."""
        now = int(time.time()) if now is None else now
        if conn.execute("SELECT 1 FROM subscriptions WHERE order_id=?", (order_id,)).fetchone():
            return
        user = conn.execute("SELECT role FROM merchants WHERE id=?", (user_id,)).fetchone()
        if user is None or (plan["audience"] == "merchant" and user[0] != "merchant"):
            raise CommerceError("账号不符合套餐身份要求")
        previous = conn.execute(
            "SELECT MAX(ends) FROM subscriptions WHERE user_id=? AND plan_id=?",
            (user_id, plan["id"]),
        ).fetchone()[0]
        starts = max(now, previous or now)
        conn.execute(
            "INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                user_id,
                plan["id"],
                starts,
                add_months(starts, plan["period_months"]),
                json.dumps(plan["entitlements"]),
                order_id,
            ),
        )

    def usage(self, conn, user_id, feature="garment_upload", *, now=None):
        now = int(time.time()) if now is None else now
        user = conn.execute("SELECT role,created FROM merchants WHERE id=?", (user_id,)).fetchone()
        if not user or user[0] != "merchant":
            raise CommerceError("需要商家身份")
        free = self._plan(
            conn.execute("SELECT * FROM commerce_plans WHERE id='merchant_free'").fetchone()
        )
        start, end = period_at(int(user[1]), free["period_months"], now)
        scope, limit = "merchant_free", free["entitlements"].get(feature, 0)
        choices = self._active_subscriptions(conn, user_id, now)
        # Overlapping tiers do not sum silently; the greatest allowance wins.
        for sub_id, _, sub_start, sub_end, rights in choices:
            allowance = json.loads(rights).get(feature, 0)
            if type(allowance) is int and allowance > limit:
                scope, start, end, limit = sub_id, sub_start, sub_end, allowance
        if type(limit) is not int:
            raise CommerceError("次数权益必须配置为整数")
        row = conn.execute(
            "SELECT used FROM usage_buckets WHERE user_id=? AND feature=? AND scope=? AND starts=?",
            (user_id, feature, scope, start),
        ).fetchone()
        used = row[0] if row else 0
        return {
            "feature": feature,
            "scope": scope,
            "starts": start,
            "ends": end,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
        }

    def consume_upload(self, conn, user_id, *, now=None):
        bucket = self.usage(conn, user_id, now=now)
        if bucket["remaining"] <= 0:
            raise CommerceError(f"本周期上传额度已用完（{bucket['limit']} 次）；删除商品不恢复额度")
        conn.execute(
            "INSERT INTO usage_buckets VALUES (?,?,?,?,?,1) "
            "ON CONFLICT(user_id,feature,scope,starts) DO UPDATE SET used=used+1",
            (user_id, bucket["feature"], bucket["scope"], bucket["starts"], bucket["ends"]),
        )

    def _ledger(self, conn, user_id, delta, kind, reference, *, now=None):
        if type(delta) is not int or abs(delta) > MAX_CENTS:
            raise CommerceError("流水金额必须为整数分")
        if not conn.execute("SELECT 1 FROM merchants WHERE id=?", (user_id,)).fetchone():
            raise CommerceError("账号不存在")
        old = conn.execute(
            "SELECT delta_cents,balance_cents FROM wallet_ledger "
            "WHERE user_id=? AND kind=? AND reference=?",
            (user_id, kind, reference),
        ).fetchone()
        if old:
            if old[0] != delta:
                raise IdempotencyConflict("重复交易金额不一致")
            return old[1]
        conn.execute("INSERT OR IGNORE INTO wallets VALUES (?,0)", (user_id,))
        balance = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        new_balance = balance + delta
        if new_balance < 0:
            raise InsufficientFunds("钱包余额不足，请先充值")
        cents(new_balance)
        conn.execute("UPDATE wallets SET balance_cents=? WHERE user_id=?", (new_balance, user_id))
        conn.execute(
            "INSERT INTO wallet_ledger VALUES (?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                user_id,
                delta,
                new_balance,
                kind,
                reference,
                int(time.time()) if now is None else now,
            ),
        )
        return new_balance

    def credit_verified_order(self, conn, user_id, amount_cents, order_id):
        return self._ledger(conn, user_id, cents(amount_cents, positive=True), "recharge", order_id)

    def reserve_model(self, user_id, idempotency_key, request: dict, *, now=None):
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
            raise CommerceError("建模请求需要 8 至 128 字符的幂等键")
        fingerprint = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        now = int(time.time()) if now is None else now
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT job_id,fingerprint,state,amount_cents FROM model_charges "
                "WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if row:
                if row[1] != fingerprint:
                    raise IdempotencyConflict("相同幂等键不可用于不同的建模请求")
                return {"job_id": row[0], "state": row[2], "amount_cents": row[3], "replayed": True}
            job_id = uuid4().hex
            amount = cents(self.model_price_cents)
            self._ledger(conn, user_id, -amount, "model_debit", job_id, now=now)
            conn.execute(
                "INSERT INTO model_charges VALUES (?,?,?,?,?,'reserved',?,?)",
                (job_id, user_id, amount, idempotency_key, fingerprint, now, now),
            )
        return {"job_id": job_id, "state": "reserved", "amount_cents": amount, "replayed": False}

    def finish_model(self, job_id, *, succeeded: bool, valid_result: bool, now=None):
        now = int(time.time()) if now is None else now
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id,amount_cents,state FROM model_charges WHERE job_id=?", (job_id,)
            ).fetchone()
            if not row or row[2] != "reserved":
                return
            state = "completed" if succeeded and valid_result else "refunded"
            if state == "refunded":
                self._ledger(conn, row[0], row[1], "model_refund", job_id, now=now)
            conn.execute(
                "UPDATE model_charges SET state=?,updated=? WHERE job_id=?", (state, now, job_id)
            )

    def refund_orphaned_models(self, active_ids, *, before: int):
        with self.accounts.connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM model_charges WHERE state='reserved' AND created<=?", (before,)
            ).fetchall()
        for (job_id,) in rows:
            if job_id not in active_ids:
                self.finish_model(job_id, succeeded=False, valid_result=False)

    def reconcile_models(self, lookup, valid_result):
        """Recover an interrupted enqueue/settlement, without repeating cloud calls."""
        with self.accounts.connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM model_charges WHERE state='reserved'"
            ).fetchall()
        for (job_id,) in rows:
            job = lookup(job_id)
            if not job:
                self.finish_model(job_id, succeeded=False, valid_result=False)
            elif job["state"] not in {"queued", "running", "awaiting_review"}:
                self.finish_model(
                    job_id, succeeded=job["state"] == "succeeded", valid_result=valid_result(job)
                )

    def summary(self, user_id, *, now=None):
        now = int(time.time()) if now is None else now
        with self.accounts.connect() as conn:
            conn.execute("BEGIN")
            user = conn.execute("SELECT role FROM merchants WHERE id=?", (user_id,)).fetchone()
            balance = conn.execute(
                "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT id,plan_id,starts,ends,entitlements FROM subscriptions "
                "WHERE user_id=? AND ends>? "
                "ORDER BY starts",
                (user_id, now),
            ).fetchall()
            rights = {}
            for _, _, start, _, entitlements in rows:
                if start <= now:
                    for feature, allowance in json.loads(entitlements).items():
                        rights[feature] = max(rights.get(feature, 0), allowance)
            usage = self.usage(conn, user_id, now=now) if user and user[0] == "merchant" else None
        return {
            "currency": "CNY",
            "balance_cents": balance[0] if balance else 0,
            "entitlements": rights,
            "subscriptions": [
                {
                    "id": row[0],
                    "plan_id": row[1],
                    "starts": row[2],
                    "ends": row[3],
                    "entitlements": json.loads(row[4]),
                }
                for row in rows
            ],
            "upload_usage": usage,
        }

    def ledger(self, user_id, *, limit=50, offset=0):
        with self.accounts.connect() as conn:
            rows = conn.execute(
                "SELECT id,delta_cents,balance_cents,kind,reference,created FROM wallet_ledger "
                "WHERE user_id=? ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ).fetchall()
        return [
            dict(
                zip(
                    ("id", "delta_cents", "balance_cents", "kind", "reference", "created"),
                    row,
                    strict=False,
                )
            )
            for row in rows
        ]

    def platform_totals(self):
        """Admin console aggregate: model charges, wallet balances, recent ledger.

        Ledger rows carry business references and amounts only; the privacy
        design keeps photos, paths and body metrics out of this table.
        """
        with self.accounts.connect() as conn:
            charges = {
                state: {"count": count, "amount_cents": amount}
                for state, count, amount in conn.execute(
                    "SELECT state, COUNT(*), COALESCE(SUM(amount_cents), 0) "
                    "FROM model_charges GROUP BY state"
                )
            }
            wallets = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(balance_cents), 0) FROM wallets"
            ).fetchone()
            ledger = conn.execute(
                "SELECT l.id, l.user_id, m.name, l.delta_cents, l.balance_cents, l.kind, "
                "l.reference, l.created FROM wallet_ledger l "
                "LEFT JOIN merchants m ON m.id = l.user_id "
                "ORDER BY l.created DESC, l.rowid DESC LIMIT 20"
            ).fetchall()
        return {
            "model_charges": {
                state: charges.get(state, {"count": 0, "amount_cents": 0})
                for state in ("reserved", "completed", "refunded")
            },
            "wallets": {"count": wallets[0], "total_balance_cents": wallets[1]},
            "recent_ledger": [
                dict(
                    zip(
                        ("id", "user_id", "account_name", "delta_cents", "balance_cents",
                         "kind", "reference", "created"),
                        row,
                        strict=False,
                    )
                )
                for row in ledger
            ],
        }
