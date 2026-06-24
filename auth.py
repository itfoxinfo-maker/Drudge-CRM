"""Authentication helpers: password hashing and signed session tokens.

Standard library only. Passwords use PBKDF2-HMAC-SHA256; session tokens are
HMAC-signed JSON payloads (a minimal JWT-like scheme).
"""
import hashlib
import hmac
import os
import json
import base64
import time
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("PESTCRM_DATA_DIR", os.path.join(BASE_DIR, "data"))
SECRET_PATH = os.path.join(DATA_DIR, "secret.key")
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days
PBKDF2_ROUNDS = 120_000


def _secret():
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "wb") as f:
            f.write(secrets.token_bytes(32))
    with open(SECRET_PATH, "rb") as f:
        return f.read()


# ---- passwords ----
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---- tokens ----
def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def make_token(user_id: int, token_version: int = 0) -> str:
    # tv (token version) is matched against the user's current token_version,
    # so bumping that column server-side invalidates all existing tokens.
    payload = {"uid": user_id, "tv": token_version, "exp": int(time.time()) + TOKEN_TTL}
    body = _b64e(json.dumps(payload).encode())
    sig = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return body + "." + sig


def verify_token(token: str):
    """Return the token payload dict {uid, tv, exp} if valid, else None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
