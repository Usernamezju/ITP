from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.garments import MerchantStore
from itp.phones import PhoneStore


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path, jwt_secret="s" * 64),
                     start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost") as client:
        yield client


def register(client, name, phone=""):
    return client.post("/api/auth/register", json={"name": name, "display_name": name,
                        "password": "test-password", "phone": phone})


def login(client, name):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={
        "name": name, "password": "test-password"}).json()["access_token"]}


def test_unverified_unique_format_legacy_login_and_reopen(client):
    assert register(client, "invalid", "123").status_code == 422
    response = register(client, "alice", "13800000000")
    assert response.status_code == 201, response.text
    assert response.json()["phone_verified"] is False
    assert register(client, "other", "13800000000").status_code == 409
    assert client.app.state.merchants.merchant_by_name("other") is None
    assert register(client, "legacy").status_code == 201
    assert client.get("/api/account/me", headers=login(client, "legacy")).status_code == 200
    reopened = MerchantStore(client.app.state.merchants.root)
    assert reopened.phones.profile(response.json()["id"])["phone"] == "13800000000"
    assert client.put("/api/account/phone", headers=login(client, "legacy"),
                      json={"phone": "13900000000"}).status_code == 200


def test_verification_single_use_owner_expiry_and_attempt_limit(client):
    accounts = client.app.state.merchants
    sent = []
    phones = PhoneStore(accounts, sender=lambda phone, code: sent.append((phone, code)))
    challenge = phones.send("alice", "13800000000", "bind", now=100)
    key, code = challenge["challenge_id"], sent[-1][1]
    with pytest.raises(ValueError):
        phones.check("other", "13800000000", "bind", key, code, now=101)
    with pytest.raises(ValueError):
        phones.check("alice", "13800000000", "bind", key, code, now=400)
    for _ in range(5):
        with pytest.raises(ValueError):
            phones.check("alice", "13800000000", "bind", key, "wrong", now=101)
    with pytest.raises(ValueError):
        phones.check("alice", "13800000000", "bind", key, code, now=101)
    assert register(client, "alice").status_code == 201
    user = accounts.merchant_by_name("alice")
    phones.limiter = type(phones.limiter)()
    new = phones.send(user["id"], "13800000000", "bind")
    proof = phones.check(user["id"], "13800000000", "bind", new["challenge_id"], sent[-1][1])
    with accounts.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        phones.bind(conn, user["id"], "13800000000", proof=proof)
    assert phones.profile(user["id"])["phone_verified"]
    with pytest.raises(ValueError), accounts.connect() as conn:
        phones.bind(conn, user["id"], "13900000000", proof=proof)
    with pytest.raises(ValueError):
        phones.check(user["id"], "13800000000", "bind", proof, sent[-1][1])


def test_concurrent_bind_cannot_claim_same_phone(client):
    users = [register(client, name).json() for name in ("alice", "other")]
    phones = client.app.state.merchants.phones
    def bind(user):
        try:
            with phones.accounts.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                phones.bind(conn, user["id"], "13800000000")
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(bind, users)) == 1


def test_verified_owner_protected_and_unverified_claim_recoverable(client):
    accounts = client.app.state.merchants
    first = register(client, "first", "13800000000").json()
    second = register(client, "second").json()
    sent = []
    phones = PhoneStore(accounts, sender=lambda phone, code: sent.append(code))
    challenge = phones.send(second["id"], "13800000000", "bind")
    proof = phones.check(second["id"], "13800000000", "bind", challenge["challenge_id"], sent[-1])
    with accounts.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        phones.bind(conn, second["id"], "13800000000", proof=proof)
    assert phones.profile(first["id"])["phone"] is None
    with pytest.raises(ValueError), accounts.connect() as conn:
        phones.bind(conn, first["id"], "13800000000")
    with accounts.connect() as conn:
        assert sent[-1] not in str(conn.execute("SELECT * FROM sms_challenges").fetchall())
