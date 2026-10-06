"""Storage and token plumbing behind changing a merchant password."""

import sqlite3

from itp.garments import MerchantStore
from itp.merchant_auth import (
    JWT_FINGERPRINT_LENGTH,
    decode_token,
    encode_token,
    hash_password,
    password_fingerprint,
    verify_password,
)

NEW_PASSWORD = "second-password"
SECRET = "a" * 64


def test_setting_a_password_replaces_the_hash(tmp_path):
    store = MerchantStore(tmp_path / "data")
    merchant = store.create_merchant(name="shop", display_name="店", contact="",
                                     password_hash=hash_password("first-password"), quota=10)

    assert store.set_password(merchant["id"], hash_password(NEW_PASSWORD)) is True

    updated = store.merchant(merchant["id"])
    assert verify_password(NEW_PASSWORD, updated["password_hash"])
    assert not verify_password("first-password", updated["password_hash"])


def test_setting_a_password_for_an_unknown_merchant_reports_failure(tmp_path):
    store = MerchantStore(tmp_path / "data")

    assert store.set_password("f" * 32, hash_password(NEW_PASSWORD)) is False


def test_a_legacy_database_still_opens_and_authenticates(tmp_path):
    """A file written before password changes existed must keep working."""
    root = tmp_path / "data"
    root.mkdir()
    legacy = sqlite3.connect(root / "merchants.sqlite3")
    legacy.executescript(
        """
        CREATE TABLE merchants (
            id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, display_name TEXT,
            contact TEXT, password_hash TEXT, created REAL,
            disabled INTEGER DEFAULT 0, quota INTEGER
        );
        """
    )
    stored = hash_password("first-password")
    legacy.execute("INSERT INTO merchants VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   ("legacy", "old-shop", "老商家", "", stored, 1.0, 0, 10))
    legacy.commit()
    legacy.close()

    store = MerchantStore(root)
    merchant = store.merchant_by_name("old-shop")

    assert merchant is not None
    assert verify_password("first-password", merchant["password_hash"])


def test_the_fingerprint_follows_the_password_and_leaks_nothing():
    first = hash_password("first-password")
    second = hash_password(NEW_PASSWORD)

    assert password_fingerprint(first) != password_fingerprint(second)
    assert password_fingerprint(first) == password_fingerprint(first)
    assert len(password_fingerprint(first)) == JWT_FINGERPRINT_LENGTH
    # It is a digest of the stored hash, not anything derived from the password.
    assert "first-password" not in password_fingerprint(first)
    assert first[:8] not in password_fingerprint(first)


def test_a_token_carries_the_password_it_was_issued_under():
    stored = hash_password("first-password")

    token, _lifetime = encode_token(SECRET, "m" * 32, hours=1, password_hash=stored)
    claims = decode_token(SECRET, token)

    assert claims["pwd"] == password_fingerprint(stored)
    # A token signed for a different password no longer matches.
    other, _lifetime = encode_token(SECRET, "m" * 32, hours=1,
                                   password_hash=hash_password(NEW_PASSWORD))
    assert decode_token(SECRET, other)["pwd"] != claims["pwd"]


def test_a_token_without_a_password_tag_stays_valid():
    """Tokens minted before this claim existed must not be rejected."""
    token, _lifetime = encode_token(SECRET, "m" * 32, hours=1)

    assert "pwd" not in decode_token(SECRET, token)
