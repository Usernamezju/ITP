"""Integer money, atomic ledger, calendar quotas, billing and private APIs."""

import json
import sqlite3
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from auth_helpers import fund_client
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.commerce import (
    CommerceError,
    IdempotencyConflict,
    InsufficientFunds,
    add_months,
    cents,
    period_at,
)
from itp.config import Settings
from itp.garments import AlreadyExists, MerchantStore, QuotaExceeded
from itp.model_validation import valid_mesh


def stamp(date):
    return int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp())


@pytest.fixture
def commerce(tmp_path):
    accounts = MerchantStore(tmp_path)
    user = accounts.create_merchant(
        name="alice",
        display_name="Alice",
        contact="",
        password_hash="test-hash",
        quota=200,
        role="merchant",
    )
    return accounts.commerce, user["id"]


def credit(commerce, user_id, amount=10000, reference="verified-order"):
    with commerce.accounts.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return commerce.credit_verified_order(conn, user_id, amount, reference)


@pytest.mark.parametrize("value", [True, False, 30.0, 0.1, "3000", -1, 10**12 + 1])
def test_cents_never_coerces_float_boolean_or_negative(value):
    with pytest.raises(CommerceError):
        cents(value)


@pytest.mark.parametrize("value", [True, 1500.0, 1.5, "-1", "15.00"])
def test_price_settings_reject_non_integer_money(value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, model_price_cents=value)


def test_defaults_and_custom_commercial_plan(commerce):
    c, user = commerce
    prices = c.prices()
    plans = {p["id"]: p for p in prices["plans"]}
    assert prices["model_price_cents"] == 1500 and plans["customer_annual"]["price_cents"] == 3000
    assert plans["customer_annual"]["period_months"] == 12
    assert plans["merchant_free"]["entitlements"]["garment_upload"] == 5
    c.configure_plan(
        "business",
        name="Business",
        audience="merchant",
        price_cents=88800,
        period_months=3,
        entitlements={"garment_upload": 87, "custom_feature": True},
    )
    assert next(p for p in c.prices()["plans"] if p["id"] == "business")["price_cents"] == 88800
    assert c.summary(user)["balance_cents"] == 0


def test_calendar_month_ends_leap_year_and_exact_reset():
    jan = stamp("2024-01-31T09:20:00")
    feb = stamp("2024-02-29T09:20:00")
    march = stamp("2024-03-31T09:20:00")
    assert add_months(jan, 1) == feb
    assert period_at(jan, 1, feb - 1) == (jan, feb)
    assert period_at(jan, 1, feb) == (feb, march)
    assert add_months(feb, 12) == stamp("2025-02-28T09:20:00")


def test_delete_does_not_restore_consumed_uploads_and_duplicate_sku_rolls_back(commerce):
    c, user = commerce
    store = c.accounts
    metrics = {"name": "Coat", "status": "draft", "sku": "one"}
    first = store.create_garment(user, metrics)
    with pytest.raises(AlreadyExists):
        store.create_garment(user, metrics)
    assert c.summary(user)["upload_usage"]["used"] == 1
    store.delete_garment(user, first["id"])
    assert c.summary(user)["upload_usage"]["used"] == 1
    for n in range(4):
        store.create_garment(user, {"name": str(n), "status": "draft"})
    with pytest.raises(QuotaExceeded):
        store.create_garment(user, {"name": "sixth", "status": "draft"})
    assert store.count_garments(user) == 4
    bucket = c.summary(user)["upload_usage"]
    assert c.summary(user, now=bucket["ends"])["upload_usage"]["used"] == 0


def test_cross_connection_upload_race_cannot_overrun(commerce):
    c, user = commerce

    def upload(n):
        independent = MerchantStore(c.accounts.root)
        try:
            independent.create_garment(user, {"name": str(n), "status": "draft"})
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(upload, range(12))) == 5
    assert c.summary(user)["upload_usage"]["used"] == 5


def test_ledger_credit_idempotency_and_restart(commerce):
    c, user = commerce
    assert credit(c, user) == 10000
    assert credit(c, user) == 10000
    with pytest.raises(IdempotencyConflict):
        credit(c, user, 12000)
    reopened = MerchantStore(c.accounts.root).commerce
    assert reopened.summary(user)["balance_cents"] == 10000
    assert len(reopened.ledger(user)) == 1
    with c.accounts.connect() as conn:
        assert conn.execute("SELECT typeof(balance_cents) FROM wallets").fetchone()[0] == "integer"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE wallets SET balance_cents=0.5")


def test_model_reservation_idempotency_and_refund_once(commerce):
    c, user = commerce
    credit(c, user)
    reserved = c.reserve_model(user, "same-request-key", {"front": "sensitive-file-id"})
    assert c.summary(user)["balance_cents"] == 8500
    assert c.reserve_model(user, "same-request-key", {"front": "sensitive-file-id"})["replayed"]
    with pytest.raises(IdempotencyConflict):
        c.reserve_model(user, "same-request-key", {"front": "different"})
    c.finish_model(reserved["job_id"], succeeded=False, valid_result=True)
    c.finish_model(reserved["job_id"], succeeded=True, valid_result=True)
    assert c.summary(user)["balance_cents"] == 10000
    assert [entry["kind"] for entry in c.ledger(user)].count("model_refund") == 1
    with c.accounts.connect() as conn:
        row = conn.execute("SELECT fingerprint,state FROM model_charges").fetchone()
        assert row[1] == "refunded" and "sensitive-file-id" not in str(row)


@pytest.mark.parametrize(
    "succeeded,valid,expected", [(True, True, 8500), (True, False, 10000), (False, False, 10000)]
)
def test_only_successful_valid_mesh_retains_debit(commerce, succeeded, valid, expected):
    c, user = commerce
    credit(c, user)
    job = c.reserve_model(user, "a-model-request", {"front": "x"})
    c.finish_model(job["job_id"], succeeded=succeeded, valid_result=valid)
    assert c.summary(user)["balance_cents"] == expected


def test_insufficient_funds_creates_no_debit_or_charge(commerce):
    c, user = commerce
    with pytest.raises(InsufficientFunds):
        c.reserve_model(user, "a-model-request", {})
    assert c.ledger(user) == []
    with c.accounts.connect() as conn:
        assert conn.execute("SELECT count(*) FROM model_charges").fetchone()[0] == 0


def test_duplicate_and_parallel_debits_are_serialized(commerce):
    c, user = commerce
    credit(c, user, 3000)

    def debit(n):
        try:
            return c.reserve_model(user, f"request-number-{n}", {})["job_id"]
        except InsufficientFunds:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(debit, range(10)))
    assert sum(result is not None for result in results) == 2
    assert c.summary(user)["balance_cents"] == 0


def test_orphaned_crash_reservations_refund_not_live_jobs(commerce):
    c, user = commerce
    credit(c, user)
    c.reserve_model(user, "orphan-request", {}, now=1)
    live = c.reserve_model(user, "live-request", {}, now=1)
    c.refund_orphaned_models({live["job_id"]}, before=2)
    c.refund_orphaned_models({live["job_id"]}, before=2)
    assert c.summary(user)["balance_cents"] == 8500


def test_subscription_snapshots_renewal_and_expiry(commerce):
    c, user = commerce
    c.configure_plan(
        "pro",
        name="Pro",
        audience="merchant",
        price_cents=9000,
        period_months=2,
        entitlements={"garment_upload": 30, "personalized_recommendation": True},
    )
    plan = next(p for p in c.prices()["plans"] if p["id"] == "pro")
    now = stamp("2026-10-06T12:00:00")
    with c.accounts.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        c.grant_subscription(conn, user, plan, "paid-order-one", now=now)
        c.grant_subscription(conn, user, plan, "paid-order-one", now=now)
        c.grant_subscription(conn, user, plan, "paid-order-two", now=now)
    summary = c.summary(user, now=now)
    assert len(summary["subscriptions"]) == 2
    assert summary["subscriptions"][0]["ends"] == summary["subscriptions"][1]["starts"]
    assert summary["upload_usage"]["limit"] == 30
    c.configure_plan(
        "pro",
        name="New Pro",
        audience="merchant",
        price_cents=10000,
        period_months=1,
        entitlements={"garment_upload": 60},
    )
    assert c.summary(user, now=now)["upload_usage"]["limit"] == 30
    expired = c.summary(user, now=summary["subscriptions"][-1]["ends"])
    assert expired["upload_usage"]["limit"] == 5 and not expired["entitlements"]


def triangle_glb():
    binary = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(binary)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
    }
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, length)
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def test_mesh_validation_rejects_magic_only_and_truncated_results(tmp_path):
    path = tmp_path / "result.glb"
    for data in (b"glTF" + b"\0" * 20, triangle_glb()[:-1], b"not a model"):
        path.write_bytes(data)
        assert not valid_mesh(path, "GLB")
    path.write_bytes(triangle_glb())
    assert valid_mesh(path, "GLB")


def test_paid_model_api_replay_failure_refund_and_owner_isolation(settings, image_bytes):
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.get("/api/account/commerce").status_code == 401
        assert client.post("/api/jobs", json={"front": "a" * 32}).status_code == 401
        fund_client(client)
        photo = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        body = {"front": photo["id"]}
        before = client.get("/api/account/commerce").json()["balance_cents"]
        first = client.post("/api/jobs", json=body)
        assert first.status_code == 201
        duplicate = client.post("/api/jobs", json=body)
        assert duplicate.json()["id"] == first.json()["id"]
        assert client.get("/api/account/commerce").json()["balance_cents"] == before - 1500
        client.headers["Idempotency-Key"] = "new-model-request"
        assert client.post("/api/jobs", json=body).status_code == 201
        # No network call: a local worker error exercises real automatic refund.
        app.state.pipeline.cloud_step = lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("test")
        )
        job = app.state.store.job(first.json()["id"])
        app.state.pipeline.run_job(job)
        assert client.get("/api/account/commerce").json()["balance_cents"] == before - 1500
        assert (
            len(
                [
                    e
                    for e in client.get("/api/account/ledger").json()["items"]
                    if e["kind"] == "model_refund"
                ]
            )
            == 1
        )
        client.headers.pop("Authorization")
        assert client.get("/api/jobs").status_code == 401


def test_prices_are_configured_and_no_browser_can_credit_a_wallet(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            model_price_cents=678,
            customer_membership_price_cents=3456,
        ),
        start_worker=False,
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        pricing = client.get("/api/pricing").json()
        assert pricing["model_price_cents"] == 678
        assert (
            next(p for p in pricing["plans"] if p["id"] == "customer_annual")["price_cents"] == 3456
        )
        assert (
            client.post("/api/account/commerce", json={"balance_cents": 999999}).status_code == 405
        )


def test_successful_pipeline_keeps_charge_and_rejection_refunds(settings, image_bytes):
    from test_pipeline import Cloud

    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        fund_client(client)
        photo = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        first = client.post("/api/jobs", json={"front": photo["id"], "texture": False}).json()
        before = app.state.commerce.summary(first["owner_id"])["balance_cents"]
        app.state.pipeline.cloud = Cloud()
        app.state.pipeline.fetch = lambda url, path, **kwargs: path.write_bytes(triangle_glb())
        app.state.pipeline.run_job(app.state.store.job(first["id"]))
        assert client.get(f"/api/jobs/{first['id']}").json()["state"] == "succeeded"
        assert app.state.commerce.summary(first["owner_id"])["balance_cents"] == before
        client.headers["Idempotency-Key"] = "pose-review-request"
        photo = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        second = client.post("/api/jobs", json={"front": photo["id"]}).json()
        job = app.state.store.job(second["id"])
        job["state"] = "awaiting_review"
        app.state.store.save_job(job)
        assert (
            client.post(f"/api/jobs/{second['id']}/review", json={"approve": False}).status_code
            == 200
        )
        assert app.state.commerce.summary(first["owner_id"])["balance_cents"] == before


def test_enqueue_exception_refunds_and_other_users_cannot_read_jobs(settings, image_bytes):
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost:8000", raise_server_exceptions=False) as client:
        fund_client(client)
        photo = client.post("/api/assets", files={"file": ("front.png", image_bytes)}).json()
        first = client.post("/api/jobs", json={"front": photo["id"]}).json()
        before = app.state.commerce.summary(first["owner_id"])["balance_cents"]
        client.headers["Idempotency-Key"] = "failed-enqueue-request"
        app.state.store.create_job = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk"))
        assert client.post("/api/jobs", json={"front": photo["id"]}).status_code == 500
        assert app.state.commerce.summary(first["owner_id"])["balance_cents"] == before
        client.headers.pop("Authorization")
        assert (
            client.post(
                "/api/auth/register",
                json={"name": "other", "display_name": "Other", "password": "another-password"},
            ).status_code
            == 201
        )
        token = client.post(
            "/api/auth/login", json={"name": "other", "password": "another-password"}
        ).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        assert client.get("/api/jobs").json() == []
        assert client.get(f"/api/jobs/{first['id']}").status_code == 404
        assert (
            client.post(f"/api/jobs/{first['id']}/review", json={"approve": False}).status_code
            == 404
        )


def test_reconciliation_settles_terminal_and_missing_jobs(commerce):
    c, user = commerce
    credit(c, user)
    complete = c.reserve_model(user, "completed-request", {})
    missing = c.reserve_model(user, "missing-request", {})
    c.reconcile_models(
        lambda job_id: {"state": "succeeded"} if job_id == complete["job_id"] else None,
        lambda job: True,
    )
    assert c.summary(user)["balance_cents"] == 8500
    c.finish_model(missing["job_id"], succeeded=False, valid_result=False)
    assert c.summary(user)["balance_cents"] == 8500
