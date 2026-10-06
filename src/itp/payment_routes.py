"""Payment API routes, with no caller-controlled credit or success flag."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from itp.accounts import AuthLimiter
from itp.commerce import CommerceError, IdempotencyConflict
from itp.merchant_auth import current_user
from itp.payments import PaymentError

logger = logging.getLogger(__name__)
User = Annotated[dict, Depends(current_user)]


class PaymentOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["recharge", "membership"]
    provider: Literal["alipay", "wechat", "mock"] | None = None
    amount_cents: StrictInt | None = Field(default=None, ge=1, le=10000000)
    plan_id: str | None = Field(default=None, min_length=1, max_length=80)


def payment_router(service):
    router = APIRouter()
    limiter = AuthLimiter()

    @router.get("/api/payments/methods")
    def methods():
        return {"methods": service.methods()}

    @router.post("/api/account/orders", status_code=201)
    def create(body: PaymentOrderRequest, request: Request, user: User):
        limiter.check(("create", user["id"]), attempts=30, seconds=3600)
        try:
            return service.create(
                user["id"], body.model_dump(), request.headers.get("idempotency-key", "")
            )
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except PaymentError as exc:
            raise HTTPException(503, str(exc)) from exc
        except CommerceError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/account/orders")
    def orders(
        user: User,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return {"items": service.list(user["id"], limit=limit, offset=offset)}

    @router.get("/api/account/orders/{order_id}")
    def order(order_id: str, user: User):
        result = service.get(order_id, user["id"])
        if not result:
            raise HTTPException(404, "订单不存在")
        return service.public(result)

    @router.post("/api/account/orders/{order_id}/refresh")
    def refresh(order_id: str, user: User):
        limiter.check(("query", user["id"]), attempts=30, seconds=60)
        try:
            return service.query(order_id, user["id"])
        except KeyError as exc:
            raise HTTPException(404, "订单不存在") from exc
        except (PaymentError, CommerceError) as exc:
            raise HTTPException(503, str(exc)) from exc

    if "mock" in service.providers:

        @router.post("/api/account/orders/{order_id}/mock-pay")
        def mock_pay(order_id: str, user: User):
            try:
                return service.simulate(order_id, user["id"])
            except KeyError as exc:
                raise HTTPException(404, "订单不存在") from exc
            except PaymentError as exc:
                raise HTTPException(403, str(exc)) from exc

    @router.post("/api/payments/callbacks/{provider_name}")
    async def callback(provider_name: Literal["alipay", "wechat", "mock"], request: Request):
        try:
            provider = service.provider(provider_name)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 128 * 1024:
                    raise PaymentError("支付回调过大")
            event = provider.callback(bytes(raw), request.headers)
            if event:
                service.fulfill(event)
        except (PaymentError, CommerceError) as exc:
            logger.warning(
                "Rejected payment callback: provider=%s reason=%s",
                provider_name,
                type(exc).__name__,
            )
            if provider_name == "alipay":
                return PlainTextResponse("failure", status_code=400)
            return JSONResponse({"code": "FAIL", "message": "回调验证或履约失败"}, status_code=400)
        if provider_name == "alipay":
            return PlainTextResponse("success")
        return JSONResponse({"code": "SUCCESS", "message": "成功"})

    return router
