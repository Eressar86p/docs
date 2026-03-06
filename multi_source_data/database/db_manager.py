"""
Database Manager
================
Manages a SQLite database per dataset.
Each database file lives in DATABASE_DIR/<name>.db
A central registry (registry.db) keeps metadata for all datasets.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session

from config import settings


# ── Registry models ────────────────────────────────────────────────────────────

class _Base(DeclarativeBase):
    pass


class DatasetMeta(_Base):
    """One row per registered dataset."""
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)
    source = Column(String(64), nullable=False)          # excel / sharepoint / …
    source_item_id = Column(String(512))                  # remote file id or path
    source_item_name = Column(String(512))
    pipeline_path = Column(String(512))                   # JSON pipeline file
    db_path = Column(String(512), nullable=False)
    table_name = Column(String(256), default="data")
    row_count = Column(Integer, default=0)
    last_refreshed = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    extra_json = Column(Text, default="{}")               # serialised connector params


def _registry_engine():
    settings.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = settings.DATABASE_DIR / "registry.db"
    engine = create_engine(f"sqlite:///{registry_path}", echo=False)
    _Base.metadata.create_all(engine)
    return engine


# ── Manager ────────────────────────────────────────────────────────────────────

class DatabaseManager:
    """
    CRUD layer for the dataset registry and individual SQLite databases.
    """

    def __init__(self) -> None:
        self._engine = _registry_engine()

    # ── Registration ───────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        source: str,
        source_item: dict[str, Any],
        pipeline_path: str | None,
        extra: dict | None = None,
    ) -> DatasetMeta:
        """Create or update a dataset entry in the registry."""
        db_path = str(settings.db_path(name))
        with Session(self._engine) as session:
            existing = session.query(DatasetMeta).filter_by(name=name).first()
            if existing:
                meta = existing
            else:
                meta = DatasetMeta(name=name)
                session.add(meta)

            meta.source = source
            meta.source_item_id = source_item.get("id", "")
            meta.source_item_name = source_item.get("name", "")
            meta.pipeline_path = pipeline_path or ""
            meta.db_path = db_path
            meta.extra_json = json.dumps(extra or {})
            session.commit()
            session.refresh(meta)
            # Return a detached copy
            row_id = meta.id
        return self.get_by_id(row_id)

    # ── Write data ─────────────────────────────────────────────────────────

    def save(self, meta: DatasetMeta, df: pd.DataFrame) -> DatasetMeta:
        """Write *df* into the dataset's SQLite database."""
        engine = create_engine(f"sqlite:///{meta.db_path}", echo=False)
        df.to_sql(meta.table_name or "data", engine, if_exists="replace", index=False)

        with Session(self._engine) as session:
            obj = session.get(DatasetMeta, meta.id)
            obj.row_count = len(df)
            obj.last_refreshed = datetime.utcnow()
            session.commit()
            session.refresh(obj)
            row_id = obj.id
        return self.get_by_id(row_id)

    # ── Read data ──────────────────────────────────────────────────────────

    def read(
        self,
        meta: DatasetMeta,
        limit: int | None = None,
        offset: int = 0,
        filters: dict | None = None,
    ) -> pd.DataFrame:
        """Read data from the dataset's SQLite database with optional pagination."""
        engine = create_engine(f"sqlite:///{meta.db_path}", echo=False)
        table = meta.table_name or "data"

        sql = f'SELECT * FROM "{table}"'
        params: dict = {}

        if filters:
            clauses = []
            for i, (col, val) in enumerate(filters.items()):
                key = f"p{i}"
                clauses.append(f'"{col}" = :{key}')
                params[key] = val
            sql += " WHERE " + " AND ".join(clauses)

        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"

        with engine.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def columns(self, meta: DatasetMeta) -> list[str]:
        engine = create_engine(f"sqlite:///{meta.db_path}", echo=False)
        insp = inspect(engine)
        table = meta.table_name or "data"
        if not insp.has_table(table):
            return []
        return [c["name"] for c in insp.get_columns(table)]

    # ── Registry queries ───────────────────────────────────────────────────

    def list_all(self) -> list[DatasetMeta]:
        with Session(self._engine) as session:
            rows = session.query(DatasetMeta).all()
            return [self._detach(r) for r in rows]

    def get(self, name: str) -> DatasetMeta | None:
        with Session(self._engine) as session:
            row = session.query(DatasetMeta).filter_by(name=name).first()
            return self._detach(row) if row else None

    def get_by_id(self, row_id: int) -> DatasetMeta | None:
        with Session(self._engine) as session:
            row = session.get(DatasetMeta, row_id)
            return self._detach(row) if row else None

    def delete(self, name: str) -> bool:
        with Session(self._engine) as session:
            row = session.query(DatasetMeta).filter_by(name=name).first()
            if not row:
                return False
            db_path = Path(row.db_path)
            session.delete(row)
            session.commit()
        if db_path.exists():
            db_path.unlink()
        return True

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _detach(obj: DatasetMeta | None) -> DatasetMeta | None:
        """Return a copy of the ORM object with all attributes loaded (detached)."""
        if obj is None:
            return None
        d = DatasetMeta(
            id=obj.id,
            name=obj.name,
            source=obj.source,
            source_item_id=obj.source_item_id,
            source_item_name=obj.source_item_name,
            pipeline_path=obj.pipeline_path,
            db_path=obj.db_path,
            table_name=obj.table_name,
            row_count=obj.row_count,
            last_refreshed=obj.last_refreshed,
            created_at=obj.created_at,
            extra_json=obj.extra_json,
        )
        return d
