class AppError(Exception):
    """Base exception for all application errors."""

    def __init__(self, message: str, error_code: str, status_code: int = 400) -> None:
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, resource: str) -> None:
        super().__init__(
            message=f"{resource} not found.",
            error_code=f"{resource.upper().replace(' ', '_')}_NOT_FOUND",
            status_code=404,
        )


class ConflictError(AppError):
    def __init__(self, message: str, error_code: str = "CONFLICT") -> None:
        super().__init__(message=message, error_code=error_code, status_code=409)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__(message=message, error_code="UNAUTHORIZED", status_code=401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Access denied.") -> None:
        super().__init__(message=message, error_code="FORBIDDEN", status_code=403)


class ValidationError(AppError):
    def __init__(self, message: str, error_code: str = "VALIDATION_ERROR") -> None:
        super().__init__(message=message, error_code=error_code, status_code=422)


class InsufficientBalanceError(AppError):
    def __init__(self) -> None:
        super().__init__(
            message="Insufficient wallet balance.",
            error_code="INSUFFICIENT_BALANCE",
            status_code=422,
        )


class KYCTierError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_code="KYC_TIER_INSUFFICIENT", status_code=403)


class DailyLimitError(AppError):
    def __init__(self) -> None:
        super().__init__(
            message="Daily transaction limit exceeded for your KYC tier.",
            error_code="DAILY_LIMIT_EXCEEDED",
            status_code=422,
        )


class DuplicateTransferError(AppError):
    def __init__(self) -> None:
        super().__init__(
            message="Duplicate transfer detected. Please wait before retrying.",
            error_code="DUPLICATE_TRANSFER",
            status_code=429,
        )


class ExternalServiceError(AppError):
    def __init__(self, service: str) -> None:
        super().__init__(
            message=f"{service} is temporarily unavailable. Please try again.",
            error_code="EXTERNAL_SERVICE_ERROR",
            status_code=503,
        )
