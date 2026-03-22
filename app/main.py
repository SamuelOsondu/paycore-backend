import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.limiter import limiter
from app.core.middleware import RequestIDMiddleware
from app.core.response import error_response, success_response

logging.basicConfig(
    level=logging.DEBUG if settings.is_development else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="PayCore API",
        description="Wallet and Ledger Infrastructure API",
        version="1.0.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
    )

    # ── Rate limiter state ────────────────────────────────────────────────────
    app.state.limiter = limiter

    # ── Middleware ────────────────────────────────────────────────────────────
    # Order matters: RequestID first so every subsequent log has an ID.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_development else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ────────────────────────────────────────────────────
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_response(
                message="Too many requests. Please slow down.",
                error_code="RATE_LIMIT_EXCEEDED",
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # Map FastAPI/Starlette HTTP exceptions (e.g. 401 from oauth2_scheme) to
        # the standard response envelope so clients always get {success, message, error}.
        error_map = {
            401: ("UNAUTHORIZED", "Authentication required."),
            403: ("FORBIDDEN", "Access denied."),
            404: ("NOT_FOUND", "Resource not found."),
            405: ("METHOD_NOT_ALLOWED", "Method not allowed."),
            429: ("TOO_MANY_REQUESTS", "Too many requests. Please slow down."),
        }
        code, default_msg = error_map.get(exc.status_code, ("HTTP_ERROR", str(exc.detail)))
        message = str(exc.detail) if exc.detail and exc.detail != "Not authenticated" else default_msg
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(message=message, error_code=code),
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.error("AppError: %s [%s]", exc.message, exc.error_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(message=exc.message, error_code=exc.error_code),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Flatten Pydantic errors into a readable list
        details = [
            {"field": ".".join(str(l) for l in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_response(
                message="Request validation failed.",
                error_code="VALIDATION_ERROR",
                data=details,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response(
                message="An unexpected error occurred.",
                error_code="INTERNAL_SERVER_ERROR",
            ),
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_router)

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health() -> dict:
        return success_response(
            data={"status": "ok", "environment": settings.ENVIRONMENT},
            message="Service is healthy.",
        )

    return app


app = create_app()
