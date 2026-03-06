"""OneDrive connector using Microsoft Graph API."""
from __future__ import annotations

import io
from typing import Any

import pandas as pd
import requests
from rich.console import Console

from .auth_helper import get_access_token
from .base import BaseConnector, DataSource

console = Console()
_GRAPH = "https://graph.microsoft.com/v1.0"


class OneDriveConnector(BaseConnector):
    source = DataSource.ONEDRIVE

    def __init__(self) -> None:
        self._token: str | None = None
        self._headers: dict = {}

    # ── helpers ────────────────────────────────────────────────────────────

    def _get(self, url: str, **kwargs) -> dict:
        resp = requests.get(url, headers=self._headers, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ── BaseConnector interface ────────────────────────────────────────────

    def authenticate(self) -> None:
        self._token = get_access_token()
        self._headers = {"Authorization": f"Bearer {self._token}"}
        me = self._get(f"{_GRAPH}/me")
        console.print(
            f"[green]✓ Signed in as:[/green] "
            f"{me.get('displayName')} ({me.get('userPrincipalName')})"
        )

    def list_available(self) -> list[dict[str, Any]]:
        """List Excel / CSV files in the user's OneDrive root (recursive)."""
        if not self._token:
            raise RuntimeError("Call authenticate() first.")

        items: list[dict] = []
        url = (
            f"{_GRAPH}/me/drive/root/search(q='.xlsx')"
            "?$select=id,name,parentReference,size&$top=50"
        )
        data = self._get(url)
        for f in data.get("value", []):
            items.append({
                "id": f["id"],
                "name": f["name"],
                "path": f.get("parentReference", {}).get("path", ""),
                "type": "excel",
            })

        # Also search CSV
        url_csv = (
            f"{_GRAPH}/me/drive/root/search(q='.csv')"
            "?$select=id,name,parentReference,size&$top=50"
        )
        data_csv = self._get(url_csv)
        for f in data_csv.get("value", []):
            items.append({
                "id": f["id"],
                "name": f["name"],
                "path": f.get("parentReference", {}).get("path", ""),
                "type": "csv",
            })

        return items

    def load(self, item: dict[str, Any]) -> pd.DataFrame:
        if not self._token:
            raise RuntimeError("Call authenticate() first.")

        # Download file content
        url = f"{_GRAPH}/me/drive/items/{item['id']}/content"
        resp = requests.get(url, headers=self._headers, timeout=60)
        resp.raise_for_status()
        content = io.BytesIO(resp.content)

        if item["type"] == "csv":
            return pd.read_csv(content)
        return pd.read_excel(content, engine="openpyxl")
