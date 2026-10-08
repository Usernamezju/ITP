"""Optional SMS verification, with single-use challenges and account ownership."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from itp.accounts import AuthLimiter, public_account
from itp.merchant_auth import current_user


def mainland_phone(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"1[3-9][0-9]{9}", value):
        raise ValueError("请输入有效的中国大陆 11 位手机号")
    return value


class PhoneStore:
    def __init__(self, accounts, settings=None, *, sender=None):
        self.accounts = accounts
        self.endpoint = getattr(settings, "sms_endpoint", "")
        key = getattr(settings, "sms_api_key", None)
        self.api_key = key.get_secret_value() if key else ""
        self.sender = sender
        self.secret = secrets.token_bytes(32)
        self.limiter = AuthLimiter()
        with accounts.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account_phones (
                    user_id TEXT PRIMARY KEY REFERENCES merchants(id),
                    phone TEXT NOT NULL UNIQUE, verified INTEGER NOT NULL DEFAULT 0,
                    updated INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sms_challenges (
                    id TEXT PRIMARY KEY, subject TEXT NOT NULL, phone TEXT NOT NULL,
                    purpose TEXT NOT NULL, digest TEXT NOT NULL, expires INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0
                );
            """)

    @property
    def ready(self):
        return bool(self.sender or (self.endpoint and self.api_key))

    def profile(self, user_id):
        with self.accounts.connect() as conn:
            row = conn.execute("SELECT phone,verified FROM account_phones WHERE user_id=?",
                               (user_id,)).fetchone()
        return {"phone": row[0] if row else None, "phone_verified": bool(row and row[1]),
                "sms_available": self.ready}

    def _digest(self, challenge, code):
        return hmac.new(self.secret, f"{challenge}:{code}".encode(), hashlib.sha256).hexdigest()

    def send(self, subject, phone, purpose, *, now=None):
        phone = mainland_phone(phone)
        if not self.ready:
            raise ValueError("短信服务未配置，手机号只能保存为未验证")
        self.limiter.check(("sms-phone", phone), attempts=1, seconds=60)
        self.limiter.check(("sms-phone-hour", phone), attempts=5, seconds=3600)
        self.limiter.check(("sms-user", subject), attempts=8, seconds=3600)
        now = int(time.time()) if now is None else now
        challenge, code = uuid4().hex, f"{secrets.randbelow(1000000):06d}"
        try:
            if self.sender:
                self.sender(phone, code)
            else:
                with httpx.Client(timeout=10, follow_redirects=False) as client:
                    response = client.post(self.endpoint, headers={"Authorization":
                        f"Bearer {self.api_key}"}, json={"phone": phone, "code": code,
                        "expires_in": 300, "purpose": purpose})
                    response.raise_for_status()
        except Exception:
            raise ValueError("验证码发送失败，请稍后重试") from None
        with self.accounts.connect() as conn:
            conn.execute("DELETE FROM sms_challenges WHERE expires<?", (now - 3600,))
            conn.execute("UPDATE sms_challenges SET consumed=1 WHERE subject=? AND purpose=?",
                         (subject, purpose))
            conn.execute("INSERT INTO sms_challenges VALUES (?,?,?,?,?,?,0,0)",
                         (challenge, subject, phone, purpose,
                          self._digest(challenge, code), now + 300))
        return {"challenge_id": challenge, "expires_in": 300}

    def check(self, subject, phone, purpose, challenge, code, *, now=None):
        """Persist failed attempts before returning a proof for the binding transaction."""
        now = int(time.time()) if now is None else now
        valid = False
        with self.accounts.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT subject,phone,purpose,digest,expires,attempts,consumed "
                               "FROM sms_challenges WHERE id=?", (challenge,)).fetchone()
            if row and row[0:3] == (subject, phone, purpose) and row[4] > now \
                    and row[5] < 5 and not row[6]:
                conn.execute("UPDATE sms_challenges SET attempts=attempts+1 WHERE id=?",
                             (challenge,))
                valid = hmac.compare_digest(row[3], self._digest(challenge, code))
        if not valid:
            raise ValueError("验证码错误、已使用或已过期，请重新验证")
        return challenge

    def bind(self, conn, user_id, phone, *, proof=None, old_proof=None, now=None):
        now = int(time.time()) if now is None else now
        phone = mainland_phone(phone)
        current = conn.execute("SELECT phone,verified FROM account_phones WHERE user_id=?",
                               (user_id,)).fetchone()
        if current and current[0] == phone:
            if current[1] and not proof:
                return
        elif current and current[1] and not old_proof:
            raise ValueError("换绑已验证手机号需要先验证原手机号")
        if self.ready and not proof:
            raise ValueError("请先验证新手机号")
        holder = conn.execute("SELECT user_id,verified FROM account_phones WHERE phone=?",
                              (phone,)).fetchone()
        if holder and holder[0] != user_id:
            # Verified proof can reclaim an unverified number, never another
            # account's verified binding. Unverified input cannot steal either.
            if not proof or holder[1]:
                raise ValueError("手机号已被其他账号绑定")
            conn.execute("DELETE FROM account_phones WHERE user_id=?", (holder[0],))
        for challenge in (proof, old_proof):
            if challenge:
                updated = conn.execute("UPDATE sms_challenges SET consumed=1 "
                    "WHERE id=? AND consumed=0 AND expires>?", (challenge, now))
                if updated.rowcount != 1:
                    raise ValueError("验证码已使用或已过期")
        conn.execute("INSERT INTO account_phones VALUES (?,?,?,?) ON CONFLICT(user_id) "
                     "DO UPDATE SET phone=excluded.phone,verified=excluded.verified,"
                     "updated=excluded.updated", (user_id, phone, int(bool(proof)), now))


class SmsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(max_length=32)
    name: str = Field(default="", max_length=32)


class PhoneBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(max_length=32)
    challenge_id: str = Field(default="", max_length=32)
    code: str = Field(default="", max_length=6)
    old_challenge_id: str = Field(default="", max_length=32)
    old_code: str = Field(default="", max_length=6)


def phone_router(accounts):
    router = APIRouter()
    phones = accounts.phones

    @router.get("/api/auth/phone-policy")
    def policy():
        return {"sms_available": phones.ready, "country": "CN"}

    @router.post("/api/auth/sms")
    def register_sms(body: SmsRequest, request: Request):
        phones.limiter.check(("sms-ip", request.client.host if request.client else "unknown"),
                             attempts=10, seconds=3600)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{3,32}", body.name):
            raise HTTPException(422, "请先填写有效账号")
        try:
            return phones.send(f"register:{body.name}", body.phone, "register")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/account/phone/sms")
    def bind_sms(body: SmsRequest, user: Annotated[dict, Depends(current_user)]):
        try:
            return phones.send(user["id"], body.phone, "bind")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.put("/api/account/phone")
    def bind_phone(body: PhoneBinding, user: Annotated[dict, Depends(current_user)]):
        try:
            phone = mainland_phone(body.phone)
            proof = phones.check(user["id"], phone, "bind", body.challenge_id, body.code) \
                if phones.ready else None
            profile = phones.profile(user["id"])
            old_proof = None
            if profile["phone_verified"] and profile["phone"] != phone:
                old_proof = phones.check(user["id"], profile["phone"], "bind",
                                         body.old_challenge_id, body.old_code)
            with accounts.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                phones.bind(conn, user["id"], phone, proof=proof, old_proof=old_proof)
            return public_account(user) | phones.profile(user["id"])
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
