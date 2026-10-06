"""Unified customers and merchants share the existing credential/JWT implementation."""

import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.garments import MerchantStore
from itp.merchant_auth import decode_token, encode_token, hash_password, verify_password

SECRET = "a" * 64
PASSWORD = "original-password"


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET),
                     start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


def signup(client, role="customer", name="alice"):
    response = client.post("/api/auth/register", json={"name": name, "display_name": "测试账号",
        "password": PASSWORD, "role": role})
    assert response.status_code == 201, response.text
    token = client.post("/api/auth/login", json={"name": name, "password": PASSWORD}).json()["access_token"]
    return response.json(), {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("role", ["customer", "merchant"])
def test_register_login_profile_and_existing_password_crypto(client, role):
    user, auth = signup(client, role)
    assert user["role"] == role
    stored = client.app.state.merchants.merchant(user["id"])
    assert stored["password_hash"].startswith("scrypt$")
    assert verify_password(PASSWORD, stored["password_hash"])
    response = client.get("/api/account/me", headers=auth)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == user
    assert "password" not in response.text
    assert stored["password_hash"] not in response.text


def test_customers_cannot_enter_any_merchant_business_route(client):
    _, auth = signup(client)
    for path in ("/api/merchant/me", "/api/merchant/garments", "/api/merchant/looks"):
        assert client.get(path, headers=auth).status_code == 403
    assert client.post("/api/merchant/password", headers=auth,
        json={"current_password": PASSWORD, "new_password": "second-password"}).status_code == 403
    assert client.post("/api/merchant/login", json={"name": "alice", "password": PASSWORD}).status_code == 403


def test_merchant_legacy_and_unified_logins_use_same_account(client):
    user, auth = signup(client, "merchant")
    assert client.get("/api/merchant/me", headers=auth).json()["merchant_id"] == user["id"]
    legacy = client.post("/api/merchant/login", json={"name": "alice", "password": PASSWORD})
    assert legacy.status_code == 200
    claims = decode_token(SECRET, legacy.json()["access_token"])
    assert claims["sub"] == user["id"]
    assert client.post("/api/merchant/register", json={"name": "alice", "password": PASSWORD,
        "display_name": "重复商家"}).status_code == 409


def test_logout_revokes_only_this_token_and_survives_restart(client):
    user, auth = signup(client)
    other = client.post("/api/auth/login", json={"name": "alice", "password": PASSWORD}).json()["access_token"]
    token = auth["Authorization"].removeprefix("Bearer ")
    assert other != token
    assert client.post("/api/auth/logout", headers=auth).status_code == 200
    assert client.get("/api/account/me", headers=auth).status_code == 401
    assert client.get("/api/account/me", headers={"Authorization": "Bearer " + other}).status_code == 200
    root = client.app.state.merchants.root
    reopened = MerchantStore(root)
    assert reopened.token_revoked(hashlib.sha256(token.encode()).hexdigest())
    with reopened.connect() as conn:
        row = conn.execute("SELECT token_hash, user_id FROM auth_revocations").fetchone()
    assert row == (hashlib.sha256(token.encode()).hexdigest(), user["id"])
    assert token != row[0]


def test_profile_updates_are_private_and_role_is_not_editable(client):
    user, auth = signup(client)
    other, other_auth = signup(client, "merchant", name="other")
    result = client.patch("/api/account/me", headers=auth,
                          json={"display_name": "新昵称", "contact": "new@example.org"})
    assert result.status_code == 200
    assert result.json()["id"] == user["id"]
    assert client.get("/api/account/me", headers=other_auth).json()["display_name"] == other["display_name"]
    for body in ({"role": "merchant"}, {"id": other["id"]}, {"password_hash": "changed"},
                 {"display_name": None}, {"display_name": ""}, {"contact": "x\ny"}):
        assert client.patch("/api/account/me", headers=auth, json=body).status_code == 422
    assert client.get("/api/account/me", headers=auth).json()["role"] == "customer"


def test_password_change_revokes_all_user_sessions(client):
    _, auth = signup(client)
    second = client.post("/api/auth/login", json={"name": "alice", "password": PASSWORD}).json()["access_token"]
    assert client.post("/api/account/password", headers=auth,
        json={"current_password": "wrong", "new_password": "second-password"}).status_code == 401
    assert client.post("/api/account/password", headers=auth,
        json={"current_password": PASSWORD, "new_password": "second-password"}).status_code == 200
    for token in (auth["Authorization"], "Bearer " + second):
        assert client.get("/api/account/me", headers={"Authorization": token}).status_code == 401
    assert client.post("/api/auth/login", json={"name": "alice", "password": "second-password"}).status_code == 200


def test_registration_rejects_admin_and_login_is_throttled(client):
    assert client.post("/api/auth/register", json={"name": "admin", "display_name": "管理员",
        "password": PASSWORD, "role": "admin"}).status_code == 422
    for _ in range(8):
        assert client.post("/api/auth/login", json={"name": "missing", "password": PASSWORD}).status_code == 401
    denied = client.post("/api/merchant/login", json={"name": "missing", "password": PASSWORD})
    assert denied.status_code == 429
    assert int(denied.headers["retry-after"]) > 0


def test_password_values_are_not_reflected_in_validation_errors(client):
    response = client.post("/api/auth/register", json={"name": "alice", "display_name": "Alice",
        "password": PASSWORD, "role": "admin", "api_key": "sensitive-input"})
    assert response.status_code == 422
    assert PASSWORD not in response.text and "sensitive-input" not in response.text


def test_public_login_does_not_use_ephemeral_signing_secret(tmp_path):
    unwritable = tmp_path / "env-directory"
    unwritable.mkdir()
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data",
                              public_origin="https://example.org"),
                     start_worker=False, config_path=unwritable)
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.post("/api/auth/register", json={"name": "alice", "display_name": "Alice", "password": PASSWORD})
        assert response.status_code == 201
        assert client.post("/api/auth/login", json={"name": "alice", "password": PASSWORD}).status_code == 503


def test_legacy_migration_preserves_id_credentials_and_products(tmp_path):
    path = tmp_path / "merchants.sqlite3"
    stored = hash_password(PASSWORD)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE merchants (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, "
            "display_name TEXT, contact TEXT, password_hash TEXT, created REAL, disabled INTEGER, quota INTEGER)")
        conn.execute("INSERT INTO merchants VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("legacy", "old-shop", "老商家", "", stored, 1.0, 0, 200))
    store = MerchantStore(tmp_path)
    user = store.merchant("legacy")
    assert user["role"] == "merchant" and user["password_hash"] == stored
    assert verify_password(PASSWORD, user["password_hash"])
    assert MerchantStore(tmp_path).merchant("legacy") == user
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM merchants").fetchone()[0] == 1


def test_exact_expiry_and_non_ascii_tokens_are_rejected():
    token, lifetime = encode_token(SECRET, "alice", hours=1, now=10)
    with pytest.raises(ValueError):
        decode_token(SECRET, token, now=10 + lifetime)
    with pytest.raises(ValueError):
        decode_token(SECRET, "é.é.é")
