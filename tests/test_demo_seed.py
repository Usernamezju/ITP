"""The demonstration shops are created by a local script, never by the API.

The script is the only caller of `BenefitsStore.grant_demo`, so what it refuses
matters as much as what it creates: a deployment that may not have demo
privileges must not be seeded, and seeding twice must not mint a second shop or
quietly reset a password.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def seed(tmp_path, *args, **env):
    """Run the seeder with its own working directory, so no project .env leaks in."""
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed_demo.py"),
         "--data-dir", str(tmp_path), *args],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "ITP_ENVIRONMENT": "production", **env},
    )


def demo_env(**extra):
    return {"ITP_ENVIRONMENT": "development", "ITP_DEMO_ENABLED": "true", **extra}


@pytest.mark.parametrize("env", [
    {"ITP_ENVIRONMENT": "production"},
    {"ITP_ENVIRONMENT": "development"},  # the switch is off
    {"ITP_ENVIRONMENT": "development", "ITP_DEMO_ENABLED": "true",
     "ITP_PUBLIC_ORIGIN": "https://example.com"},  # public deployment
])
def test_a_deployment_that_may_not_have_demo_privileges_is_never_seeded(tmp_path, env):
    result = seed(tmp_path, **env)
    assert result.returncode != 0
    assert "注册" not in result.stdout
    # Nothing was written: no account, no product, no entitlement.
    assert not (tmp_path / "merchants.sqlite3").exists()


def test_seeding_creates_two_shops_with_products_and_the_entitlement(tmp_path):
    result = seed(tmp_path, **demo_env())
    assert result.returncode == 0, result.stderr
    assert "商品：新增 4 件" in result.stdout
    assert "演示特权已授予 2 个账号" in result.stdout

    with sqlite3.connect(tmp_path / "merchants.sqlite3") as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM merchants ORDER BY name")]
        assert names == ["demo-atelier", "demo-outfitters"]
        assert conn.execute("SELECT COUNT(*) FROM demo_accounts").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM garments").fetchone()[0] == 4
        # The password is a hash, and nothing stores or logs the plaintext one.
        hashes = [row[0] for row in conn.execute("SELECT password_hash FROM merchants")]
        assert all(value.startswith("scrypt$") for value in hashes)
        revealed = [line.split("/")[-1].strip()
                    for line in result.stdout.splitlines() if "  /  " in line]
        assert len(revealed) == 2 and all(secret not in " ".join(hashes) for secret in revealed)


def test_seeding_again_changes_nothing(tmp_path):
    first = seed(tmp_path, **demo_env())
    assert first.returncode == 0, first.stderr
    with sqlite3.connect(tmp_path / "merchants.sqlite3") as conn:
        before = dict(conn.execute("SELECT name, password_hash FROM merchants"))

    again = seed(tmp_path, **demo_env())
    assert again.returncode == 0, again.stderr
    assert "商品：新增 0 件" in again.stdout
    assert again.stdout.count("已存在，保留原密码") == 2
    with sqlite3.connect(tmp_path / "merchants.sqlite3") as conn:
        assert dict(conn.execute("SELECT name, password_hash FROM merchants")) == before
        assert conn.execute("SELECT COUNT(*) FROM garments").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM demo_accounts").fetchone()[0] == 2


def test_the_operator_can_supply_the_password_instead(tmp_path):
    result = seed(tmp_path, **demo_env(ITP_DEMO_PASSWORD="demo-pass-12345"))
    assert result.returncode == 0, result.stderr
    # The operator already holds this one, so the script does not echo it back
    # into a terminal that may be logged or shared.
    assert "demo-pass-12345" not in result.stdout
    assert "密码由 ITP_DEMO_PASSWORD 提供" in result.stdout


def test_revoking_removes_the_entitlement_but_keeps_the_shops(tmp_path):
    seed(tmp_path, **demo_env())
    revoked = seed(tmp_path, "--revoke", **demo_env())
    assert revoked.returncode == 0, revoked.stderr
    with sqlite3.connect(tmp_path / "merchants.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM demo_accounts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM merchants").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM garments").fetchone()[0] == 4
