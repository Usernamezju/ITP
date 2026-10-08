"""Manual collection codes: only an operator turns a claimed transfer into money.

The whole point of these tests is what the platform refuses to do.  A payer can
see the shop's own QR picture and can refresh the order as often as they like;
nothing they do credits a wallet, grants a membership or marks an order paid.
That happens exactly once, inside one transaction, when an administrator who
checked the real account confirms it.
"""

import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from itp.api import create_app
from itp.config import Settings
from itp.manual_qr import MAX_QR_UPLOAD, ManualQrStore, render_qr
from itp.payment_service import PaymentService
from itp.payments import MANUAL_CHANNELS, PaymentError, manual_qr_dir

SECRET = "m" * 64
PASSWORD = "manual-payment-password"


def png_bytes(size=(320, 320), color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                    environment="test")


def upload_codes(client, settings, *, wechat=True, alipay=True, enabled=True):
    """Put real pictures on disk and switch the channel on, as the console does."""
    store = ManualQrStore(manual_qr_dir(settings))
    values = {"payment_manual_enabled": enabled}
    for channel, wanted in (("wechat", wechat), ("alipay", alipay)):
        if wanted:
            values[f"payment_manual_{channel}_qr"] = store.save(render_qr(png_bytes()))
    client.app.state.settings = Settings(
        _env_file=None, **{**client.app.state.settings.model_dump(), **values}
    )
    client.app.state.payments.reload(client.app.state.settings)
    return values


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                        environment="test", customer_membership_price_cents=3000)
    app = create_app(settings, start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client, settings


def signup(client, role="customer", name="alice"):
    assert client.post("/api/auth/register", json={
        "name": name, "display_name": "测试账号", "password": PASSWORD, "role": role,
    }).status_code == 201
    login = client.post("/api/auth/login", json={"name": name, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def admin_token(client, name="ops"):
    store = client.app.state.merchants
    account = store.merchant_by_name(name) or store.create_merchant(
        name=name, display_name="平台管理员", contact="", password_hash="scrypt$admin",
        quota=0, role="admin")
    from itp.merchant_auth import encode_token, resolve_jwt_secret
    token, _ = encode_token(resolve_jwt_secret(client.app), account["id"], hours=12,
                            password_hash=account["password_hash"])
    return {"Authorization": "Bearer " + token}


def auth(token):
    return {"Authorization": "Bearer " + token}


def create_order(client, token, *, provider="manual_wechat", kind="recharge", amount=5000):
    response = client.post("/api/account/orders", headers={
        **auth(token), "Idempotency-Key": f"manual-order-{kind}-{amount}-{provider}",
        "Content-Type": "application/json",
    }, json={"kind": kind, "provider": provider,
             **({"amount_cents": amount} if kind == "recharge" else {"plan_id": "customer_monthly"})})
    assert response.status_code == 201, response.text
    return response.json()


def ready_methods(client):
    """The channels the customer page may offer: exactly the ready ones."""
    methods = client.get("/api/payments/methods").json()["methods"]
    return [method for method in methods if method["ready"]]


def balance(client, user_id):
    with client.app.state.merchants.connect() as conn:
        row = conn.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else 0


def account_id(client, name="alice"):
    return client.app.state.merchants.merchant_by_name(name)["id"]


def test_the_payer_sees_the_uploaded_code_and_nothing_is_credited(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token)

    # The existing checkout contract: an image the current frontend already shows.
    assert order["state"] == "pending"
    assert order["provider"] == "manual_wechat"
    assert order["checkout"]["qr_image"].startswith("/api/payments/manual/qr/")
    image = client.get(order["checkout"]["qr_image"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, no-store"

    # Refreshing reads the order back; it can never become paid by itself.
    for _ in range(3):
        refreshed = client.post(f"/api/account/orders/{order['id']}/refresh", headers=auth(token))
        assert refreshed.status_code == 200
        assert refreshed.json()["state"] == "pending"
    assert balance(client, account_id(client)) == 0
    with client.app.state.merchants.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payment_transactions").fetchone()[0] == 0


def test_a_payer_cannot_confirm_their_own_transfer(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token)
    for path in (f"/api/admin/payments/manual/orders/{order['id']}/confirm",
                 f"/api/admin/payments/manual/orders/{order['id']}/reject"):
        assert client.post(path, headers=auth(token)).status_code in (401, 403)
        assert client.post(path).status_code == 401
    assert client.get("/api/admin/payments/manual/orders",
                      headers=auth(token)).status_code in (401, 403)
    assert client.get("/api/admin/payments/manual/orders").status_code == 401
    assert balance(client, account_id(client)) == 0


def test_an_admin_confirms_and_the_wallet_is_credited_once(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token, amount=12345)
    admin = admin_token(client)

    pending = client.get("/api/admin/payments/manual/orders", headers=admin).json()
    assert pending["total"] == 1
    item = pending["items"][0]
    assert item["id"] == order["id"] and item["account_name"] == "alice"
    assert item["amount_cents"] == 12345 and item["kind"] == "recharge"
    assert item["state"] == "pending" and item["created"] > 0

    confirmed = client.post(f"/api/admin/payments/manual/orders/{order['id']}/confirm",
                            headers=admin)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "paid"
    assert confirmed.json()["paid_at"] is not None
    assert balance(client, account_id(client)) == 12345

    with client.app.state.merchants.connect() as conn:
        rows = conn.execute(
            "SELECT provider, transaction_id, order_id, amount_cents FROM payment_transactions"
        ).fetchall()
    assert len(rows) == 1
    provider, transaction_id, order_id, amount = rows[0]
    assert provider == "manual_wechat" and order_id == order["id"] and amount == 12345
    assert transaction_id == PaymentService.manual_transaction_id(order["id"])
    assert transaction_id.startswith("manual_") and len(transaction_id) > len("manual_")

    # The payer's own view catches up on the next poll, with no extra call.
    refreshed = client.post(f"/api/account/orders/{order['id']}/refresh", headers=auth(token))
    assert refreshed.json()["state"] == "paid"

    # Confirming twice is the same confirmation: no second credit, no error.
    again = client.post(f"/api/admin/payments/manual/orders/{order['id']}/confirm", headers=admin)
    assert again.status_code == 200
    assert again.json()["state"] == "paid"
    assert balance(client, account_id(client)) == 12345
    with client.app.state.merchants.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payment_transactions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM wallet_ledger WHERE kind='recharge'").fetchone()[0] == 1
    assert client.get("/api/admin/payments/manual/orders", headers=admin).json()["total"] == 0


def test_a_membership_is_granted_by_the_same_confirmation(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token, provider="manual_alipay", kind="membership")
    admin = admin_token(client)

    confirmed = client.post(f"/api/admin/payments/manual/orders/{order['id']}/confirm", headers=admin)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "paid"
    user_id = account_id(client)
    with client.app.state.merchants.connect() as conn:
        subscription = conn.execute(
            "SELECT user_id, plan_id, order_id FROM subscriptions WHERE order_id=?",
            (order["id"],),
        ).fetchone()
    assert subscription is not None and subscription[0] == user_id
    assert subscription[2] == order["id"]
    # A membership order never touches the wallet.
    assert balance(client, user_id) == 0


def test_a_rejected_order_is_never_credited_and_loses_its_code(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token)
    admin = admin_token(client)

    rejected = client.post(f"/api/admin/payments/manual/orders/{order['id']}/reject", headers=admin)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["checkout"] is None
    assert balance(client, account_id(client)) == 0
    assert client.get("/api/admin/payments/manual/orders", headers=admin).json()["total"] == 0

    # A refused order cannot be confirmed afterwards.
    confirmed = client.post(f"/api/admin/payments/manual/orders/{order['id']}/confirm", headers=admin)
    assert confirmed.status_code == 409
    assert balance(client, account_id(client)) == 0
    # And a paid order cannot be rejected.
    other = create_order(client, token, amount=700)
    assert client.post(f"/api/admin/payments/manual/orders/{other['id']}/confirm",
                       headers=admin).status_code == 200
    assert client.post(f"/api/admin/payments/manual/orders/{other['id']}/reject",
                       headers=admin).status_code == 409


def test_turning_manual_collection_off_stops_new_orders(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    first = create_order(client, token)
    assert client.post("/api/account/orders", headers={
        **auth(token), "Idempotency-Key": "second-manual-order",
        "Content-Type": "application/json",
    }, json={"kind": "recharge", "provider": "manual_wechat", "amount_cents": 100,
             "plan_id": None}).status_code == 201

    upload_codes(client, settings, enabled=False)
    assert ready_methods(client) == []

    refused = client.post("/api/account/orders", headers={
        **auth(token), "Idempotency-Key": "third-manual-order",
        "Content-Type": "application/json",
    }, json={"kind": "recharge", "provider": "manual_wechat", "amount_cents": 100,
             "plan_id": None})
    assert refused.status_code == 503
    assert "支付方式" in refused.json()["detail"]

    # The orders placed while it was on stay confirmable: the money was sent.
    admin = admin_token(client)
    assert client.get("/api/admin/payments/manual/orders", headers=admin).json()["total"] == 2
    assert client.post(f"/api/admin/payments/manual/orders/{first['id']}/confirm",
                       headers=admin).status_code == 200


def test_the_console_uploads_a_code_and_never_reports_a_secret(env):
    client, settings = env
    admin = admin_token(client)
    upload = client.post("/api/admin/payments/manual/qr", headers=admin,
                         data={"channel": "wechat"},
                         files={"file": ("code.png", png_bytes(), "image/png")})
    assert upload.status_code == 200, upload.text
    document = upload.json()
    manual = document["manual"]
    assert manual["channels"]["manual_wechat"]["qr_set"] is True
    assert manual["channels"]["manual_wechat"]["updated"] > 0
    assert manual["channels"]["manual_alipay"]["qr_set"] is False
    # Enabled by default? No: the switch is the operator's, and the picture alone
    # does not offer the channel to payers.
    assert manual["enabled"] is False
    assert ready_methods(client) == []

    key = manual["channels"]["manual_wechat"]["qr_key"]
    assert (settings.data_dir / "payment" / "manual" / key).is_file()
    # The settings file stores the file name, never the picture.
    assert "#" not in key and len(key) == 36

    enabled = client.post("/api/admin/payments/config", headers=admin, json={
        "payment_manual_enabled": True})
    assert enabled.status_code == 200, enabled.text
    methods = ready_methods(client)
    assert [method["id"] for method in methods] == ["manual_wechat"]
    assert "人工确认" in methods[0]["name"]

    cleared = client.post("/api/admin/payments/config", headers=admin, json={
        "payment_manual_clear": "wechat"})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["settings"]["manual"]["wechat_qr_set"] is False
    assert not (settings.data_dir / "payment" / "manual" / key).exists()
    assert ready_methods(client) == []


def test_upload_rules_and_permissions(env):
    client, settings = env
    admin = admin_token(client)
    token = signup(client)
    for headers in ({}, auth(token)):
        response = client.post("/api/admin/payments/manual/qr", headers=headers,
                               data={"channel": "wechat"},
                               files={"file": ("code.png", png_bytes(), "image/png")})
        assert response.status_code in (401, 403)
    assert client.post("/api/admin/payments/manual/qr", headers=admin,
                       data={"channel": "paypal"},
                       files={"file": ("code.png", png_bytes(), "image/png")}
                       ).status_code == 422
    assert client.post("/api/admin/payments/manual/qr", headers=admin,
                       data={"channel": "wechat"},
                       files={"file": ("code.png", b"not an image", "image/png")}
                       ).status_code == 422
    too_big = client.post("/api/admin/payments/manual/qr", headers=admin,
                          data={"channel": "wechat"},
                          files={"file": ("code.png", b"x" * (MAX_QR_UPLOAD + 1), "image/png")})
    assert too_big.status_code == 422
    # Nothing was stored by any of the refused uploads.
    assert not list((settings.data_dir / "payment" / "manual").glob("*.png")) \
        if (settings.data_dir / "payment" / "manual").exists() else True


def test_a_callback_cannot_claim_a_manual_order_was_paid(env):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token)
    for provider in MANUAL_CHANNELS:
        response = client.post(f"/api/payments/callbacks/{provider}",
                               json={"order_id": order["id"], "transaction_id": "forged",
                                     "amount_cents": order["amount_cents"]})
        assert response.status_code == 422
    assert client.get(f"/api/account/orders/{order['id']}", headers=auth(token)).json()["state"] == "pending"
    assert balance(client, account_id(client)) == 0


def test_manual_orders_are_isolated_per_account_and_per_channel(env):
    client, settings = env
    upload_codes(client, settings)
    first = signup(client, name="alice")
    second = signup(client, name="bob")
    mine = create_order(client, first, provider="manual_wechat")
    theirs = create_order(client, second, provider="manual_alipay", amount=900)

    admin = admin_token(client)
    pending = client.get("/api/admin/payments/manual/orders", headers=admin).json()
    assert [item["id"] for item in pending["items"]] == [theirs["id"], mine["id"]]
    assert {item["provider"] for item in pending["items"]} == set(MANUAL_CHANNELS)

    # One account's order is invisible to another, and the credit follows the
    # order's own owner — never the administrator's.
    assert client.get(f"/api/account/orders/{mine['id']}", headers=auth(second)).status_code == 404
    assert client.post(f"/api/admin/payments/manual/orders/{mine['id']}/confirm",
                       headers=admin).status_code == 200
    assert balance(client, account_id(client, "alice")) == 5000
    assert balance(client, account_id(client, "bob")) == 0
    assert balance(client, account_id(client, "ops")) == 0


def test_the_credited_amount_comes_from_the_order_never_from_the_caller(env):
    """The confirmation endpoint takes no amount: the order row decides."""
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token, amount=4200)
    admin = admin_token(client)

    confirmed = client.post(
        f"/api/admin/payments/manual/orders/{order['id']}/confirm",
        headers={**admin, "Content-Type": "application/json"},
        json={"amount_cents": 999999, "state": "paid"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert balance(client, account_id(client)) == 4200
    with client.app.state.merchants.connect() as conn:
        amount = conn.execute(
            "SELECT amount_cents FROM payment_transactions WHERE order_id=?", (order["id"],)
        ).fetchone()[0]
    assert amount == 4200


def test_fulfilment_is_atomic_when_the_database_is_busy(env, monkeypatch):
    client, settings = env
    upload_codes(client, settings)
    token = signup(client)
    order = create_order(client, token, amount=2500)
    admin = admin_token(client)

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(client.app.state.merchants.commerce, "credit_verified_order", unavailable)
    response = client.post(f"/api/admin/payments/manual/orders/{order['id']}/confirm", headers=admin)
    assert response.status_code == 503
    assert "稍后重试" in response.json()["detail"]
    # Nothing half-done: no credit, no transaction row, still pending.
    assert balance(client, account_id(client)) == 0
    with client.app.state.merchants.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payment_transactions").fetchone()[0] == 0
    assert client.get(f"/api/account/orders/{order['id']}", headers=auth(token)).json()["state"] != "paid"
