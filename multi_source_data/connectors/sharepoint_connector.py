"""SharePoint connector using Microsoft Graph API."""
from __future__ import annotations

import io
from typing import Any

import pandas as pd
import requests
from rich.console import Console
from rich.prompt import Prompt

from .auth_helper import get_access_token
from .base import BaseConnector, DataSource

console = Console()
_GRAPH = "https://graph.microsoft.com/v1.0"


class SharePointConnector(BaseConnector):
    source = DataSource.SHAREPOINT

    def __init__(self) -> None:
        self._token: str | None = None
        self._headers: dict = {}
        self._sites: list[dict] = []

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
        """
        Two-step listing:
        1. List SharePoint sites the user follows.
        2. Within each site list Excel/CSV files in document libraries.
        Returns files prefixed with site name.
        """
        if not self._token:
            raise RuntimeError("Call authenticate() first.")

        items: list[dict] = []

        # Followed sites + root site
        sites_data = self._get(f"{_GRAPH}/sites?search=*&$top=20")
        self._sites = sites_data.get("value", [])

        for site in self._sites:
            site_id = site["id"]
            site_name = site.get("displayName") or site.get("name", site_id)

            try:
                # Get document libraries (drives) for this site
                drives_data = self._get(f"{_GRAPH}/sites/{site_id}/drives")
                for drive in drives_data.get("value", []):
                    drive_id = drive["id"]
                    drive_name = drive.get("name", "Documents")

                    # Search Excel files in this drive
                    for ext in [".xlsx", ".csv"]:
                        try:
                            search = self._get(
                                f"{_GRAPH}/drives/{drive_id}/root/search(q='{ext}')"
                                "?$select=id,name,parentReference,size&$top=30"
                            )
                            for f in search.get("value", []):
                                items.append({
                                    "id": f["id"],
                                    "drive_id": drive_id,
                                    "name": f["name"],
                                    "label": f"{site_name} / {drive_name} / {f['name']}",
                                    "type": "csv" if ext == ".csv" else "excel",
                                })
                        except Exception:
                            pass
            except Exception:
                pass

        return items

    def load(self, item: dict[str, Any]) -> pd.DataFrame:
        if not self._token:
            raise RuntimeError("Call authenticate() first.")

        url = f"{_GRAPH}/drives/{item['drive_id']}/items/{item['id']}/content"
        resp = requests.get(url, headers=self._headers, timeout=60)
        resp.raise_for_status()
        content = io.BytesIO(resp.content)

        if item["type"] == "csv":
            return pd.read_csv(content)
        return pd.read_excel(content, engine="openpyxl")
