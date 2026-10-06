"""Admin role migration, CLI-only creation and the read-only /api/admin console."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from itp.api import create_app
from itp.config import Settings
from itp.garments import MerchantStore

SECRET = "a" * 64
PASSWORD = "original-password"

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
    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data", jwt_secret=SECRET),
                     start_worker=False, config_path=tmp_path / ".env")
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


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
