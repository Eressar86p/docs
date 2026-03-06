"""
REST API Server (FastAPI)
=========================
Endpoints:

  GET  /                         – health check
  GET  /datasets                 – list all registered datasets
  GET  /datasets/{name}          – metadata for one dataset
  GET  /datasets/{name}/data     – paginated data query
  POST /datasets/{name}/refresh  – re-pull from source and re-apply pipeline
  DELETE /datasets/{name}        – remove dataset from registry + delete DB file

Usage from another Python project:
    import requests
    r = requests.post("http://localhost:8000/datasets/my_dataset/refresh")
    data = requests.get("http://localhost:8000/datasets/my_dataset/data?limit=100")
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.models import DataResponse, DatasetInfo, ErrorResponse, RefreshResponse
from config import settings
from database.db_manager import DatabaseManager, DatasetMeta
from power_query.query_engine import PowerQueryEngine, QueryPipeline

db_mgr = DatabaseManager()
pq_engine = PowerQueryEngine()


def build_app() -> FastAPI:
    app = FastAPI(
        title="Multi-Source Data API",
        description=(
            "Query and refresh datasets pulled from Azure, SharePoint, "
            "OneDrive or Excel."
        ),
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health ─────────────────────────────────────────────────────────────

    @app.get("/", tags=["Health"])
    def root():
        return {"status": "ok", "service": "multi-source-data-api"}

    # ── Dataset list ───────────────────────────────────────────────────────

    @app.get("/datasets", response_model=list[DatasetInfo], tags=["Datasets"])
    def list_datasets():
        """List all registered datasets."""
        return [_to_info(m) for m in db_mgr.list_all()]

    # ── Dataset meta ───────────────────────────────────────────────────────

    @app.get(
        "/datasets/{name}",
        response_model=DatasetInfo,
        responses={404: {"model": ErrorResponse}},
        tags=["Datasets"],
    )
    def get_dataset(name: str):
        meta = _get_or_404(name)
        return _to_info(meta)

    # ── Data query ─────────────────────────────────────────────────────────

    @app.get(
        "/datasets/{name}/data",
        response_model=DataResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Data"],
    )
    def get_data(
        name: str,
        limit: int | None = Query(None, ge=1, le=100_000, description="Max rows to return"),
        offset: int = Query(0, ge=0, description="Row offset"),
        filters: str | None = Query(
            None,
            description='JSON object of column=value filters, e.g. {"status":"Active"}',
        ),
    ):
        """
        Return paginated data from a dataset.

        Example:
            GET /datasets/sales/data?limit=50&offset=0&filters={"region":"EU"}
        """
        meta = _get_or_404(name)
        parsed_filters: dict | None = None
        if filters:
            try:
                parsed_filters = json.loads(filters)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail="filters must be valid JSON")

        df = db_mgr.read(meta, limit=limit, offset=offset, filters=parsed_filters)
        return DataResponse(
            dataset=name,
            columns=list(df.columns),
            rows=df.to_dict(orient="records"),
            total_rows=meta.row_count or 0,
            limit=limit,
            offset=offset,
        )

    # ── Refresh ────────────────────────────────────────────────────────────

    @app.post(
        "/datasets/{name}/refresh",
        response_model=RefreshResponse,
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        tags=["Refresh"],
    )
    def refresh_dataset(name: str, background_tasks: BackgroundTasks):
        """
        Trigger a data refresh for the named dataset.
        Re-pulls from the original source and re-applies the saved pipeline.

        Suitable for calling from any other Python project:

            import requests
            requests.post("http://localhost:8000/datasets/my_data/refresh")
        """
        meta = _get_or_404(name)
        try:
            df, row_count = _do_refresh(meta)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return RefreshResponse(
            dataset=name,
            rows_loaded=row_count,
            refreshed_at=datetime.utcnow(),
            message=f"Dataset '{name}' refreshed successfully with {row_count} rows.",
        )

    # ── Delete ─────────────────────────────────────────────────────────────

    @app.delete(
        "/datasets/{name}",
        responses={404: {"model": ErrorResponse}},
        tags=["Datasets"],
    )
    def delete_dataset(name: str):
        if not db_mgr.get(name):
            raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
        db_mgr.delete(name)
        return {"message": f"Dataset '{name}' deleted."}

    return app


# ── Internal helpers ───────────────────────────────────────────────────────────

def _get_or_404(name: str) -> DatasetMeta:
    meta = db_mgr.get(name)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    return meta


def _to_info(m: DatasetMeta) -> DatasetInfo:
    return DatasetInfo(
        id=m.id,
        name=m.name,
        source=m.source,
        source_item_name=m.source_item_name,
        pipeline_path=m.pipeline_path,
        db_path=m.db_path,
        table_name=m.table_name or "data",
        row_count=m.row_count or 0,
        last_refreshed=m.last_refreshed,
        created_at=m.created_at,
    )


def _do_refresh(meta: DatasetMeta) -> tuple[pd.DataFrame, int]:
    """
    Re-create the connector from stored metadata, pull the data,
    apply the pipeline, and persist to the SQLite DB.
    """
    extra: dict[str, Any] = json.loads(meta.extra_json or "{}")
    source = meta.source

    # ── Re-create connector ──────────────────────────────────────────────
    if source == "excel":
        from connectors import ExcelConnector
        conn = ExcelConnector()
        conn._file_path = __import__("pathlib").Path(extra["file_path"])
    elif source == "onedrive":
        from connectors import OneDriveConnector
        conn = OneDriveConnector()
        conn.authenticate()
    elif source == "sharepoint":
        from connectors import SharePointConnector
        conn = SharePointConnector()
        conn.authenticate()
    elif source == "azure":
        from connectors import AzureConnector
        conn = AzureConnector()
        conn._conn_str = extra.get("connection_string", "")
        conn._service_type = extra.get("service_type", "blob")
    else:
        raise ValueError(f"Unknown source: {source}")

    source_item = {
        "id": meta.source_item_id,
        "name": meta.source_item_name,
        **extra.get("item_extra", {}),
    }

    df = conn.load(source_item)

    # ── Apply pipeline ──────────────────────────────────────────────────
    if meta.pipeline_path and __import__("pathlib").Path(meta.pipeline_path).exists():
        pipeline = QueryPipeline.load(__import__("pathlib").Path(meta.pipeline_path))
        df = pq_engine.apply_pipeline(df, pipeline)

    # ── Persist ──────────────────────────────────────────────────────────
    db_mgr.save(meta, df)
    return df, len(df)
