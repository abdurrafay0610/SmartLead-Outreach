from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 50


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class HealthResponse(BaseModel):
    status: str
    database: str
    redis: str


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
