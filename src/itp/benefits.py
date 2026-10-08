"""Points and monthly benefits. Money stays in the existing CNY wallet."""

import calendar
import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from itp.commerce import CommerceError, IdempotencyConflict, InsufficientFunds, cents
from itp.merchant_auth import current_user

TZ = ZoneInfo("Asia/Shanghai")


def month_after(stamp, months=1):
    date = datetime.fromtimestamp(stamp, TZ)
    year, month = divmod(date.year * 12 + date.month - 1 + months, 12)
    return int(
        date.replace(
            year=year, month=month + 1, day=min(date.day, calendar.monthrange(year, month + 1)[1])
        ).timestamp()
    )


def day_at(stamp):
    return datetime.fromtimestamp(stamp, TZ).date()


class BenefitsStore:
    def __init__(self, commerce, settings=None):
        self.commerce, self.accounts = commerce, commerce.accounts
        self.model_price_points = 800
        self.demo_enabled = bool(getattr(settings, "demo_enabled", False))
        with self.accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS point_wallets (
                    user_id TEXT PRIMARY KEY REFERENCES merchants(id),
                    balance INTEGER NOT NULL CHECK(typeof(balance)='integer' AND balance>=0)
                );
                CREATE TABLE IF NOT EXISTS point_ledger (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES merchants(id),
                    delta INTEGER NOT NULL CHECK(typeof(delta)='integer'),
                    balance INTEGER NOT NULL CHECK(typeof(balance)='integer' AND balance>=0),
                    kind TEXT NOT NULL, reference TEXT NOT NULL, created INTEGER NOT NULL,
                    UNIQUE(user_id,kind,reference)
                );
                CREATE TRIGGER IF NOT EXISTS point_ledger_no_update BEFORE UPDATE ON point_ledger
                    BEGIN SELECT RAISE(ABORT,'point ledger is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS point_ledger_no_delete BEFORE DELETE ON point_ledger
                    BEGIN SELECT RAISE(ABORT,'point ledger is append-only'); END;
                CREATE TABLE IF NOT EXISTS benefit_grants (
                    user_id TEXT NOT NULL REFERENCES merchants(id), kind TEXT NOT NULL,
                    reference TEXT NOT NULL, created INTEGER NOT NULL,
                    PRIMARY KEY(user_id,kind,reference)
                );
                CREATE TABLE IF NOT EXISTS attendance (
                    user_id TEXT NOT NULL REFERENCES merchants(id), day TEXT NOT NULL,
                    supplemented INTEGER NOT NULL, points INTEGER NOT NULL,
                    scope TEXT NOT NULL, created INTEGER NOT NULL,
                    PRIMARY KEY(user_id,day)
                );
                CREATE TABLE IF NOT EXISTS quota_events (
                    user_id TEXT NOT NULL, feature TEXT NOT NULL, reference TEXT NOT NULL,
                    created INTEGER NOT NULL, amount INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL, fingerprint TEXT NOT NULL DEFAULT '', result TEXT,
                    PRIMARY KEY(user_id,feature,reference)
                );
                CREATE TABLE IF NOT EXISTS demo_accounts (
                    user_id TEXT PRIMARY KEY REFERENCES merchants(id), created INTEGER NOT NULL
                );
            """)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(model_charges)")}
            if "amount_points" not in columns:
                conn.execute(
                    "ALTER TABLE model_charges ADD COLUMN amount_points INTEGER "
                    "CHECK(amount_points>=0)"
                )
            # Record aggregate legacy usage once; deleted products never earn
            # quota back. New events use their actual creation timestamps.
            if not conn.execute(
                "SELECT 1 FROM commerce_migrations WHERE version='quota_events_v2'"
            ).fetchone():
                conn.execute(
                    "INSERT OR IGNORE INTO quota_events "
                    "(user_id,feature,reference,created,amount,state) "
                    "SELECT user_id,feature,'legacy:'||scope||':'||starts,starts,used,'done' "
                    "FROM usage_buckets"
                )
                conn.execute("INSERT INTO commerce_migrations VALUES ('quota_events_v2')")

    def demo(self, conn, user_id):
        return self.demo_enabled and bool(
            conn.execute("SELECT 1 FROM demo_accounts WHERE user_id=?", (user_id,)).fetchone()
        )

    def ledger_change(self, conn, user_id, delta, kind, reference, *, now=None):
        if type(delta) is not int or abs(delta) > 10**12:
            raise CommerceError("积分必须是整数")
        old = conn.execute(
            "SELECT delta,balance FROM point_ledger WHERE user_id=? AND kind=? AND reference=?",
            (user_id, kind, reference),
        ).fetchone()
        if old:
            if old[0] != delta:
                raise IdempotencyConflict("相同积分业务编号的数值不一致")
            return old[1]
        conn.execute("INSERT OR IGNORE INTO point_wallets VALUES (?,0)", (user_id,))
        balance = (
            conn.execute(
                "SELECT balance FROM point_wallets WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            + delta
        )
        if balance < 0:
            raise InsufficientFunds("积分不足，请先签到或开通会员")
        cents(balance)
        conn.execute("UPDATE point_wallets SET balance=? WHERE user_id=?", (balance, user_id))
        conn.execute(
            "INSERT INTO point_ledger VALUES (?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                user_id,
                delta,
                balance,
                kind,
                reference,
                int(time.time()) if now is None else now,
            ),
        )
        return balance

    def grant(self, conn, user_id, kind, reference, points, *, now=None):
        now = int(time.time()) if now is None else now
        inserted = conn.execute(
            "INSERT OR IGNORE INTO benefit_grants VALUES (?,?,?,?)", (user_id, kind, reference, now)
        ).rowcount
        if inserted and points:
            self.ledger_change(conn, user_id, points, kind, reference, now=now)
        return bool(inserted)

    def register(self, conn, user_id, role, *, now=None):
        if role == "customer":
            self.grant(conn, user_id, "registration", "once", 200, now=now)

    def membership(self, conn, user_id, now):
        rows = self.commerce._active_subscriptions(conn, user_id, now)
        for sub_id, plan_id, start, end, encoded in rows:
            rights = json.loads(encoded)
            if rights.get("customer_monthly") or (
                plan_id == "customer_annual" and rights.get("personalized_recommendation")
            ):
                if plan_id == "customer_annual":
                    anchor = start
                    index = 0
                    while month_after(anchor, index + 1) <= now:
                        index += 1
                    start, end = (
                        month_after(anchor, index),
                        min(end, month_after(anchor, index + 1)),
                    )
                    sub_id = f"{sub_id}:{index}"
                return {
                    "scope": sub_id,
                    "starts": start,
                    "ends": end,
                    "plan_id": plan_id,
                    "rights": rights,
                }
        return None

    def customer_rights(self, conn, user_id, now):
        user = conn.execute("SELECT created FROM merchants WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise CommerceError("账号不存在")
        member = self.membership(conn, user_id, now)
        end = month_after(int(user[0]))
        first = int(user[0]) <= now < end
        return {
            "member": bool(member),
            "first_month": first,
            "first_month_ends": end,
            "signin_points": 50 if member else 10 if first else 0,
            "recommend_limit": 10 if member else 2 if first else 0,
            "cycle": member,
        }

    def merchant_usage(self, conn, user_id, feature="garment_upload", *, now=None):
        now = int(time.time()) if now is None else now
        user = conn.execute("SELECT role,created FROM merchants WHERE id=?", (user_id,)).fetchone()
        if not user or user[0] != "merchant":
            raise CommerceError("需要商家身份")
        free = self.commerce._plan(
            conn.execute("SELECT * FROM commerce_plans WHERE id='merchant_free'").fetchone()
        )
        start, end = int(user[1]), month_after(int(user[1]))
        limit = free["entitlements"].get(feature, 0) if start <= now < end else 0
        scope = "merchant_free"
        subscriptions = self.commerce._active_subscriptions(conn, user_id, now)
        for sub_id, _, sub_start, sub_end, rights in subscriptions:
            allowance = json.loads(rights).get(feature, 0)
            if allowance is True or (
                type(allowance) is int and limit is not True and allowance > limit
            ):
                scope, start, end, limit = sub_id, sub_start, sub_end, allowance
        # An upgrade raises the allowance, without refunding earlier consumption
        # within any overlapping merchant cycle (including the free first month).
        anchors = [s[2] for s in subscriptions if "garment_upload" in json.loads(s[4])]
        if int(user[1]) <= now < month_after(int(user[1])):
            anchors.append(int(user[1]))
        if anchors:
            start = min(start, *anchors)
        used = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM quota_events WHERE user_id=? "
            "AND feature=? AND created>=? AND created<? AND state!='refunded'",
            (user_id, feature, start, end),
        ).fetchone()[0]
        unlimited = limit is True or self.demo(conn, user_id)
        return {
            "feature": feature,
            "scope": scope,
            "starts": start,
            "ends": end,
            "limit": None if unlimited else limit,
            "used": used,
            "unlimited": unlimited,
            "remaining": None if unlimited else max(0, limit - used),
        }

    def consume_upload(self, conn, user_id, *, now=None):
        now = int(time.time()) if now is None else now
        bucket = self.merchant_usage(conn, user_id, now=now)
        if not bucket["unlimited"] and bucket["remaining"] <= 0:
            raise CommerceError("本期新增商品额度已用完或首月已结束；删除商品不返还额度")
        conn.execute(
            "INSERT INTO quota_events (user_id,feature,reference,created,state) "
            "VALUES (?,'garment_upload',?,?,'done')",
            (user_id, uuid4().hex, now),
        )

    def merchant_usage_for(self, user_id, feature, *, now=None):
        with self.accounts.connect() as conn:
            return self.merchant_usage(conn, user_id, feature, now=now)

    def reserve_call(self, user_id, feature, reference, request, *, now=None):
        now = int(time.time()) if now is None else now
        if not reference or not 8 <= len(reference) <= 128:
            raise CommerceError("调用需要 8 至 128 字符的幂等键")
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT fingerprint,state,result FROM quota_events "
                "WHERE user_id=? AND feature=? AND reference=?",
                (user_id, feature, reference),
            ).fetchone()
            if old:
                if digest != old[0]:
                    raise IdempotencyConflict("同一幂等键不可用于不同请求")
                if old[1] == "done":
                    return {"replayed": True, "result": json.loads(old[2]) if old[2] else None}
                if old[1] == "reserved":
                    raise IdempotencyConflict("该请求仍在处理中，请稍后重试")
            if feature == "recommendation":
                rights = self.customer_rights(conn, user_id, now)
                date = datetime.fromtimestamp(now, TZ)
                start = int(date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                limit = rights["recommend_limit"]
                used = conn.execute(
                    "SELECT COUNT(*) FROM quota_events WHERE user_id=? "
                    "AND feature=? AND created>=? AND state!='refunded'",
                    (user_id, feature, start),
                ).fetchone()[0]
                unlimited = self.demo(conn, user_id)
                if not unlimited and used >= limit:
                    raise CommerceError("今日推荐额度已用完或免费首月已结束，请查看会员权益")
            else:
                bucket = self.merchant_usage(conn, user_id, feature, now=now)
                if not bucket["unlimited"] and bucket["remaining"] <= 0:
                    raise CommerceError("本期 AI 描述额度已用完，请查看商家套餐")
            conn.execute(
                "INSERT INTO quota_events "
                "(user_id,feature,reference,created,state,fingerprint) "
                "VALUES (?,?,?,?,'reserved',?) "
                "ON CONFLICT(user_id,feature,reference) DO UPDATE SET state='reserved',"
                "created=excluded.created",
                (user_id, feature, reference, now, digest),
            )
        return {"replayed": False}

    def finish_call(self, user_id, feature, reference, result=None):
        with self.accounts.connect() as conn:
            conn.execute(
                "UPDATE quota_events SET state=?,result=? "
                "WHERE user_id=? AND feature=? AND reference=? AND state='reserved'",
                (
                    "done" if result is not None else "refunded",
                    json.dumps(result) if result is not None else None,
                    user_id,
                    feature,
                    reference,
                ),
            )

    def check_in(self, user_id, target=None, *, now=None):
        now = int(time.time()) if now is None else now
        today = day_at(now)
        try:
            day = datetime.strptime(target, "%Y-%m-%d").date() if target else today
        except (TypeError, ValueError):
            raise CommerceError("签到日期格式应为 YYYY-MM-DD") from None
        if day > today:
            raise CommerceError("不能签到未来日期")
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rights = self.customer_rights(conn, user_id, now)
            existing = conn.execute(
                "SELECT 1 FROM attendance WHERE user_id=? AND day=?", (user_id, day.isoformat())
            ).fetchone()
            if existing:
                return {"signed": True, "replayed": True}
            supplemented = day < today
            cycle = rights["cycle"]
            if supplemented:
                if not cycle or not day_at(cycle["starts"]) <= day < day_at(cycle["ends"]):
                    raise CommerceError("仅可补签当前有效会员周期内的漏签日")
                cards = conn.execute(
                    "SELECT COUNT(*) FROM attendance WHERE user_id=? "
                    "AND scope=? AND supplemented=1",
                    (user_id, cycle["scope"]),
                ).fetchone()[0]
                if cards >= 5:
                    raise CommerceError("本会员周期补签卡已用完")
            if rights["signin_points"] == 0:
                raise CommerceError("免费首月已结束，签到需有效会员")
            scope = cycle["scope"] if cycle else "first_month"
            points = rights["signin_points"]
            if cycle:
                self.grant(conn, user_id, "makeup_cards", scope, 0, now=now)
            conn.execute(
                "INSERT INTO attendance VALUES (?,?,?,?,?,?)",
                (user_id, day.isoformat(), int(supplemented), points, scope, now),
            )
            self.grant(
                conn,
                user_id,
                "makeup" if supplemented else "signin",
                day.isoformat(),
                points,
                now=now,
            )
            if cycle:
                first, end = day_at(cycle["starts"]), day_at(cycle["ends"])
                count = conn.execute(
                    "SELECT COUNT(*) FROM attendance WHERE user_id=? AND day>=? AND day<?",
                    (user_id, first.isoformat(), end.isoformat()),
                ).fetchone()[0]
                if count == (end - first).days:
                    self.grant(conn, user_id, "full_attendance", scope, 100, now=now)
        return {"signed": True, "replayed": False, "points": points}

    def summary(self, user_id, *, month=None, now=None):
        now = int(time.time()) if now is None else now
        today = day_at(now)
        try:
            first = (
                datetime.strptime(month + "-01", "%Y-%m-%d").date()
                if month
                else today.replace(day=1)
            )
        except (TypeError, ValueError):
            raise CommerceError("月份格式应为 YYYY-MM") from None
        with self.accounts.connect() as conn:
            rights = self.customer_rights(conn, user_id, now)
            cycle = rights["cycle"]
            balance = conn.execute(
                "SELECT balance FROM point_wallets WHERE user_id=?", (user_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT day,supplemented,points FROM attendance WHERE user_id=?", (user_id,)
            ).fetchall()
            signed = {
                day: {"supplemented": bool(makeup), "points": points}
                for day, makeup, points in rows
            }
            cards = (
                5
                - sum(
                    bool(makeup)
                    for day, makeup, _ in rows
                    if cycle
                    and day_at(cycle["starts"]).isoformat()
                    <= day
                    < day_at(cycle["ends"]).isoformat()
                )
                if cycle
                else 0
            )
            progress = sum(
                1
                for day in signed
                if cycle
                and day_at(cycle["starts"]).isoformat() <= day < day_at(cycle["ends"]).isoformat()
            )
            used = conn.execute(
                "SELECT COUNT(*) FROM quota_events WHERE user_id=? AND "
                "feature='recommendation' AND created>=? AND state!='refunded'",
                (user_id, int(datetime.combine(today, datetime.min.time(), TZ).timestamp())),
            ).fetchone()[0]
            demo = self.demo(conn, user_id)
        days = []
        for n in range(calendar.monthrange(first.year, first.month)[1]):
            date = first + timedelta(days=n)
            key = date.isoformat()
            record = signed.get(key)
            eligible = bool(cycle and day_at(cycle["starts"]) <= date < day_at(cycle["ends"]))
            status = (
                "supplemented"
                if record and record["supplemented"]
                else "signed"
                if record
                else "future"
                if date > today
                else "today"
                if date == today
                else "missed"
            )
            days.append(
                {
                    "day": key,
                    "state": status,
                    "can_makeup": eligible and date < today and not record and cards > 0,
                }
            )
        streak = 0
        cursor = today if today.isoformat() in signed else today - timedelta(days=1)
        while cursor.isoformat() in signed:
            streak += 1
            cursor -= timedelta(days=1)
        return rights | {
            "balance_points": balance[0] if balance else 0,
            "model_price_points": self.model_price_points,
            "timezone": str(TZ),
            "today": today.isoformat(),
            "month": first.strftime("%Y-%m"),
            "days": days,
            "cards": max(0, cards),
            "streak": streak,
            "attendance_count": progress,
            "attendance_required": (day_at(cycle["ends"]) - day_at(cycle["starts"])).days
            if cycle
            else 0,
            "recommend_used": used,
            "demo_unlimited": demo,
        }

    def ledger(self, user_id, limit=50, offset=0):
        with self.accounts.connect() as conn:
            return [
                dict(
                    zip(
                        ("id", "delta", "balance", "kind", "reference", "created"), row, strict=True
                    )
                )
                for row in conn.execute(
                    "SELECT id,delta,balance,kind,reference,created "
                    "FROM point_ledger WHERE user_id=? "
                    "ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?",
                    (user_id, limit, offset),
                )
            ]


class SigninRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str | None = None


def benefits_router(accounts):
    router = APIRouter()
    benefits = accounts.commerce.benefits

    @router.get("/api/account/points")
    def summary(user: Annotated[dict, Depends(current_user)], month: str | None = None):
        try:
            return benefits.summary(user["id"], month=month)
        except CommerceError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/account/points/ledger")
    def ledger(
        user: Annotated[dict, Depends(current_user)],
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return {"items": benefits.ledger(user["id"], limit, offset)}

    @router.post("/api/account/signin")
    def signin(body: SigninRequest, user: Annotated[dict, Depends(current_user)]):
        try:
            return benefits.check_in(user["id"], body.day)
        except CommerceError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
