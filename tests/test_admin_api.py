"""Admin role migration, CLI-only creation and the read-only /api/admin console."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.garments import MerchantStore
from itp.merchant_auth import encode_token, resolve_jwt_secret
from itp.payments import VerifiedPayment

SECRET = "a" * 64
PASSWORD = "original-password"
ADMIN_PATHS = ("/api/admin/status", "/api/admin/settings", "/api/admin/accounts",
               "/api/admin/usage", "/api/admin/orders", "/api/admin/jobs")

LEGACY_SCHEMA = (
    "CREATE TABLE merchants ("
    "id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, display_name TEXT, "
    "contact TEXT, password_hash TEXT, created REAL, "
    "disabled INTEGER DEFAULT 0, quota INTEGER, "
    "role TEXT NOT NULL DEFAULT 'merchant' CHECK (role IN ('customer', 'merchant')))"
)

PRE_ROLE_SCHEMA = (
    "CREATE TABLE merchants ("
    "id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, display_name TEXT, "
    "contact TEXT, password_hash TEXT, created REAL, "
    "disabled INTEGER DEFAULT 0, quota INTEGER)"
)


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                              environment="test", payment_mock_enabled=True,
                              payment_mock_secret="test-signing-secret-" * 3),
                     start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


def signup(client, role="customer", name="alice"):
    response = client.post("/api/auth/register", json={"name": name, "display_name": "测试账号",
        "password": PASSWORD, "role": role})
    assert response.status_code == 201, response.text
    login = client.post("/api/auth/login", json={"name": name, "password": PASSWORD})
    return response.json(), {"Authorization": "Bearer " + login.json()["access_token"]}


def admin_auth(client, name="admin"):
    store = client.app.state.merchants
    account = store.merchant_by_name(name) or create_admin(store, name=name)
    token, _ = encode_token(resolve_jwt_secret(client.app), account["id"], hours=12,
                            password_hash=account["password_hash"])
    return account, {"Authorization": "Bearer " + token}


def write_legacy_database(root, schema=LEGACY_SCHEMA):
    origin = root / "merchants.sqlite3"
    with sqlite3.connect(origin) as conn:
        conn.execute(schema)
        row = ("legacy-id", "old-shop", "老商家", "old@example.org", "scrypt$legacy", 1.5, 0, 4)
        if "role" in schema:
            conn.execute(
                "INSERT INTO merchants (id, name, display_name, contact, password_hash, "
                "created, disabled, quota, role) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'merchant')", row)
        else:
            conn.execute(
                "INSERT INTO merchants (id, name, display_name, contact, password_hash, "
                "created, disabled, quota) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
    return origin


def create_admin(store, name="admin", password="admin-password"):
    return store.create_merchant(
        name=name, display_name="平台管理员", contact="",
        password_hash="scrypt$admin", quota=0, role="admin")


def test_legacy_check_constraint_is_rebuilt_with_a_backup(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    write_legacy_database(root)
    store = MerchantStore(root)

    legacy = store.merchant("legacy-id")
    assert legacy["name"] == "old-shop"
    assert legacy["password_hash"] == "scrypt$legacy"
    assert legacy["role"] == "merchant"
    assert legacy["quota"] == 4
    with store.connect() as conn:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'merchants'").fetchone()[0]
    assert "'admin'" in schema
    assert (root / "merchants.sqlite3.bak").exists()
    assert create_admin(store)["role"] == "admin"


def test_admin_migration_is_idempotent(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    write_legacy_database(root)
    MerchantStore(root)
    second = MerchantStore(root)
    with second.connect() as conn:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'merchants'").fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0]
    assert "'admin'" in schema and count == 1
    # The rebuilt table still enforces the role CHECK.
    with pytest.raises(sqlite3.IntegrityError):
        with second.connect() as conn:
            conn.execute("UPDATE merchants SET role = 'superuser' WHERE id = 'legacy-id'")


def test_pre_role_table_gains_an_admin_capable_column(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    write_legacy_database(root, PRE_ROLE_SCHEMA)
    store = MerchantStore(root)
    legacy = store.merchant("legacy-id")
    assert legacy["role"] == "merchant"
    assert legacy["quota"] == 4
    assert not (root / "merchants.sqlite3.bak").exists()
    assert create_admin(store)["role"] == "admin"


def test_fresh_database_accepts_admin_without_backup(tmp_path):
    store = MerchantStore(tmp_path / "data")
    with store.connect() as conn:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'merchants'").fetchone()[0]
    assert "'admin'" in schema
    assert not (tmp_path / "data" / "merchants.sqlite3.bak").exists()
    assert create_admin(store)["role"] == "admin"


def test_create_merchant_rejects_unknown_roles(tmp_path):
    store = MerchantStore(tmp_path / "data")
    for role in ("superuser", "root", ""):
        with pytest.raises(ValueError):
            store.create_merchant(name="x", display_name="X", contact="",
                                  password_hash="h", quota=0, role=role)


def test_registration_api_still_refuses_admin(client):
    response = client.post("/api/auth/register", json={"name": "admin", "display_name": "管理员",
        "password": PASSWORD, "role": "admin"})
    assert response.status_code == 422


# ------------------------------------------------------------------ access control


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_endpoints_require_a_valid_session(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_endpoints_reject_customer_and_merchant_roles(client, path):
    _, customer = signup(client)
    _, merchant = signup(client, "merchant", name="shop")
    for auth in (customer, merchant):
        response = client.get(path, headers=auth)
        assert response.status_code == 403
        assert response.json()["detail"] == "需要管理员身份才能访问"


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_endpoints_answer_the_admin_role_with_no_store(client, path):
    _, auth = admin_auth(client)
    response = client.get(path, headers=auth)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"


def test_disabled_admin_loses_access(client):
    account, auth = admin_auth(client)
    client.app.state.merchants.set_disabled(account["id"], True)
    response = client.get("/api/admin/status", headers=auth)
    assert response.status_code == 403
    assert "禁用" in response.json()["detail"]


def test_admin_endpoints_are_hidden_from_the_public_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/api/admin") for path in paths)


# ------------------------------------------------------------------ status probe


def test_faceverse_probe_reports_unconfigured(client):
    _, auth = admin_auth(client)
    services = client.get("/api/admin/status", headers=auth).json()["services"]
    assert services["faceverse"] == {"configured": False, "reachable": False,
                                     "model": "faceverse-v4", "status": None,
                                     "cuda": None, "gpu": None}


def faceverse_app(tmp_path):
    return create_app(
        Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                 faceverse_endpoint="https://face.example.org/v1/face-refine",
                 faceverse_api_key="face-secret"),
        start_worker=False, config_path=tmp_path / ".env")


def test_faceverse_probe_reports_a_live_service(tmp_path, monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ready", "model": "faceverse-v4",
                                         "cuda": True, "gpu": "RTX 4090"})

    real_client = httpx.Client
    app = faceverse_app(tmp_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        _, auth = admin_auth(client)
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler), **kwargs))
        services = client.get("/api/admin/status", headers=auth).json()["services"]
    assert seen["url"] == "https://face.example.org/health"
    assert seen["authorization"] == "Bearer face-secret"
    assert services["faceverse"] == {"configured": True, "reachable": True,
                                     "model": "faceverse-v4", "status": "ready",
                                     "cuda": True, "gpu": "RTX 4090"}


def test_faceverse_probe_failure_never_breaks_the_status_page(tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    real_client = httpx.Client
    app = faceverse_app(tmp_path)
    with TestClient(app, base_url="http://localhost:8000") as client:
        _, auth = admin_auth(client)
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler), **kwargs))
        response = client.get("/api/admin/status", headers=auth)
    assert response.status_code == 200
    faceverse = response.json()["services"]["faceverse"]
    assert faceverse["configured"] is True and faceverse["reachable"] is False


# ------------------------------------------------------------ settings & accounts


def test_admin_settings_reports_secrets_as_booleans(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET,
                              tencent_secret_id="test-only-secret-id",
                              tencent_secret_key="test-only-secret-key",
                              pose_api_key="test-only-pose-key"),
                     start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        _, auth = admin_auth(client)
        response = client.get("/api/admin/settings", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["tencent_secret_id_set"] is True
    assert body["tencent_secret_key_set"] is True
    assert body["pose_api_key_set"] is True
    assert body["tencent_model"]
    for secret in ("test-only-secret-id", "test-only-secret-key", "test-only-pose-key"):
        assert secret not in response.text


# ------------------------------------------------------------------ platform data


def test_admin_dashboards_aggregate_every_account(client):
    store = client.app.state.merchants
    payments = client.app.state.payments
    commerce = payments.commerce
    alice = store.create_merchant(name="alice", display_name="Alice", contact="",
                                  password_hash="hash", quota=0, role="customer")
    bob = store.create_merchant(name="bob", display_name="Bob", contact="",
                                password_hash="hash", quota=0, role="merchant")
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        commerce.credit_verified_order(conn, alice["id"], 10000, "alice-recharge")
        commerce.credit_verified_order(conn, bob["id"], 4000, "bob-recharge")

    paid = payments.create(alice["id"], {"kind": "recharge", "provider": "mock",
                                         "amount_cents": 5000, "plan_id": None}, "alice-order-key")
    payments.fulfill(VerifiedPayment("mock", paid["id"], "alice-transaction", 5000, "CNY",
                                     "local-development", "local-development"))
    payments.create(bob["id"], {"kind": "recharge", "provider": "mock", "amount_cents": 700,
                                "plan_id": None}, "bob-order-key")

    with store.connect() as conn:
        commerce.benefits.ledger_change(conn, alice["id"], 1600, "test_grant", "alice-points")
        commerce.benefits.ledger_change(conn, bob["id"], 1600, "test_grant", "bob-points")
    price = 0
    completed = commerce.reserve_model(alice["id"], "alice-charge-key", {"name": "alice"})
    commerce.finish_model(completed["job_id"], succeeded=True, valid_result=True)
    refunded = commerce.reserve_model(bob["id"], "bob-charge-key", {"name": "bob"})
    commerce.finish_model(refunded["job_id"], succeeded=False, valid_result=False)

    store.create_garment(bob["id"], {"name": "测试商品", "status": "draft"})
    for owner in (alice["id"], bob["id"]):
        client.app.state.store.create_job({"name": "aggregate-job"}, owner_id=owner)

    _, auth = admin_auth(client)
    usage = client.get("/api/admin/usage", headers=auth).json()
    assert usage["model_charges"]["completed"] == {"count": 1, "amount_cents": price}
    assert usage["model_charges"]["refunded"] == {"count": 1, "amount_cents": price}
    assert usage["wallets"] == {"count": 2,
                                "total_balance_cents": 10000 + 5000 - price + 4000}
    assert usage["garments"]["total"] == 1 and usage["garments"]["draft"] == 1
    assert usage["orders"]["paid"] == {"count": 1, "amount_cents": 5000}
    assert usage["orders"]["pending"]["count"] == 1
    assert {row["account_name"] for row in usage["recent_ledger"]} == {"alice", "bob"}

    accounts = client.get("/api/admin/accounts", headers=auth).json()
    assert accounts["total"] == 3  # alice, bob and the console's own admin account
    by_name = {item["name"]: item for item in accounts["items"]}
    assert by_name["alice"]["role"] == "customer" and by_name["alice"]["garment_count"] == 0
    assert by_name["bob"]["garment_count"] == 1 and by_name["bob"]["disabled"] is False
    assert by_name["admin"]["role"] == "admin"
    assert "password_hash" not in client.get("/api/admin/accounts", headers=auth).text

    orders = client.get("/api/admin/orders", headers=auth).json()
    assert orders["total"] == 2
    assert {row["account_name"] for row in orders["items"]} == {"alice", "bob"}

    jobs = client.get("/api/admin/jobs", headers=auth).json()
    assert {job["owner_id"] for job in jobs["jobs"]} == {alice["id"], bob["id"]}
    assert jobs["tryons"] == [] and jobs["face_refinements"] == []
    assert "没有历史任务" in jobs["note"]


def test_admin_pagination_bounds_are_validated(client):
    _, auth = admin_auth(client)
    for path in ("/api/admin/accounts", "/api/admin/orders"):
        assert client.get(path, headers=auth, params={"limit": 0}).status_code == 422
        assert client.get(path, headers=auth, params={"offset": -1}).status_code == 422
    assert client.get("/api/admin/accounts", headers=auth,
                      params={"limit": 501}).status_code == 422
    assert client.get("/api/admin/orders", headers=auth,
                      params={"limit": 201}).status_code == 422


CLI = Path(__file__).resolve().parents[1] / "scripts" / "create_admin.py"


def run_cli(cwd, extra_env, *args):
    """Run the real create_admin.py with a clean ITP_DATA_DIR, never prompting."""
    env = {key: value for key, value in os.environ.items() if key != "ITP_DATA_DIR"}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(CLI), *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=120)


def test_create_admin_cli_reads_the_data_dir_from_the_app_env(tmp_path):
    """configure.py stores ITP_DATA_DIR in the app .env; the documented CLI command must find it."""
    data = tmp_path / "itp-data"
    MerchantStore(data)  # create the database like a first app startup
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / ".env").write_text(f"ITP_DATA_DIR={data}\n", encoding="utf-8")

    created = run_cli(app_root, None, "--name", "ops", "--password", "cli-password")

    assert created.returncode == 0, created.stderr
    account = MerchantStore(data).merchant_by_name("ops")
    assert account["role"] == "admin"
    assert "已创建管理员账号" in created.stdout

    duplicate = run_cli(app_root, None, "--name", "ops", "--password", "cli-password")
    assert duplicate.returncode == 1
    assert "已存在" in duplicate.stdout


def test_create_admin_cli_honours_the_itp_data_dir_variable(tmp_path):
    data = tmp_path / "itp-data"
    MerchantStore(data)

    created = run_cli(tmp_path, {"ITP_DATA_DIR": str(data)},
                      "--name", "ops", "--password", "cli-password")

    assert created.returncode == 0, created.stderr
    assert MerchantStore(data).merchant_by_name("ops")["role"] == "admin"


def test_create_admin_cli_accepts_an_explicit_data_dir(tmp_path):
    data = tmp_path / "itp-data"
    MerchantStore(data)

    created = run_cli(tmp_path, None, "--data-dir", str(data),
                      "--name", "ops", "--password", "cli-password")

    assert created.returncode == 0, created.stderr
    assert MerchantStore(data).merchant_by_name("ops")["role"] == "admin"
