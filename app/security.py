"""Small, dependency-light helpers for password storage and same-origin sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def session_secret(data_dir: Path) -> bytes:
    configured = os.getenv("SESSION_SECRET", "").strip()
    if configured:
        if len(configured) < 32:
            raise RuntimeError("SESSION_SECRET must contain at least 32 characters.")
        return configured.encode("utf-8")

    secret_path = data_dir / "session.secret"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        return secret_path.read_bytes()
    except FileNotFoundError:
        secret = secrets.token_bytes(48)
        try:
            with secret_path.open("xb") as handle:
                handle.write(secret)
        except FileExistsError:
            return secret_path.read_bytes()
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
        return secret


def create_session(user: dict[str, Any], secret: bytes, lifetime_seconds: int = 8 * 60 * 60) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "iat": now,
        "exp": now + lifetime_seconds,
    }
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{body}"
    signature = _b64(hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def read_session(token: str, secret: bytes) -> dict[str, Any] | None:
    try:
        if len(token) > 4096:
            return None
        header, body, signature = token.split(".", 2)
        header_data = json.loads(_unb64(header))
        if not isinstance(header_data, dict) or header_data.get("alg") != "HS256" or header_data.get("typ") != "JWT":
            return None
        signing_input = f"{header}.{body}"
        expected = _b64(hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_unb64(body))
        if not isinstance(payload, dict):
            return None
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        if not payload.get("sub") or payload.get("role") not in {"APPLICANT", "ANALYST", "ADMIN"}:
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1${}${}".format(_b64(salt), _b64(derived))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected = _unb64(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_text),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False
