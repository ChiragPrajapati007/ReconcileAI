"""
Shared schema primitives used across all API endpoints.
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PaginatedResponse(BaseModel, Generic[T]):
    """Wrapper for paginated list endpoints."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    page: int
    page_size: int
    total: int
