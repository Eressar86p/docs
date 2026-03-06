"""Pydantic response models for the REST API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DatasetInfo(BaseModel):
    id: int
    name: str
    source: str
    source_item_name: str | None
    pipeline_path: str | None
    db_path: str
    table_name: str
    row_count: int
    last_refreshed: datetime | None
    created_at: datetime | None


class DataResponse(BaseModel):
    dataset: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    limit: int | None
    offset: int


class RefreshResponse(BaseModel):
    dataset: str
    rows_loaded: int
    refreshed_at: datetime
    message: str


class ErrorResponse(BaseModel):
    detail: str
