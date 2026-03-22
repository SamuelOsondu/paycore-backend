from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard envelope for every API response."""

    success: bool
    message: str
    data: Optional[T] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class PaginatedData(BaseModel, Generic[T]):
    """Wrapper for paginated list results, used as the `data` field in ApiResponse."""

    items: list[T]
    total: int
    limit: int
    offset: int
