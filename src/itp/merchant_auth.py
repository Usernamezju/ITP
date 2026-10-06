"""Merchant accounts: scrypt passwords, HS256 tokens and the FastAPI guard.

Everything here is standard library only.  Passwords are stored as
``scrypt$n$r$p$salt_hex$hash_hex`` and compared in constant time; tokens are
compact JWTs signed with HMAC-SHA256 using a secret that lives in the local
``.env`` file and is generated on first use.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# Declared so the OpenAPI document carries the scheme and /docs offers an
# Authorize button; the token is still validated by current_merchant below.
bearer_scheme = HTTPBearer(
    auto_error=False, description="商家登录 /api/merchant/login 返回的 access_token"
)

# Cost parameters fixed by the project: 2**14 memory-ish cost, r=8, p=1.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
HASH_BYTES = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024

JWT_ALGORITHM = "HS256"
JWT_SECRET_HEX_LENGTH = 64
JWT_LEEWAY_SECONDS = 0
# Enough of the hash of the password hash to tell two passwords apart.
JWT_FINGERPRINT_LENGTH = 16

_ENV_KEY = re.compile(r"^\s*(?:export\s+)?(ITP_[A-Z_]+)\s*=")
_secret_lock = threading.Lock()

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64url(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a fresh random salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=HASH_BYTES,
        maxmem=SCRYPT_MAXMEM,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash without leaking timing."""
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# A login for an unknown account still runs one scrypt verification against this
# hash, so a missing name and a wrong password take the same time.
DUMMY_PASSWORD_HASH = hash_password("itp-placeholder-password")


def password_fingerprint(password_hash: str) -> str:
    """A short, non-reversible tag for the password a token was issued under.

    Putting it in the token makes revocation exact: after a password change the
    stored hash differs, so every older token stops matching.  Nothing about the
    password or its hash can be recovered from the tag.
    """
    digest = hashlib.sha256(password_hash.encode("utf-8")).hexdigest()
    return digest[:JWT_FINGERPRINT_LENGTH]


def encode_token(
    secret: str, merchant_id: str, *, hours: int, now: float | None = None,
    password_hash: str | None = None,
) -> tuple[str, int]:
    """Sign a compact JWT for one merchant.  Returns the token and its lifetime."""
    issued = int(now if now is not None else time.time())
    expires_in = max(int(hours), 1) * 3600
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload: dict[str, Any] = {"sub": merchant_id, "iat": issued, "exp": issued + expires_in}
    if password_hash:
        payload["pwd"] = password_fingerprint(password_hash)
    signing_input = f"{_b64url(_json_bytes(header))}.{_b64url(_json_bytes(payload))}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return f"{signing_input}.{_b64url(signature.digest())}", expires_in


def decode_token(secret: str, token: str, *, now: float | None = None) -> dict[str, Any]:
    """Verify a token and return its claims, raising ValueError when invalid."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    header_text, payload_text, signature_text = parts
    signing_input = f"{header_text}.{payload_text}"
    expected = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    try:
        provided = _unb64url(signature_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("malformed signature") from exc
    if not hmac.compare_digest(expected.digest(), provided):
        raise ValueError("bad signature")
    try:
        header = json.loads(_unb64url(header_text))
        payload = json.loads(_unb64url(payload_text))
    except (ValueError, TypeError) as exc:
        raise ValueError("malformed token body") from exc
    if not isinstance(header, dict) or header.get("alg") != JWT_ALGORITHM:
        raise ValueError("unsupported algorithm")
    if not isinstance(payload, dict) or not isinstance(payload.get("sub"), str):
        raise ValueError("missing subject")
    expires = payload.get("exp")
    if not isinstance(expires, int):
        raise ValueError("missing expiry")
    if int(now if now is not None else time.time()) > expires + JWT_LEEWAY_SECONDS:
        raise ValueError("token expired")
    return payload


def _json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")


def save_env_value(path: Path, field: str, value: str) -> None:
    """Write one ``ITP_`` key into an env file, atomically and owner-only.

    Mirrors the settings writer: the previous line for exactly this key is
    dropped, other lines (comments included) stay untouched, then the file is
    replaced in one step with 0600 permissions.
    """
    if path.is_symlink():
        raise OSError("Refusing to replace a symlinked settings file")
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    key = f"ITP_{field.upper()}"
    lines = [
        line
        for line in original.splitlines(keepends=True)
        if not (match := _ENV_KEY.match(line)) or match.group(1) != key
    ]
    content = "".join(lines)
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"{key}={json.dumps(value, ensure_ascii=False)}\n"
    handle, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_jwt_secret(app) -> str:
    """Return the token secret, generating and persisting one on first use.

    The secret is only created when a merchant endpoint is actually used, so a
    deployment that never touches merchant accounts leaves ``.env`` alone.  When
    the file cannot be written the process keeps an in-memory secret: tokens
    still work until the next restart, and the failure is logged rather than
    surfacing as a 500 on a login attempt.
    """
    cached = getattr(app.state, "jwt_secret", None)
    if cached:
        return str(cached)
    configured = app.state.settings.jwt_secret.get_secret_value()
    if configured:
        app.state.jwt_secret = configured
        return configured
    with _secret_lock:
        cached = getattr(app.state, "jwt_secret", None)
        if cached:
            return str(cached)
        configured = app.state.settings.jwt_secret.get_secret_value()
        if configured:
            app.state.jwt_secret = configured
            return configured
        generated = secrets.token_hex(JWT_SECRET_HEX_LENGTH // 2)
        path = Path(app.state.config_path)
        try:
            save_env_value(path, "jwt_secret", generated)
            logger.info("Generated ITP_JWT_SECRET and wrote it to %s", path)
        except OSError:
            logger.warning(
                "Could not write ITP_JWT_SECRET to %s; using a temporary secret that "
                "invalidates every token on restart",
                path,
            )
        app.state.jwt_secret = generated
        return generated


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token or None


def current_merchant(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    """FastAPI dependency: the authenticated merchant row, or 401/403.

    The ``HTTPBearer`` dependency exists to declare the scheme in the OpenAPI
    document (so /docs can send the header); a bare ``Authorization`` header is
    still accepted, and a missing token keeps the same Chinese 401.
    """
    token = credentials.credentials if credentials else bearer_token(request)
    if not token:
        raise HTTPException(
            401, "请先登录商家账号", headers={"WWW-Authenticate": "Bearer"}
        )
    secret = resolve_jwt_secret(request.app)
    try:
        claims = decode_token(secret, token)
    except ValueError as exc:
        raise HTTPException(
            401, "登录状态无效或已过期，请重新登录", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    merchant = request.app.state.merchants.merchant(claims["sub"])
    if not merchant:
        raise HTTPException(
            401, "登录状态无效或已过期，请重新登录", headers={"WWW-Authenticate": "Bearer"}
        )
    # A token carries the password it was issued under, so changing the password
    # revokes everything older without any server-side session list.  Tokens
    # minted before this claim existed carry no tag and keep working.
    fingerprint = claims.get("pwd")
    stored = merchant.get("password_hash") or ""
    if fingerprint and fingerprint != password_fingerprint(stored):
        raise HTTPException(
            401, "密码已修改，请用新密码重新登录", headers={"WWW-Authenticate": "Bearer"}
        )
    if merchant.get("disabled"):
        raise HTTPException(403, "该商家账号已被禁用")
    return merchant
