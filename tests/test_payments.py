"""Offline transactional orders: no official or charged API is called."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest
from auth_helpers import fund_client
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.commerce import CommerceError, IdempotencyConflict
from itp.config import Settings
from itp.garments import MerchantStore
from itp.payment_service import PaymentService
from itp.payments import MockProvider, PaymentError, VerifiedPayment, amount_from_yuan


def mock_settings(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        environment="test",
        payment_mock_enabled=True,
        payment_mock_secret="test-signing-secret-" * 3,
        **overrides,
    )


@pytest.fixture
def service(tmp_path):
    accounts = MerchantStore(tmp_path)
    user = accounts.create_merchant(
        name="alice",
        display_name="Alice",
        contact="",
        password_hash="test",
        quota=0,
        role="customer",
    )
    return PaymentService(accounts, mock_settings(tmp_path)), user["id"]


def recharge(provider="mock", amount=5000):
    return {"kind": "recharge", "provider": provider, "amount_cents": amount, "plan_id": None}


def test_mock_is_explicit_and_forbidden_in_public_or_production(tmp_path):
    for kwargs in (
        {"environment": "production"},
        {"environment": "test", "public_origin": "https://example.org"},
        {"environment": "development", "payment_mock_secret": ""},
    ):
        base = {
            "environment": "test",
            "payment_mock_enabled": True,
            "payment_mock_secret": "x" * 32,
        }
        base.update(kwargs)
        with pytest.raises(ValueError):
            Settings(_env_file=None, **base)
    with pytest.raises(PaymentError):
        MockProvider(Settings(_env_file=None), MerchantStore(tmp_path))
    production = create_app(Settings(_env_file=None, data_dir=tmp_path), start_worker=False)
    assert not any("/mock-pay" in path for path in production.openapi()["paths"])
    assert "mock" not in production.state.payments.providers


@pytest.mark.parametrize("value", [1.2, True, "NaN", "Infinity", "1.001", "-2.00", "0"])
def test_official_amount_parser_is_exact_and_never_uses_float(value):
    with pytest.raises((PaymentError, CommerceError)):
        amount_from_yuan(value)
    assert amount_from_yuan("30.00") == 3000
    assert amount_from_yuan(Decimal("29.99")) == 2999


def test_recharge_only_after_verified_mock_query_and_replay_once(service):
    s, user = service
    first = s.create(user, recharge(), "recharge-order-key")
    assert first["state"] == "pending" and s.commerce.summary(user)["balance_cents"] == 0
    assert s.create(user, recharge(), "recharge-order-key")["id"] == first["id"]
    assert s.query(first["id"], user)["state"] == "pending"
    with pytest.raises(IdempotencyConflict):
        s.create(user, recharge(amount=6000), "recharge-order-key")
    assert s.simulate(first["id"], user)["state"] == "paid"
    s.simulate(first["id"], user)
    assert s.commerce.summary(user)["balance_cents"] == 5000 and len(s.commerce.ledger(user)) == 1
    assert PaymentService(s.accounts, s.settings).query(first["id"], user)["state"] == "paid"


def test_membership_orders_use_snapshot_not_new_price_or_browser_amount(service):
    s, user = service
    body = {
        "kind": "membership",
        "provider": "mock",
        "plan_id": "customer_annual",
        "amount_cents": None,
    }
    order = s.create(user, body, "member-order-key")
    assert order["amount_cents"] == 3000
    s.commerce.configure_plan(
        "customer_annual",
        name="Annual",
        audience="customer",
        price_cents=5000,
        period_months=6,
        entitlements={"personalized_recommendation": True},
    )
    s.simulate(order["id"], user)
    summary = s.commerce.summary(user)
    assert summary["entitlements"]["personalized_recommendation"] and summary["balance_cents"] == 0
    assert summary["subscriptions"][0]["ends"] - summary["subscriptions"][0]["starts"] > 360 * 86400
    with pytest.raises(CommerceError):
        s.create(user, {**body, "amount_cents": 1}, "override-price-key")


def test_merchant_role_boundary_and_free_server_configured_membership(service):
    s, user = service
    s.commerce.configure_plan(
        "business",
        name="Business",
        audience="merchant",
        price_cents=8000,
        period_months=1,
        entitlements={"garment_upload": 20},
    )
    with pytest.raises(CommerceError):
        s.create(
            user,
            {"kind": "membership", "provider": "mock", "plan_id": "business"},
            "merchant-plan-key",
        )
    s.commerce.configure_plan(
        "trial",
        name="Trial",
        audience="customer",
        price_cents=0,
        period_months=1,
        entitlements={"personalized_recommendation": True},
    )
    free = s.create(user, {"kind": "membership", "plan_id": "trial"}, "free-plan-order")
    assert free["provider"] == "free" and free["state"] == "paid"


def test_wrong_amount_currency_app_merchant_or_transaction_cannot_fulfill(service):
    s, user = service
    order = s.create(user, recharge(), "verified-order-key")
    event = VerifiedPayment(
        "mock",
        order["id"],
        "one-transaction",
        5000,
        "CNY",
        "local-development",
        "local-development",
    )
    for changed in (
        {"amount_cents": 1},
        {"currency": "USD"},
        {"app_id": "other"},
        {"merchant_id": "other"},
        {"provider": "wechat"},
    ):
        with pytest.raises(PaymentError):
            s.fulfill(replace(event, **changed))
    assert s.commerce.summary(user)["balance_cents"] == 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(result["state"] == "paid" for result in pool.map(s.fulfill, [event] * 10))
    assert s.commerce.summary(user)["balance_cents"] == 5000
    other = s.create(user, recharge(), "another-order-key")
    with pytest.raises(PaymentError):
        s.fulfill(replace(event, order_id=other["id"]))
    with pytest.raises(PaymentError):
        s.fulfill(replace(event, transaction_id="second-transaction"))


def test_created_order_resumes_but_uncertain_submission_does_not_retry(service, monkeypatch):
    s, user = service
    assert s.reserve(user, recharge(), "crash-before-submit")["state"] == "created"
    assert s.create(user, recharge(), "crash-before-submit")["state"] == "pending"
    calls = []

    def fail(*args):
        calls.append(True)
        raise PaymentError("Network uncertain")

    monkeypatch.setattr(s.providers["mock"], "create", fail)
    assert s.create(user, recharge(), "network-failure-key")["state"] == "uncertain"
    assert s.create(user, recharge(), "network-failure-key")["state"] == "uncertain"
    assert len(calls) == 1 and s.commerce.summary(user)["balance_cents"] == 0


def test_order_api_rejects_browser_success_override_and_cross_user_access(tmp_path):
    app = create_app(mock_settings(tmp_path), start_worker=False)
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.post("/api/account/orders", json=recharge()).status_code == 401
        fund_client(client)
        for invalid in (
            {**recharge(), "amount_cents": 50.0},
            {**recharge(), "amount_cents": True},
            {**recharge(), "paid": True},
        ):
            assert client.post("/api/account/orders", json=invalid).status_code == 422
        order = client.post("/api/account/orders", json=recharge()).json()
        assert order["state"] == "pending"
        assert client.post(
            f"/api/account/orders/{order['id']}/paid", json={"success": True}
        ).status_code in {404, 405}
        assert (
            client.post("/api/payments/callbacks/mock", content=b'{"paid":true}').status_code == 400
        )
        assert client.post(f"/api/account/orders/{order['id']}/mock-pay").json()["state"] == "paid"
        assert client.post(f"/api/account/orders/{order['id']}/refresh").json()["state"] == "paid"
        assert [e["kind"] for e in client.get("/api/account/ledger").json()["items"]].count(
            "recharge"
        ) == 2
        client.headers.pop("Authorization")
        client.post(
            "/api/auth/register",
            json={"name": "another", "display_name": "Another", "password": "another-password"},
        )
        token = client.post(
            "/api/auth/login", json={"name": "another", "password": "another-password"}
        ).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        assert client.get("/api/account/orders").json()["items"] == []
        assert client.get(f"/api/account/orders/{order['id']}").status_code == 404
        assert client.post(f"/api/account/orders/{order['id']}/refresh").status_code == 404
