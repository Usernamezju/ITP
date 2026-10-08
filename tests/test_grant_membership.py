"""The operator's membership grant: what it hands out, and what it refuses.

A membership granted by hand has to look exactly like one that was paid for —
same chaining, same entitlements, same one-time bonus — while recording that no
money moved. These tests drive the real command against a scratch data
directory, so what they check is what an operator would actually run.
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")

sys.path.insert(0, str(ROOT / "src"))
from itp.benefits import month_after  # noqa: E402


def grant(tmp_path, *args, **env):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "grant_membership.py"),
         "--data-dir", str(tmp_path), *args],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "ITP_ENVIRONMENT": "development", **env},
    )


@pytest.fixture
def shop(tmp_path):
    """A scratch data directory holding one merchant account named `fan`."""
    sys.path.insert(0, str(ROOT / "src"))
    from itp.config import Settings
    from itp.garments import MerchantStore
    from itp.merchant_auth import hash_password

    settings = Settings(_env_file=None, data_dir=tmp_path, environment="development")
    store = MerchantStore(tmp_path, settings)
    user = store.create_merchant(
        name="fan", display_name="智慧服装", contact="",
        password_hash=hash_password("scratch-password"), quota=0,
    )
    return tmp_path, user["id"]


def subscriptions(tmp_path, user_id):
    with sqlite3.connect(tmp_path / "merchants.sqlite3") as conn:
        return conn.execute(
            "SELECT plan_id, starts, ends, order_id FROM subscriptions "
            "WHERE user_id=? ORDER BY starts", (user_id,)
        ).fetchall()


def points(tmp_path, user_id):
    with sqlite3.connect(tmp_path / "merchants.sqlite3") as conn:
        row = conn.execute(
            "SELECT balance FROM point_wallets WHERE user_id=?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def month_of(stamp):
    return datetime.fromtimestamp(stamp, TZ).strftime("%Y-%m")


def test_one_month_covers_today_and_records_the_operator(tmp_path, shop):
    data, user = shop
    result = grant(data, "--account", "fan", "--months", "1")
    assert result.returncode == 0, result.stderr

    rows = subscriptions(data, user)
    assert [row[0] for row in rows] == ["customer_monthly"]
    starts, ends, reference = rows[0][1], rows[0][2], rows[0][3]
    today = datetime.now(TZ)
    assert month_of(starts) == today.strftime("%Y-%m")
    assert ends > starts
    # Money did not move, and the row says who is responsible instead.
    assert reference.startswith("operator-membership:")
    with sqlite3.connect(data / "merchants.sqlite3") as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # Nothing about payments was touched: the two tables a real purchase
        # writes to were never even created in this database.
        assert "payment_orders" not in tables
        assert "payment_transactions" not in tables
        assert conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0] == 0


def test_twelve_months_chain_into_twelve_contiguous_periods(tmp_path, shop):
    data, user = shop
    result = grant(data, "--account", "fan", "--months", "12")
    assert result.returncode == 0, result.stderr

    rows = subscriptions(data, user)
    assert len(rows) == 12
    assert len({row[3] for row in rows}) == 12
    # Each period ends where the next begins, with no gap and no overlap.
    for earlier, later in zip(rows, rows[1:], strict=False):
        assert earlier[2] == later[1]
    # Twelve periods from now, computed by the same month arithmetic the
    # subscription chain uses, so the anniversary anchor is what is checked.
    assert rows[-1][2] == month_after(rows[0][1], 12)


def test_the_same_command_twice_on_one_day_changes_nothing(tmp_path, shop):
    data, user = shop
    assert grant(data, "--account", "fan", "--months", "12").returncode == 0
    before = subscriptions(data, user)

    again = grant(data, "--account", "fan", "--months", "12")
    assert again.returncode == 0, again.stderr
    assert subscriptions(data, user) == before
    assert points(data, user) == 1000


def test_the_first_membership_bonus_is_paid_once(tmp_path, shop):
    data, user = shop
    assert grant(data, "--account", "fan", "--months", "12").returncode == 0
    assert points(data, user) == 1000

    with sqlite3.connect(data / "merchants.sqlite3") as conn:
        ledger = conn.execute(
            "SELECT kind, reference, delta FROM point_ledger WHERE user_id=?", (user,)
        ).fetchall()
    # One row, not twelve: chaining twelve periods does not pay the joining
    # bonus twelve times.
    assert ledger == [("first_membership", "once", 1000)]


def test_an_unknown_account_or_plan_is_refused(tmp_path, shop):
    data, _ = shop
    missing = grant(data, "--account", "nobody", "--months", "1")
    assert missing.returncode != 0
    assert "账号不存在" in missing.stderr

    unknown = grant(data, "--account", "fan", "--plan", "customer_deluxe")
    assert unknown.returncode != 0
    assert "套餐不存在" in unknown.stderr

    assert grant(data, "--account", "fan", "--months", "0").returncode != 0
