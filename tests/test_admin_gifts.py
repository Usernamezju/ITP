"""Admin-only grants, atomic fulfillment, calendar renewals and retry protection."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from itp.admin_gifts import GiftRequest, GiftStore
from itp.api import create_app
from itp.config import Settings
from itp.merchant_auth import encode_token


@pytest.fixture
def setup(tmp_path):
    app = create_app(
        Settings(
            _env_file=None, data_dir=tmp_path / "data", environment="test", jwt_secret="a" * 64
        ),
        start_worker=False,
    )
    store = app.state.merchants
    users, headers = {}, {}
    for name, role in (
        ("ops", "admin"),
        ("fan", "merchant"),
        ("alice", "customer"),
        ("other", "merchant"),
    ):
        user = store.create_merchant(
            name=name,
            display_name=name,
            contact="",
            role=role,
            password_hash="unused-test-hash",
            quota=0,
        )
        users[name] = user
        token, _ = encode_token("a" * 64, user["id"], hours=1, password_hash=user["password_hash"])
        headers[name] = {"Authorization": "Bearer " + token}
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client, store, users, headers


def payload(user, **changes):
    return {
        "request_id": uuid4().hex,
        "user_id": user["id"],
        "kind": "points",
        "points": 800,
        "reason": "活动奖励",
        **changes,
    }


def test_access_control_and_private_routes(setup):
    client, store, users, headers = setup
    body = payload(users["fan"])
    for auth, expected in (({}, 401), (headers["fan"], 403), (headers["alice"], 403)):
        for path in ("/api/admin/gifts", "/api/admin/gifts/accounts?q=fan"):
            assert client.get(path, headers=auth).status_code == expected
        assert client.post("/api/admin/gifts", json=body, headers=auth).status_code == expected
    store.set_disabled(users["ops"]["id"], True)
    assert client.post("/api/admin/gifts", json=body, headers=headers["ops"]).status_code == 403
    assert not any(
        p.startswith("/api/admin/gifts") for p in client.get("/openapi.json").json()["paths"]
    )


def test_points_grant_is_audited_and_idempotent(setup):
    client, store, users, headers = setup
    before = store.commerce.benefits.summary(users["fan"]["id"])["balance_points"]
    other = store.commerce.summary(users["other"]["id"])
    body = payload(users["fan"])
    first = client.post("/api/admin/gifts", json=body, headers=headers["ops"])
    assert first.status_code == 200, first.text
    replay = client.post("/api/admin/gifts", json=body, headers=headers["ops"])
    assert replay.json()["id"] == first.json()["id"] and replay.json()["replayed"]
    assert store.commerce.benefits.summary(users["fan"]["id"])["balance_points"] == before + 800
    ledger = store.commerce.benefits.ledger(users["fan"]["id"])
    assert len(ledger) == 1 and ledger[0]["kind"] == "admin_gift"
    assert ledger[0]["reference"] == first.json()["id"]
    conflict = client.post("/api/admin/gifts", json=body | {"points": 801}, headers=headers["ops"])
    assert conflict.status_code == 409
    history = client.get("/api/admin/gifts", headers=headers["ops"])
    assert history.headers["cache-control"] == "no-store"
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["admin_name"] == "ops"
    assert history.json()["items"][0]["reason"] == "活动奖励"
    assert store.commerce.summary(users["other"]["id"]) == other
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payment_orders").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0] == 0


def test_member_grant_renews_and_does_not_duplicate_or_award_paid_bonus(setup):
    client, store, users, headers = setup
    body = payload(
        users["fan"], kind="membership", points=None, plan_id="merchant_premium", periods=2
    )
    for _ in range(2):
        response = client.post("/api/admin/gifts", json=body, headers=headers["ops"])
        assert response.status_code == 200, response.text
    subscriptions = response.json()["subscriptions"]
    assert len(subscriptions) == 2
    assert subscriptions[0]["ends"] == subscriptions[1]["starts"]
    assert store.commerce.summary(users["fan"]["id"])["upload_usage"]["unlimited"]
    new = client.post(
        "/api/admin/gifts", json=body | {"request_id": uuid4().hex}, headers=headers["ops"]
    ).json()
    assert new["subscriptions"][0]["starts"] == subscriptions[-1]["ends"]
    before = store.commerce.benefits.summary(users["alice"]["id"])["balance_points"]
    customer = payload(
        users["alice"], kind="membership", points=None, plan_id="customer_monthly", periods=1
    )
    assert client.post("/api/admin/gifts", json=customer, headers=headers["ops"]).status_code == 200
    summary = store.commerce.benefits.summary(users["alice"]["id"])
    assert summary["balance_points"] == before
    with store.connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM benefit_grants WHERE kind='first_membership'"
        ).fetchone()


@pytest.mark.parametrize(
    "change",
    [
        {"points": -1},
        {"points": 0},
        {"points": True},
        {"points": 1.5},
        {"points": "800"},
        {"points": 1_000_000_001},
        {"reason": " "},
        {"admin_id": "forged"},
        {"kind": "membership", "plan_id": "merchant_premium", "periods": 1},
        {"kind": "membership", "points": None, "plan_id": "merchant_premium", "periods": 13},
        {"kind": "membership", "points": None, "plan_id": "customer_monthly", "periods": 1},
        {"kind": "membership", "points": None, "plan_id": "merchant_free", "periods": 1},
    ],
)
def test_invalid_gifts_do_not_write(setup, change):
    client, store, users, headers = setup
    response = client.post(
        "/api/admin/gifts", json=payload(users["fan"], **change), headers=headers["ops"]
    )
    assert response.status_code == 422
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_gifts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM point_ledger WHERE kind='admin_gift'").fetchone()[0]
            == 0
        )


def test_invalid_recipient_search_and_history(setup):
    client, store, users, headers = setup
    assert (
        client.post(
            "/api/admin/gifts", json=payload(users["ops"]), headers=headers["ops"]
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/gifts", json=payload({"id": "missing"}), headers=headers["ops"]
        ).status_code
        == 404
    )
    store.set_disabled(users["fan"]["id"], True)
    assert (
        client.post(
            "/api/admin/gifts", json=payload(users["fan"]), headers=headers["ops"]
        ).status_code
        == 422
    )
    found = client.get("/api/admin/gifts/accounts?q=alice", headers=headers["ops"]).json()
    assert [item["id"] for item in found["items"]] == [users["alice"]["id"]]
    assert "password" not in json.dumps(found)
    assert (
        client.get("/api/admin/gifts/accounts?q=%25", headers=headers["ops"]).json()["items"] == []
    )
    assert (
        client.get("/api/admin/gifts/accounts?q=fan", headers=headers["ops"]).json()["items"] == []
    )


def test_concurrent_retries_and_rollback(setup, monkeypatch):
    _, store, users, _ = setup
    gifts = GiftStore(store)
    body = GiftRequest(**payload(users["fan"]))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: gifts.grant(users["ops"]["id"], body), range(4)))
    assert len({result["id"] for result in results}) == 1
    assert sum(not result["replayed"] for result in results) == 1
    body = GiftRequest(
        **payload(
            users["fan"], kind="membership", points=None, plan_id="merchant_premium", periods=2
        )
    )
    real = store.commerce.grant_subscription

    def fail_after_write(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(store.commerce, "grant_subscription", fail_after_write)
    with pytest.raises(RuntimeError):
        gifts.grant(users["ops"]["id"], body)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM admin_gifts").fetchone()[0] == 1
