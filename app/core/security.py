import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

# Pre-computed dummy hash for timing-safe login when the supplied email is not
# found in the database.  Running a real bcrypt verify (even against a fake hash)
# keeps the response time indistinguishable from a "wrong password" path.
# Computed once at module import — ~200 ms at startup, acceptable for a web app.
TIMING_DUMMY_HASH: str = bcrypt.hashpw(b"__timing_dummy__", bcrypt.gensalt(rounds=12)).decode()


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── Token utilities ───────────────────────────────────────────────────────────

def create_access_token(user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token() -> tuple[str, str]:
    """Return (raw_token, hashed_token). Store only the hash."""
    raw = secrets.token_urlsafe(64)
    hashed = _hash_token(raw)
    return raw, hashed


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT. Raises jwt.PyJWTError on any failure.
    Callers must catch and convert to UnauthorizedError.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def _hash_token(raw: str) -> str:
    """SHA-256 hash for safe refresh token storage."""
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_refresh_token(raw: str) -> str:
    return _hash_token(raw)


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a merchant API key.
    Returns (raw_key, prefix, hashed_key).
    raw_key is shown once; store prefix + hashed_key only.
    """
    raw = f"pk_live_{uuid.uuid4().hex}"
    prefix = raw[:8]
    hashed = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=10)).decode()
    return raw, prefix, hashed


def verify_api_key(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(raw.encode(), hashed.encode())
