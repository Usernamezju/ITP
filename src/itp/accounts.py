"""Account DTOs and bounded authentication throttling; crypto stays in merchant_auth."""

import threading
import time
from collections import OrderedDict
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


class AccountRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    display_name: str
    contact: str = ""
    password: str
    role: Literal["customer", "merchant"] = "customer"


class AccountProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    contact: str | None = None


def public_account(user: dict) -> dict:
    return {key: user[key] for key in ("id", "name", "display_name", "contact", "role", "created")}


class AuthLimiter:
    """A process-local, bounded sliding window, never a durable customer/IP log."""

    def __init__(self, capacity: int = 4096):
        self.capacity = capacity
        self._entries: OrderedDict[tuple, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: tuple, *, attempts: int, seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            stamps = [stamp for stamp in self._entries.pop(key, []) if stamp > now - seconds]
            if len(stamps) >= attempts:
                self._entries[key] = stamps
                raise HTTPException(429, "操作过于频繁，请稍后再试",
                                    headers={"Retry-After": str(max(1, int(stamps[0] + seconds - now)))})
            stamps.append(now)
            self._entries[key] = stamps
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)
