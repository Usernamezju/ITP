import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from itp.benefits import TZ, month_after
from itp.commerce import CommerceError, IdempotencyConflict, InsufficientFunds
from itp.config import Settings
from itp.garments import MerchantStore


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp())


@pytest.fixture
def account(tmp_path):
    store = MerchantStore(tmp_path)
    user = store.create_merchant(
        name="alice",
        display_name="Alice",
        contact="",
        password_hash="hash",
        quota=0,
        role="customer",
    )
    created = stamp("2024-01-31T09:00:00")
    with store.connect() as conn:
        conn.execute("UPDATE merchants SET created=? WHERE id=?", (created, user["id"]))
    return store, user["id"], created


def subscribe(store, user, plan_id, now, reference="verified-order"):
    plan = next(p for p in store.commerce.prices()["plans"] if p["id"] == plan_id)
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        store.commerce.grant_subscription(conn, user, plan, reference, now=now)


def test_registration_once_and_first_calendar_month_exact_boundary(account):
    store, user, created = account
    benefits = store.commerce.benefits
    with store.connect() as conn:
        benefits.register(conn, user, "customer")
    assert benefits.summary(user, now=created)["balance_points"] == 200
    end = month_after(created)
    assert end == stamp("2024-02-29T09:00:00")
    benefits.check_in(user, now=end - 1)
    assert benefits.summary(user, now=end - 1)["recommend_limit"] == 2
    assert benefits.summary(user, now=end)["recommend_limit"] == 0
    with pytest.raises(CommerceError):
        benefits.check_in(user, now=end + 86400)
    assert benefits.summary(user, now=end + 86400)["balance_points"] == 210
    assert store.commerce.summary(user)["balance_cents"] == 0


def test_points_separate_immutable_idempotent_and_failure_refund(account):
    store, user, created = account
    c = store.commerce
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        c.credit_verified_order(conn, user, 100000, "cny-order")
    with pytest.raises(InsufficientFunds):
        c.reserve_model(user, "model-request", {}, now=created)
    subscribe(store, user, "customer_monthly", created)
    reserved = c.reserve_model(user, "model-request", {}, now=created)
    assert reserved["amount_points"] == 800
    assert c.reserve_model(user, "model-request", {}, now=created)["replayed"]
    assert c.summary(user)["balance_points"] == 400
    assert c.summary(user)["balance_cents"] == 100000
    for _ in range(3):
        c.finish_model(reserved["job_id"], succeeded=True, valid_result=False)
    assert c.summary(user)["balance_points"] == 1200
    assert sum(e["kind"] == "model_refund" for e in c.benefits.ledger(user)) == 1
    for statement in ("UPDATE point_ledger SET delta=0", "DELETE FROM point_ledger"):
        with pytest.raises(sqlite3.IntegrityError), store.connect() as conn:
            conn.execute(statement)


def test_legacy_cash_charge_refunds_in_cash(account):
    store, user, created = account
    with store.connect() as conn:
        store.commerce.credit_verified_order(conn, user, 10000, "legacy-order")
        store.commerce._ledger(conn, user, -1500, "model_debit", "legacy-job")
        conn.execute(
            "INSERT INTO model_charges "
            "(job_id,user_id,amount_cents,idempotency_key,fingerprint,state,created,updated) "
            "VALUES ('legacy-job',?,1500,'legacy-key','digest','reserved',?,?)",
            (user, created, created),
        )
    store.commerce.finish_model("legacy-job", succeeded=False, valid_result=False)
    assert store.commerce.summary(user)["balance_cents"] == 10000
    assert store.commerce.summary(user)["balance_points"] == 200


def test_membership_renewal_month_end_first_gift_once_and_expiry(account):
    store, user, created = account
    subscribe(store, user, "customer_monthly", created, "one")
    subscribe(store, user, "customer_monthly", created, "one")
    subscribe(store, user, "customer_monthly", created, "two")
    summary = store.commerce.summary(user, now=created)
    assert summary["balance_points"] == 1200
    assert len(summary["subscriptions"]) == 2
    assert summary["subscriptions"][1]["ends"] == stamp("2024-03-31T09:00:00")
    b = store.commerce.benefits
    assert b.summary(user, now=created)["signin_points"] == 50
    assert b.summary(user, now=created)["recommend_limit"] == 10
    b.check_in(user, now=created)
    assert b.summary(user, now=created)["balance_points"] == 1250
    assert not b.summary(user, now=stamp("2024-03-31T09:00:00"))["member"]


def test_same_day_concurrent_signin_once_and_shanghai_midnight(account):
    store, user, created = account
    b = store.commerce.benefits
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: b.check_in(user, now=created), range(12)))
    assert sum(not r["replayed"] for r in results) == 1
    assert b.summary(user, now=created)["balance_points"] == 210
    b.check_in(user, now=stamp("2024-01-31T23:59:59"))
    b.check_in(user, now=stamp("2024-02-01T00:00:00"))
    assert b.summary(user, now=created)["balance_points"] == 220


def test_makeup_race_cards_no_carry_and_no_outside_period(account):
    store, user, created = account
    subscribe(store, user, "customer_monthly", created)
    b = store.commerce.benefits
    now = stamp("2024-02-10T09:00:00")

    def makeup(n):
        try:
            b.check_in(user, f"2024-02-{n + 1:02d}", now=now)
            return True
        except CommerceError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(makeup, range(8))) == 5
    assert b.summary(user, now=now)["cards"] == 0
    with pytest.raises(CommerceError):
        b.check_in(user, "2024-01-30", now=now)
    subscribe(store, user, "customer_monthly", now, "renewal")
    renewal = stamp("2024-02-29T09:00:00")
    assert b.summary(user, now=renewal)["cards"] == 5
    with pytest.raises(CommerceError):
        b.check_in(user, "2024-02-15", now=renewal)


def test_full_attendance_one_award_including_makeup(account):
    store, user, _ = account
    start = stamp("2024-02-01T00:00:00")
    subscribe(store, user, "customer_monthly", start)
    b = store.commerce.benefits
    for day in range(2, 30):
        b.check_in(user, now=stamp(f"2024-02-{day:02d}T12:00:00"))
    now = stamp("2024-02-29T20:00:00")
    assert not any(e["kind"] == "full_attendance" for e in b.ledger(user))
    b.check_in(user, "2024-02-01", now=now)
    b.check_in(user, "2024-02-01", now=now)
    assert b.summary(user, now=now)["balance_points"] == 1200 + 29 * 50 + 100
    assert sum(e["kind"] == "full_attendance" for e in b.ledger(user)) == 1


@pytest.mark.parametrize(
    "plan,uploads,ai",
    [
        ("merchant_free", 10, 0),
        ("merchant_basic", 200, 50),
        ("merchant_standard", 500, 120),
        ("merchant_premium", None, 500),
    ],
)
def test_four_merchant_tiers_and_first_month_expiry(account, plan, uploads, ai):
    store, user, created = account
    with store.connect() as conn:
        conn.execute("UPDATE merchants SET role='merchant' WHERE id=?", (user,))
    if plan != "merchant_free":
        subscribe(store, user, plan, created)
    b = store.commerce.benefits
    assert b.merchant_usage_for(user, "garment_upload", now=created)["limit"] == uploads
    assert b.merchant_usage_for(user, "ai_description", now=created)["limit"] == ai
    assert b.merchant_usage_for(user, "garment_upload", now=month_after(created))["limit"] == 0


def test_upgrade_preserves_period_usage_and_ai_failure_returns_quota(account):
    store, user, created = account
    with store.connect() as conn:
        conn.execute("UPDATE merchants SET role='merchant' WHERE id=?", (user,))
    subscribe(store, user, "merchant_basic", created)
    b = store.commerce.benefits
    with store.connect() as conn:
        b.consume_upload(conn, user, now=created + 1)
    subscribe(store, user, "merchant_standard", created + 10, "upgrade")
    # Upgrades use the current merchant cycle anchor, so used allowance survives.
    assert b.merchant_usage_for(user, "garment_upload", now=created + 10)["used"] == 1
    call = b.reserve_call(user, "ai_description", "ai-request", {"url": "x"}, now=created + 10)
    assert not call["replayed"]
    b.finish_call(user, "ai_description", "ai-request")
    assert b.merchant_usage_for(user, "ai_description", now=created + 10)["used"] == 0
    b.reserve_call(user, "ai_description", "ai-request", {"url": "x"}, now=created + 10)
    b.finish_call(user, "ai_description", "ai-request", {"fields": {"name": "coat"}})
    assert b.reserve_call(user, "ai_description", "ai-request", {"url": "x"}, now=created + 10)[
        "replayed"
    ]
    with pytest.raises(IdempotencyConflict):
        b.reserve_call(user, "ai_description", "ai-request", {"url": "different"}, now=created + 10)


def test_recommendation_quota_failure_and_concurrent_limit(account):
    store, user, created = account
    b = store.commerce.benefits

    def call(n):
        try:
            b.reserve_call(user, "recommendation", f"recommend-{n}", {}, now=created)
            return True
        except CommerceError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(call, range(10))) == 2
    with store.connect() as conn:
        key = conn.execute("SELECT reference FROM quota_events LIMIT 1").fetchone()[0]
    b.finish_call(user, "recommendation", key)
    assert call(100)
    subscribe(store, user, "customer_monthly", created)
    assert b.summary(user, now=created)["recommend_limit"] == 10


def test_demo_privileges_fail_closed():
    for options in (
        {"environment": "production"},
        {"environment": "development", "public_origin": "https://example.com"},
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, demo_enabled=True, **options)
