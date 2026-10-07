"""A customer turns their own account into a shop without losing anything."""

import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.merchant_auth import encode_token, hash_password, resolve_jwt_secret

SECRET = "a" * 64
PASSWORD = "upgrade-password-123"


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                 environment="test"),
        start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.post("/api/auth/register", json={"name": "shopper", "display_name": "老顾客",
                                                "contact": "13800000000",
                                                "password": PASSWORD, "role": "customer"})
        login = client.post("/api/auth/login", json={"name": "shopper", "password": PASSWORD})
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        yield client


def upgrade(client, body=None):
    return client.post("/api/account/merchant", json=body if body is not None else {
        "display_name": "老顾客的小店", "contact": "13900000000"})


def test_upgrading_keeps_the_account_and_opens_the_merchant_api(client):
    store = client.app.state.merchants
    before = client.get("/api/account/me").json()
    assert before["role"] == "customer"
    assert client.get("/api/merchant/me").status_code == 403

    response = upgrade(client)
    assert response.status_code == 200, response.text
    upgraded = response.json()
    assert upgraded["id"] == before["id"]          # the same account, not a new shop
    assert upgraded["role"] == "merchant"
    assert upgraded["display_name"] == "老顾客的小店"
    assert upgraded["contact"] == "13900000000"
    assert store.merchant(before["id"])["role"] == "merchant"
    # The login never changed, so the browser keeps its session.
    assert store.merchant_by_name("shopper")["id"] == before["id"]

    # The token it already held now opens the shop endpoints.
    profile = client.get("/api/merchant/me").json()
    assert profile["display_name"] == "老顾客的小店"
    assert profile["garment_count"] == 0
    assert profile["upload_usage"]["limit"] == 5   # the merchant free plan
    # …and every customer endpoint still works.
    assert client.get("/api/account/commerce").status_code == 200
    assert client.get("/api/account/orders").status_code == 200
    assert client.get("/api/account/ledger").status_code == 200


def test_upgrading_keeps_the_balance_and_refuses_a_second_time(client):
    store = client.app.state.merchants
    user_id = client.get("/api/account/me").json()["id"]
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        store.commerce.credit_verified_order(conn, user_id, 12345, "upgrade-test-credit")
    assert client.get("/api/account/commerce").json()["balance_cents"] == 12345

    assert upgrade(client).status_code == 200
    assert client.get("/api/account/commerce").json()["balance_cents"] == 12345
    assert len(client.get("/api/account/ledger").json()["items"]) == 1

    repeated = upgrade(client)
    assert repeated.status_code == 409
    assert "已经是商家" in repeated.json()["detail"]


def test_only_a_signed_in_customer_may_upgrade(client):
    anonymous = TestClient(client.app, base_url="http://localhost:8000")
    assert anonymous.post("/api/account/merchant", json={}).status_code == 401

    store = client.app.state.merchants
    admin = store.merchant_by_name("ops") or store.create_merchant(
        name="ops", display_name="平台管理员", contact="",
        password_hash=hash_password("admin-password"), quota=0, role="admin")
    token, _ = encode_token(resolve_jwt_secret(client.app), admin["id"], hours=12,
                            password_hash=admin["password_hash"])
    refused = client.post("/api/account/merchant", json={"display_name": "ops 的小店"},
                          headers={"Authorization": "Bearer " + token})
    assert refused.status_code == 403
    assert store.merchant(admin["id"])["role"] == "admin"


def test_the_shop_profile_is_validated_before_any_change(client):
    for body, message in (
        ({"display_name": ""}, "1-40"),
        ({"display_name": "x" * 41}, "1-40"),
        ({"display_name": "店\u0000名"}, "不可见控制字符"),
        ({"display_name": "正常", "contact": "1" * 81}, "80"),
    ):
        response = client.post("/api/account/merchant", json=body)
        assert response.status_code == 422, body
        assert message in response.json()["detail"]
    assert client.get("/api/account/me").json()["role"] == "customer"
    assert client.get("/api/merchant/me").status_code == 403


def test_an_empty_body_keeps_the_current_profile_values(client):
    response = client.post("/api/account/merchant", json={})
    assert response.status_code == 200
    assert response.json()["display_name"] == "老顾客"
    assert response.json()["contact"] == "13800000000"


def test_one_upgrade_never_touches_another_account(client):
    store = client.app.state.merchants
    other = store.create_merchant(name="bystander", display_name="路人", contact="",
                                  password_hash=hash_password(PASSWORD), quota=0,
                                  role="customer")
    assert upgrade(client).status_code == 200
    assert store.merchant(other["id"])["role"] == "customer"
