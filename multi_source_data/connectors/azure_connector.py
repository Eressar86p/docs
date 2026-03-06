"""
Azure connector – supports:
  • Azure Blob Storage  (CSV / Excel blobs)
  • Azure Data Tables
"""
from __future__ import annotations

import io
from typing import Any

import pandas as pd
from rich.console import Console
from rich.prompt import Prompt

from .base import BaseConnector, DataSource
from config import settings

console = Console()


class AzureConnector(BaseConnector):
    source = DataSource.AZURE

    def __init__(self) -> None:
        self._conn_str: str | None = None
        self._service_type: str | None = None   # "blob" or "table"

    # ── BaseConnector interface ────────────────────────────────────────────

    def authenticate(self) -> None:
        console.print("\n[bold cyan]Azure Service Type[/bold cyan]")
        console.print("  [bold]1.[/bold] Blob Storage  (CSV / Excel files)")
        console.print("  [bold]2.[/bold] Data Tables")
        choice = Prompt.ask("Select", choices=["1", "2"], default="1")
        self._service_type = "blob" if choice == "1" else "table"

        conn_str = settings.AZURE_STORAGE_CONNECTION_STRING
        if not conn_str:
            conn_str = Prompt.ask(
                "[bold cyan]Azure Storage connection string[/bold cyan]\n"
                "  (find it in Azure Portal > Storage Account > Access keys)"
            ).strip()
        if not conn_str:
            raise RuntimeError("Azure Storage connection string is required.")
        self._conn_str = conn_str
        console.print("[green]✓ Azure credentials accepted.[/green]")

    def list_available(self) -> list[dict[str, Any]]:
        if not self._conn_str:
            raise RuntimeError("Call authenticate() first.")

        if self._service_type == "blob":
            return self._list_blobs()
        return self._list_tables()

    def load(self, item: dict[str, Any]) -> pd.DataFrame:
        if not self._conn_str:
            raise RuntimeError("Call authenticate() first.")

        if self._service_type == "blob":
            return self._load_blob(item)
        return self._load_table(item)

    # ── Blob helpers ──────────────────────────────────────────────────────

    def _list_blobs(self) -> list[dict[str, Any]]:
        from azure.storage.blob import BlobServiceClient  # type: ignore

        client = BlobServiceClient.from_connection_string(self._conn_str)
        items: list[dict] = []
        for container in client.list_containers():
            cname = container["name"]
            cc = client.get_container_client(cname)
            for blob in cc.list_blobs():
                bname: str = blob["name"]
                if bname.lower().endswith((".csv", ".xlsx", ".xls")):
                    items.append({
                        "id": f"{cname}/{bname}",
                        "name": bname,
                        "label": f"{cname} / {bname}",
                        "container": cname,
                        "blob": bname,
                        "type": "csv" if bname.lower().endswith(".csv") else "excel",
                    })
        return items

    def _load_blob(self, item: dict[str, Any]) -> pd.DataFrame:
        from azure.storage.blob import BlobServiceClient  # type: ignore

        client = BlobServiceClient.from_connection_string(self._conn_str)
        blob_client = client.get_blob_client(
            container=item["container"], blob=item["blob"]
        )
        data = blob_client.download_blob().readall()
        buf = io.BytesIO(data)
        if item["type"] == "csv":
            return pd.read_csv(buf)
        return pd.read_excel(buf, engine="openpyxl")

    # ── Table helpers ─────────────────────────────────────────────────────

    def _list_tables(self) -> list[dict[str, Any]]:
        from azure.data.tables import TableServiceClient  # type: ignore

        client = TableServiceClient.from_connection_string(self._conn_str)
        return [
            {"id": t["name"], "name": t["name"], "label": t["name"], "type": "table"}
            for t in client.list_tables()
        ]

    def _load_table(self, item: dict[str, Any]) -> pd.DataFrame:
        from azure.data.tables import TableServiceClient  # type: ignore

        client = TableServiceClient.from_connection_string(self._conn_str)
        tc = client.get_table_client(item["name"])
        rows = list(tc.list_entities())
        return pd.DataFrame(rows)
