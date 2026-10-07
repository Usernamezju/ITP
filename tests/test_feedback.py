"""Stored user feedback: anonymous submission, account binding and admin read."""

import sqlite3

from test_admin_api import admin_auth, signup
from test_merchant_api import env  # noqa: F401  (the live app fixture)

VALID = {"kind": "功能建议", "body": "希望穿搭推荐能按场合筛选。", "contact": "fan@example.com",
         "page": "/outfits"}


def submit(client, headers=None, **changes):
    return client.post("/api/feedback", headers=headers, json={**VALID, **changes})


def test_signed_in_feedback_binds_its_account_and_the_admin_reads_it(env):
    client, settings, _ = env
    account, headers = signup(client, name="reporter")
    first = submit(client, headers)
    assert first.status_code == 201, first.text
    assert first.json()["kind"] == "功能建议" and first.json()["signed_in"] is True
    assert first.json()["id"] and first.json()["created"] > 0
    # The body is never echoed back to the sender.
    assert "希望穿搭推荐" not in first.text

    # Anonymous reports are accepted too, and the inbox keeps both, newest first.
    assert submit(client, body="提交按钮在手机上点不动。", kind="问题反馈").status_code == 201
    _, admin = admin_auth(client)
    inbox = client.get("/api/admin/feedback", headers=admin).json()
    assert inbox["total"] == 2
    assert [item["body"] for item in inbox["items"]] == ["提交按钮在手机上点不动。", VALID["body"]]
    newest, signed = inbox["items"]
    assert newest["account_name"] is None and newest["user_id"] is None
    assert newest["page"] == "/outfits" and newest["contact"] == "fan@example.com"
    assert signed["account_name"] == "reporter" and signed["role"] == "customer"
    assert signed["user_id"] == account["id"]

    with sqlite3.connect(settings.data_dir / "merchants.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 2
        columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback)")}
        assert columns == {"id", "user_id", "account_name", "role", "kind", "body",
                           "contact", "page", "created"}


def test_only_an_admin_reads_the_inbox(env):
    client, _, _ = env
    _, customer = signup(client, name="reader")
    _, merchant = signup(client, name="shop", role="merchant")
    assert submit(client, customer).status_code == 201
    for headers in (None, customer, merchant):
        response = client.get("/api/admin/feedback", headers=headers or {})
        assert response.status_code in (401, 403), response.text
    assert client.get("/api/admin/feedback").status_code == 401


def test_an_expired_session_still_submits_as_anonymous(env):
    client, _, _ = env
    stale = {"Authorization": "Bearer not-a-real-token"}
    response = submit(client, stale)
    assert response.status_code == 201, response.text
    assert response.json()["signed_in"] is False
    _, admin = admin_auth(client)
    item = client.get("/api/admin/feedback", headers=admin).json()["items"][0]
    assert item["account_name"] is None and item["user_id"] is None


def test_lengths_kinds_and_unknown_fields_are_refused(env):
    client, _, _ = env
    assert submit(client, body="太短").status_code == 422
    assert submit(client, body="  " + "x" * 5 + "  ").status_code == 201  # trimmed, not refused
    assert submit(client, kind="随手写的").status_code == 422
    assert submit(client, body="x" * 1001).status_code == 422
    assert submit(client, contact="x" * 81).status_code == 422
    assert submit(client, page="x" * 201).status_code == 422
    assert submit(client, body="正常内容，只是多了字段", extra="x").status_code == 422
    assert submit(client, body="含控制字符\x07的内容").status_code == 422


def test_a_busy_database_is_reported_as_retryable(env, monkeypatch):
    client, _, _ = env
    def unavailable(**fields):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(client.app.state.merchants.feedback, "add", unavailable)
    response = submit(client)
    assert response.status_code == 503
    assert "稍后重试" in response.json()["detail"]


def test_repeated_submissions_from_one_source_are_throttled(env):
    client, _, _ = env
    codes = [submit(client, body=f"第 {index} 条反馈内容").status_code for index in range(11)]
    assert codes[:10] == [201] * 10
    assert codes[10] == 429
