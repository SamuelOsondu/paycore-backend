from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.limiter import limiter
from app.core.response import success_response
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from app.schemas.common import ApiResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=ApiResponse[RegisterResponse],
    status_code=201,
    summary="Register a new user account",
)
@limiter.limit("10/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create a new user account and return an initial token pair.
    The client does **not** need a separate login call after registration.

    Rate-limited to **10 requests / minute** per IP.
    """
    service = AuthService(db)
    result = await service.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
    )
    return success_response(data=result, message="Account created successfully.")


@router.post(
    "/login",
    response_model=ApiResponse[TokenResponse],
    summary="Authenticate and receive a token pair",
)
@limiter.limit("20/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Authenticate with email + password.
    Returns an access token (30 min) and a refresh token (1 day).

    Rate-limited to **20 requests / minute** per IP.
    """
    service = AuthService(db)
    result = await service.login(email=body.email, password=body.password)
    return success_response(data=result, message="Login successful.")


@router.post(
    "/refresh",
    response_model=ApiResponse[TokenResponse],
    summary="Rotate a refresh token for a fresh token pair",
)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Exchange a valid refresh token for a new access + refresh token pair.
    The old refresh token is immediately revoked (token rotation).
    """
    service = AuthService(db)
    result = await service.refresh(raw_token=body.refresh_token)
    return success_response(data=result, message="Token refreshed.")


@router.post(
    "/logout",
    response_model=ApiResponse[None],
    summary="Revoke the current refresh token",
)
async def logout(
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Revoke the supplied refresh token, ending the session.
    Idempotent — safe to call even if the token is already revoked.
    """
    service = AuthService(db)
    await service.logout(raw_token=body.refresh_token)
    return success_response(data=None, message="Logged out successfully.")
