"""
api_client.py
=============
A lightweight client you can drop into any other Python project to interact
with the Multi-Source Data API.

Usage example:
    from api_client import DataClient

    client = DataClient("http://localhost:8000")

    # List all datasets
    for ds in client.list_datasets():
        print(ds["name"], ds["row_count"])

    # Get data (returns a pandas DataFrame)
    df = client.get_data("sales_report", limit=500)

    # Refresh (re-pull from source + re-apply pipeline)
    result = client.refresh("sales_report")
    print(result["rows_loaded"])
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import requests


class DataClient:
    """
    HTTP client for the Multi-Source Data API.
    """

    def __init__(self, base_url: str = "http://localhost:8000", timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # ── Health ──────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Returns True if the API server is reachable."""
        try:
            r = self._session.get(f"{self.base_url}/", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    # ── Datasets ────────────────────────────────────────────────────────────

    def list_datasets(self) -> list[dict[str, Any]]:
        """Return metadata for all registered datasets."""
        return self._get("/datasets")

    def get_dataset(self, name: str) -> dict[str, Any]:
        """Return metadata for a single dataset."""
        return self._get(f"/datasets/{name}")

    def delete_dataset(self, name: str) -> dict[str, Any]:
        """Remove a dataset from the registry and delete its SQLite file."""
        r = self._session.delete(f"{self.base_url}/datasets/{name}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ── Data ────────────────────────────────────────────────────────────────

    def get_data(
        self,
        name: str,
        limit: int | None = None,
        offset: int = 0,
        filters: dict | None = None,
    ) -> pd.DataFrame:
        """
        Fetch data from a dataset and return a pandas DataFrame.

        Args:
            name:    Dataset name.
            limit:   Maximum number of rows to return.
            offset:  Row offset for pagination.
            filters: Dict of column → value equality filters.
        """
        params: dict[str, Any] = {"offset": offset}
        if limit is not None:
            params["limit"] = limit
        if filters:
            params["filters"] = json.dumps(filters)

        data = self._get(f"/datasets/{name}/data", params=params)
        return pd.DataFrame(data["rows"], columns=data["columns"])

    def get_all_data(self, name: str, page_size: int = 10_000) -> pd.DataFrame:
        """Paginate through all rows and return a single DataFrame."""
        meta = self.get_dataset(name)
        total = meta.get("row_count", 0)
        frames: list[pd.DataFrame] = []
        offset = 0
        while offset < total or not frames:
            df = self.get_data(name, limit=page_size, offset=offset)
            if df.empty:
                break
            frames.append(df)
            offset += len(df)
            if len(df) < page_size:
                break
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # ── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self, name: str) -> dict[str, Any]:
        """
        Trigger a data refresh – re-pulls from the original source and
        re-applies the saved Power Query pipeline.

        Returns the refresh result dict with keys:
            dataset, rows_loaded, refreshed_at, message
        """
        r = self._session.post(
            f"{self.base_url}/datasets/{name}/refresh",
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    # ── Internal ────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> Any:
        r = self._session.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()


# ── Convenience module-level functions ─────────────────────────────────────────

_default_client: DataClient | None = None


def _client(base_url: str = "http://localhost:8000") -> DataClient:
    global _default_client
    if _default_client is None or _default_client.base_url != base_url:
        _default_client = DataClient(base_url)
    return _default_client


def list_datasets(base_url: str = "http://localhost:8000") -> list[dict]:
    return _client(base_url).list_datasets()


def get_data(
    name: str,
    limit: int | None = None,
    offset: int = 0,
    filters: dict | None = None,
    base_url: str = "http://localhost:8000",
) -> pd.DataFrame:
    return _client(base_url).get_data(name, limit=limit, offset=offset, filters=filters)


def refresh(name: str, base_url: str = "http://localhost:8000") -> dict:
    return _client(base_url).refresh(name)
