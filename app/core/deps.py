import uuid

import jwt
from fastapi import Depends, Header
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Decode the JWT access token and return the active User.
    Raises UnauthorizedError if the token is invalid or the user is inactive.
    """
    from app.models.user import User
    from app.repositories.user import UserRepository

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token has expired.")
    except jwt.PyJWTError:
        raise UnauthorizedError("Invalid token.")

    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type.")

    raw_id = payload.get("sub")
    if not raw_id:
        raise UnauthorizedError("Invalid token payload.")

    try:
        user_id = uuid.UUID(raw_id)
    except ValueError:
        raise UnauthorizedError("Invalid token payload.")

    repo = UserRepository(db)
    user: User | None = await repo.get_by_id(user_id)

    if user is None:
        raise UnauthorizedError("User not found.")
    if not user.is_active:
        raise ForbiddenError("Account is deactivated.")

    return user


def require_role(*roles: str):
    """
    Dependency factory that enforces one of the given roles.
    Usage: Depends(require_role("admin")) or Depends(require_role("admin", "merchant"))
    """
    async def checker(current_user=Depends(get_current_user)):
        if current_user.role.value not in roles:
            raise ForbiddenError("Insufficient permissions.")
        return current_user

    return checker


async def get_merchant_from_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate a merchant via the X-API-Key header.
    Returns the active Merchant or raises UnauthorizedError.
    Used by merchant-facing endpoints (payment confirmation, webhook ingestion).
    """
    from app.services.merchant import MerchantAuthService

    return await MerchantAuthService(db).authenticate(x_api_key)
