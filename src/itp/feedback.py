"""User feedback: stored once, readable by the operator console only.

A report is useful exactly when something is broken, so the submission route
stays open to visitors: a signed-in account is attached from its verified
token (never from the request body), and an expired session still submits as
an anonymous entry instead of failing.  Only an admin token can read the
inbox back.
"""

import sqlite3
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from itp.accounts import AuthLimiter
from itp.merchant_auth import current_admin, current_user_optional

KINDS = ("功能建议", "问题反馈", "界面体验", "其他")
BODY_MIN = 5
BODY_MAX = 1000
CONTACT_MAX = 80
PAGE_MAX = 200


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    body: str
    # Both optional: an anonymous reporter owes us no way to reach them.
    contact: str = ""
    page: str = ""


def _clean(value: Any, label: str, *, limit: int) -> str:
    """Trim one text field and refuse control characters a form cannot show."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label}格式不正确")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{label}最多 {limit} 个字")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError(f"{label}包含无法显示的字符")
    return text


def normalize_feedback(payload: FeedbackRequest, account: dict | None) -> dict:
    """Validate one submission; the account comes from the token, not the body."""
    kind = _clean(payload.kind, "反馈类型", limit=20)
    if kind not in KINDS:
        raise ValueError("请选择反馈类型")
    body = _clean(payload.body, "反馈内容", limit=BODY_MAX)
    if len(body) < BODY_MIN:
        raise ValueError(f"反馈内容至少 {BODY_MIN} 个字")
    return {
        "kind": kind,
        "body": body,
        "contact": _clean(payload.contact, "联系方式", limit=CONTACT_MAX),
        "page": _clean(payload.page, "来源页面", limit=PAGE_MAX),
        "user_id": account["id"] if account else None,
        "account_name": account["name"] if account else None,
        "role": account["role"] if account else None,
    }


class FeedbackStore:
    """The feedback inbox, kept in the same database as accounts and goods."""

    def __init__(self, accounts):
        self.accounts = accounts
        with accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    user_id TEXT REFERENCES merchants(id) ON DELETE SET NULL,
                    account_name TEXT,
                    role TEXT,
                    kind TEXT NOT NULL,
                    body TEXT NOT NULL,
                    contact TEXT,
                    page TEXT,
                    created INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS feedback_created
                    ON feedback(created DESC);
            """)

    def add(self, *, kind: str, body: str, contact: str, page: str,
            user_id: str | None, account_name: str | None,
            role: str | None) -> dict:
        identifier = uuid4().hex
        created = int(time.time())
        with self.accounts.connect() as conn:
            conn.execute(
                "INSERT INTO feedback (id, user_id, account_name, role, kind, body,"
                " contact, page, created) VALUES (?,?,?,?,?,?,?,?,?)",
                (identifier, user_id, account_name, role, kind, body,
                 contact or None, page or None, created),
            )
        return {"id": identifier, "created": created, "kind": kind,
                "signed_in": bool(user_id)}

    def list(self, *, limit: int = 20, offset: int = 0) -> tuple[int, list[dict]]:
        """Newest first; an admin-only read, so no per-owner scoping exists."""
        with self.accounts.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            rows = conn.execute(
                "SELECT id, user_id, account_name, role, kind, body, contact, page, created "
                # rowid breaks ties inside one second, so the newest report is first.
                "FROM feedback ORDER BY created DESC, rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return total, [{
            "id": row[0], "user_id": row[1], "account_name": row[2], "role": row[3],
            "kind": row[4], "body": row[5], "contact": row[6], "page": row[7],
            "created": row[8],
        } for row in rows]


def feedback_router(merchants):
    router = APIRouter()
    limiter = AuthLimiter()

    @router.post("/api/feedback", status_code=201)
    def submit(body: FeedbackRequest, request: Request,
               account: dict | None = Depends(current_user_optional)):
        # In-memory, per-source throttling; no IP is ever persisted with a report.
        limiter.check(("feedback", request.client.host if request.client else "unknown"),
                      attempts=10, seconds=600)
        try:
            fields = normalize_feedback(body, account)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return merchants.feedback.add(**fields)
        except sqlite3.OperationalError as exc:
            raise HTTPException(503, "反馈暂不可用，请稍后重试") from exc

    @router.get("/api/admin/feedback", include_in_schema=False,
                dependencies=[Depends(current_admin)])
    def admin_feedback(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0)):
        total, items = merchants.feedback.list(limit=limit, offset=offset)
        return {"total": total, "items": items}

    return router
