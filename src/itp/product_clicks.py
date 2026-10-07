"""Anonymous product click events and merchant-owned aggregate statistics."""
from datetime import datetime, timedelta
import sqlite3
import time
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from itp.accounts import AuthLimiter
from itp.garments import normalize_purchase_url, public_garment
from itp.merchant_auth import current_merchant

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ProductClicks:
    def __init__(self, accounts):
        self.accounts = accounts
        with accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS garment_clicks (
                    id TEXT PRIMARY KEY,
                    garment_id TEXT REFERENCES garments(id) ON DELETE SET NULL,
                    garment_ref TEXT NOT NULL,
                    merchant_id TEXT NOT NULL REFERENCES merchants(id),
                    clicked_at INTEGER NOT NULL CHECK(typeof(clicked_at)='integer')
                );
                CREATE INDEX IF NOT EXISTS clicks_merchant_time
                    ON garment_clicks(merchant_id, clicked_at);
                CREATE INDEX IF NOT EXISTS clicks_product_time
                    ON garment_clicks(merchant_id, garment_ref, clicked_at);
            """)

    def record(self, garment_id):
        """Resolve the owner and link inside the write transaction, never trust callers."""
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT merchant_id, purchase_url FROM garments "
                               "WHERE id=? AND status='published'", (garment_id,)).fetchone()
            if not row or not row[1]:
                raise ValueError("该商品暂未提供购买链接或已下架")
            url = normalize_purchase_url(row[1])
            identifier = uuid4().hex
            conn.execute("INSERT INTO garment_clicks VALUES (?,?,?,?,?)",
                         (identifier, garment_id, garment_id, row[0], int(time.time())))
        return {"click_id": identifier, "purchase_url": url}

    def counts(self, merchant_id, *, garment_id=None, days=14, now=None):
        current = datetime.fromtimestamp(time.time() if now is None else now, BUSINESS_TIMEZONE)
        today = current.replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        end = int((today + timedelta(days=1)).timestamp())
        clause = "merchant_id=? AND clicked_at<?"
        args = [merchant_id, end]
        if garment_id:
            clause += " AND garment_ref=?"
            args.append(garment_id)
        with self.accounts.connect() as conn:
            summary = conn.execute(
                "SELECT COALESCE(SUM(clicked_at>=?),0), COALESCE(SUM(clicked_at>=?),0), COUNT(*) "
                f"FROM garment_clicks WHERE {clause}",
                [int(today.timestamp()), int(month.timestamp()), *args],
            ).fetchone()
            by_product = conn.execute(
                "SELECT garment_ref, SUM(clicked_at>=?), SUM(clicked_at>=?), COUNT(*) "
                f"FROM garment_clicks WHERE {clause} GROUP BY garment_ref",
                [int(today.timestamp()), int(month.timestamp()), *args],
            ).fetchall()
            start = today - timedelta(days=days - 1)
            daily = dict(conn.execute(
                "SELECT date(clicked_at,'unixepoch','+8 hours'), COUNT(*) FROM garment_clicks "
                f"WHERE {clause} AND clicked_at>=? GROUP BY 1",
                [*args, int(start.timestamp())],
            ))
        names = ("today", "month", "total")
        return {
            "timezone": "Asia/Shanghai",
            "summary": dict(zip(names, summary)),
            "counts": {row[0]: dict(zip(names, row[1:])) for row in by_product},
            "trend": [{"date": (start + timedelta(days=index)).date().isoformat(),
                       "clicks": daily.get((start + timedelta(days=index)).date().isoformat(), 0)}
                      for index in range(days)],
        }


def click_router(merchants):
    router = APIRouter()
    limiter = AuthLimiter()

    @router.post("/api/garments/{garment_id}/clicks", status_code=201)
    def record(garment_id: str, request: Request):
        # In-memory rate limiting; no IP, JWT, referrer or customer event log is persisted.
        limiter.check(("product-click", request.client.host if request.client else "unknown"),
                      attempts=120, seconds=60)
        try:
            return merchants.clicks.record(garment_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except sqlite3.OperationalError as exc:
            raise HTTPException(503, "点击记录暂不可用，请稍后重试") from exc

    @router.get("/api/merchant/analytics")
    def analytics(garment_id: str | None = Query(None),
                  limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  days: int = Query(14, ge=1, le=31),
                  rank: Literal["today", "month", "total"] | None = Query(None),
                  merchant: dict = Depends(current_merchant)):
        if garment_id and not merchants.garment_for(merchant["id"], garment_id):
            raise HTTPException(404, "商品不存在")
        result = merchants.clicks.counts(merchant["id"], garment_id=garment_id, days=days)
        if garment_id:
            total, garments = 1, [merchants.garment_for(merchant["id"], garment_id)]
        elif rank:
            # The detail page ranks one period: every product of the shop takes
            # part, including those without a click, and ties keep newest first.
            counted = result["counts"]
            ranked = merchants.all_garments(merchant["id"])
            ranked.sort(key=lambda item: (-counted.get(item["id"], {}).get(rank, 0),
                                          -counted.get(item["id"], {}).get("total", 0)))
            total, garments = len(ranked), ranked[offset:offset + limit]
        else:
            total, garments = merchants.list_garments(merchant["id"], limit=limit, offset=offset)
        gallery = merchants.images_for_many([item["id"] for item in garments])
        return {"timezone": result["timezone"], "summary": result["summary"],
                "trend": result["trend"], "total": total, "rank": rank, "items": [
                    {**public_garment(item, gallery[item["id"]]),
                     "clicks": result["counts"].get(item["id"], {"today": 0, "month": 0, "total": 0})}
                    for item in garments]}

    return router
