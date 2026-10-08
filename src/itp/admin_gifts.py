"""Audited, transactional administrator grants using existing benefit services."""

import json
import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from itp.commerce import CommerceError, IdempotencyConflict
from itp.merchant_auth import current_admin


class GiftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    user_id: str = Field(min_length=1, max_length=128)
    kind: Literal["membership", "points"]
    reason: str = Field(min_length=1, max_length=200)
    plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    periods: int | None = Field(default=None, strict=True, ge=1, le=12)
    points: int | None = Field(default=None, strict=True, ge=1, le=1_000_000_000)

    @model_validator(mode="after")
    def check_kind(self):
        if self.kind == "membership":
            if not self.plan_id or self.periods is None or self.points is not None:
                raise ValueError("赠送会员需要套餐及期数，不能同时填写积分")
        elif self.points is None or self.plan_id is not None or self.periods is not None:
            raise ValueError("赠送积分需要正整数积分，不能同时填写会员套餐")
        return self


class GiftStore:
    def __init__(self, accounts):
        self.accounts, self.commerce = accounts, accounts.commerce
        with accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS admin_gifts (
                    id TEXT PRIMARY KEY, admin_id TEXT NOT NULL REFERENCES merchants(id),
                    request_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES merchants(id),
                    kind TEXT NOT NULL CHECK(kind IN ('membership','points')),
                    reason TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, created INTEGER NOT NULL,
                    UNIQUE(admin_id,request_id)
                );
                CREATE INDEX IF NOT EXISTS admin_gifts_user ON admin_gifts(user_id,created);
                CREATE TRIGGER IF NOT EXISTS admin_gifts_no_update BEFORE UPDATE ON admin_gifts
                    BEGIN SELECT RAISE(ABORT,'gift history is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS admin_gifts_no_delete BEFORE DELETE ON admin_gifts
                    BEGIN SELECT RAISE(ABORT,'gift history is append-only'); END;
            """)

    def grant(self, admin_id, body: GiftRequest):
        fingerprint = body.model_dump_json(exclude={"request_id"})
        now, gift_id = int(time.time()), uuid4().hex
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            admin = conn.execute(
                "SELECT role,disabled FROM merchants WHERE id=?", (admin_id,)
            ).fetchone()
            if not admin or admin[0] != "admin" or admin[1]:
                raise HTTPException(403, "需要有效的管理员身份才能赠送")
            previous = conn.execute(
                "SELECT fingerprint,result FROM admin_gifts WHERE admin_id=? AND request_id=?",
                (admin_id, body.request_id.hex),
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise IdempotencyConflict("相同赠送编号的内容不一致，请刷新后重新操作")
                return json.loads(previous[1]) | {"replayed": True}
            user = conn.execute(
                "SELECT name,display_name,role,disabled FROM merchants WHERE id=?", (body.user_id,)
            ).fetchone()
            if not user:
                raise HTTPException(404, "赠送账号不存在")
            if user[3] or user[2] not in {"customer", "merchant"}:
                raise CommerceError("只能向正常的顾客或商家账号赠送")
            result = {
                "id": gift_id,
                "admin_id": admin_id,
                "user_id": body.user_id,
                "account_name": user[0],
                "kind": body.kind,
                "reason": body.reason,
                "created": now,
                "points": body.points,
                "plan_id": body.plan_id,
                "periods": body.periods,
            }
            if body.kind == "points":
                result["balance_points"] = self.commerce.benefits.ledger_change(
                    conn,
                    body.user_id,
                    body.points,
                    "admin_gift",
                    gift_id,
                    now=now,
                )
            else:
                plan = self.commerce._plan(
                    conn.execute(
                        "SELECT * FROM commerce_plans WHERE id=?", (body.plan_id,)
                    ).fetchone()
                )
                if not plan or not plan["active"] or not plan["purchasable"]:
                    raise CommerceError("请选择当前有效的付费会员套餐")
                if plan["audience"] != user[2]:
                    raise CommerceError("会员套餐与账号身份不匹配")
                # A gift carries member benefits, but is not a first paid purchase.
                gift_plan = plan | {"price_cents": 0}
                subscriptions = []
                for period in range(body.periods):
                    reference = f"admin-gift:{gift_id}:{period}"
                    self.commerce.grant_subscription(
                        conn,
                        body.user_id,
                        gift_plan,
                        reference,
                        now=now,
                    )
                    row = conn.execute(
                        "SELECT id,starts,ends FROM subscriptions WHERE order_id=?", (reference,)
                    ).fetchone()
                    subscriptions.append(dict(zip(("id", "starts", "ends"), row, strict=True)))
                result.update(
                    plan_name=plan["name"],
                    period_months=plan["period_months"],
                    subscriptions=subscriptions,
                )
            conn.execute(
                "INSERT INTO admin_gifts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    gift_id,
                    admin_id,
                    body.request_id.hex,
                    body.user_id,
                    body.kind,
                    body.reason,
                    fingerprint,
                    json.dumps(result, ensure_ascii=False),
                    now,
                ),
            )
        return result | {"replayed": False}

    def history(self, limit, offset, user_id=None):
        where, parameters = ("WHERE g.user_id=?", [user_id]) if user_id else ("", [])
        with self.accounts.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM admin_gifts g {where}",
                parameters,
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT g.result,a.name,u.name FROM admin_gifts g "
                "JOIN merchants a ON a.id=g.admin_id JOIN merchants u ON u.id=g.user_id "
                f"{where} ORDER BY g.created DESC,g.rowid DESC LIMIT ? OFFSET ?",
                parameters + [limit, offset],
            ).fetchall()
        return {
            "total": total,
            "items": [
                json.loads(row[0]) | {"admin_name": row[1], "account_name": row[2]} for row in rows
            ],
            "plans": [p for p in self.commerce.prices()["plans"] if p["purchasable"]],
        }

    def search(self, query):
        # Escape LIKE metacharacters so a name such as "shop_1" is literal.
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.accounts.connect() as conn:
            rows = conn.execute(
                "SELECT m.id,m.name,m.display_name,m.role,COALESCE(p.balance,0) "
                "FROM merchants m LEFT JOIN point_wallets p ON p.user_id=m.id "
                "WHERE m.disabled=0 AND m.role IN ('customer','merchant') AND "
                "(m.name LIKE ? ESCAPE '\\' OR m.display_name LIKE ? ESCAPE '\\' OR m.id=?) "
                "ORDER BY CASE WHEN m.name=? OR m.id=? THEN 0 ELSE 1 END,m.name LIMIT 20",
                (pattern, pattern, query, query, query),
            ).fetchall()
        return {
            "items": [
                dict(
                    zip(
                        ("id", "name", "display_name", "role", "balance_points"),
                        row,
                        strict=True,
                    )
                )
                for row in rows
            ]
        }


def admin_gifts_router(accounts):
    store = GiftStore(accounts)
    router = APIRouter(prefix="/api/admin/gifts", include_in_schema=False)

    @router.get("", dependencies=[Depends(current_admin)])
    def history(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str | None = Query(None, max_length=128),
    ):
        return store.history(limit, offset, user_id)

    @router.get("/accounts", dependencies=[Depends(current_admin)])
    def search(q: str = Query(..., min_length=1, max_length=128)):
        return store.search(q.strip())

    @router.post("")
    def grant(body: GiftRequest, admin: Annotated[dict, Depends(current_admin)]):
        try:
            return store.grant(admin["id"], body)
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except CommerceError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
