#!/usr/bin/env python3
"""
Multi-Source Data Retrieval Tool
=================================
Interactive CLI to pull data from Azure, SharePoint, OneDrive, or a local
Excel file, apply Power Query transformations, persist to a SQLite database,
and expose everything via a REST API.

Usage:
    python main.py                   # interactive mode
    python main.py api               # start the REST API server only
    python main.py refresh <name>    # refresh a dataset by name
    python main.py list              # list all registered datasets
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from config import settings
from connectors import (
    AzureConnector,
    BaseConnector,
    DataSource,
    ExcelConnector,
    OneDriveConnector,
    SharePointConnector,
)
from database.db_manager import DatabaseManager, DatasetMeta
from power_query.query_engine import PowerQueryEngine

console = Console()
app = typer.Typer(help="Multi-Source Data Retrieval Tool", add_completion=False)
db_mgr = DatabaseManager()
pq_engine = PowerQueryEngine()


# ══════════════════════════════════════════════════════════════════════════════
#  CLI commands
# ══════════════════════════════════════════════════════════════════════════════

@app.command("run", help="Interactive data-import wizard (default).")
def run_wizard():
    _wizard()


@app.command("list", help="List all registered datasets.")
def list_cmd():
    _print_datasets(db_mgr.list_all())


@app.command("refresh", help="Refresh a dataset by name.")
def refresh_cmd(name: str = typer.Argument(..., help="Dataset name")):
    meta = db_mgr.get(name)
    if not meta:
        console.print(f"[red]Dataset '{name}' not found.[/red]")
        raise typer.Exit(1)
    _refresh_dataset(meta)


@app.command("api", help="Start the REST API server.")
def api_cmd(
    host: str = typer.Option(settings.API_HOST, help="Bind host"),
    port: int = typer.Option(settings.API_PORT, help="Bind port"),
    reload: bool = typer.Option(False, help="Auto-reload on file changes (dev mode)"),
):
    from api.server import build_app
    console.print(
        Panel(
            f"[bold green]API server starting on http://{host}:{port}[/bold green]\n"
            f"Docs: http://{host}:{port}/docs\n"
            f"OpenAPI JSON: http://{host}:{port}/openapi.json",
            title="Multi-Source Data API",
            border_style="green",
        )
    )
    fastapi_app = build_app()
    uvicorn.run(fastapi_app, host=host, port=port, reload=reload)


# ══════════════════════════════════════════════════════════════════════════════
#  Wizard
# ══════════════════════════════════════════════════════════════════════════════

def _wizard():
    console.print(
        Panel(
            "[bold cyan]Multi-Source Data Retrieval Tool[/bold cyan]\n"
            "Pull data from Azure · SharePoint · OneDrive · Excel\n"
            "Apply Power Query transformations → SQLite database → REST API",
            border_style="cyan",
        )
    )

    # ── 1. Choose source ──────────────────────────────────────────────────
    console.print("\n[bold]Step 1: Choose data source[/bold]")
    source_map = {
        "1": DataSource.EXCEL,
        "2": DataSource.SHAREPOINT,
        "3": DataSource.ONEDRIVE,
        "4": DataSource.AZURE,
    }
    for k, v in source_map.items():
        icon = {"excel": "📊", "sharepoint": "📂", "onedrive": "☁️", "azure": "🔷"}.get(v.value, "")
        console.print(f"  [bold]{k}.[/bold] {icon}  {v.value.title()}")

    choice = Prompt.ask("Select source", choices=list(source_map), default="1")
    source = source_map[choice]

    connector: BaseConnector
    if source == DataSource.EXCEL:
        connector = ExcelConnector()
    elif source == DataSource.SHAREPOINT:
        connector = SharePointConnector()
    elif source == DataSource.ONEDRIVE:
        connector = OneDriveConnector()
    else:
        connector = AzureConnector()

    console.print(f"\n[green]Selected:[/green] {source.value.title()}")

    # ── 2. Authenticate ───────────────────────────────────────────────────
    console.print("\n[bold]Step 2: Authenticate / locate file[/bold]")
    try:
        connector.authenticate()
    except Exception as e:
        console.print(f"[red]Authentication error:[/red] {e}")
        raise typer.Exit(1)

    # ── 3. List available items ───────────────────────────────────────────
    console.print("\n[bold]Step 3: Available items[/bold]")
    with console.status("Fetching available items…"):
        try:
            items = connector.list_available()
        except Exception as e:
            console.print(f"[red]Error listing items:[/red] {e}")
            raise typer.Exit(1)

    if not items:
        console.print("[yellow]No compatible items found for this account / source.[/yellow]")
        raise typer.Exit(0)

    _print_items(items)

    choices = [str(i) for i in range(1, len(items) + 1)]
    sel = Prompt.ask("Select item number", choices=choices)
    selected_item = items[int(sel) - 1]
    console.print(
        f"\n[green]Selected:[/green] "
        f"{selected_item.get('label') or selected_item.get('name')}"
    )

    # ── 4. Load data ──────────────────────────────────────────────────────
    console.print("\n[bold]Step 4: Loading data…[/bold]")
    with console.status("Downloading / reading data…"):
        try:
            df = connector.load(selected_item)
        except Exception as e:
            console.print(f"[red]Error loading data:[/red] {e}")
            raise typer.Exit(1)

    console.print(f"[green]✓ Loaded {len(df):,} rows × {len(df.columns)} columns.[/green]")

    # ── 5. Name the dataset ───────────────────────────────────────────────
    default_name = _safe_name(selected_item.get("name", "dataset"))
    db_name = Prompt.ask("\n[bold]Step 5: Name this dataset[/bold]", default=default_name)
    db_name = _safe_name(db_name)

    # ── 6. Power Query ────────────────────────────────────────────────────
    console.print("\n[bold]Step 6: Power Query transformations[/bold]")
    if Confirm.ask("Do you want to apply transformations?", default=True):
        pipeline = pq_engine.interactive_build(df, db_name)
        df = pq_engine.apply_pipeline(df, pipeline)
        pipeline_path = str(pq_engine.pipelines_dir / f"{db_name}.json")
    else:
        pipeline_path = None

    console.print(f"\n[green]✓ Final dataset: {len(df):,} rows × {len(df.columns)} columns.[/green]")

    # ── 7. Build extra persistence metadata ───────────────────────────────
    extra = _build_extra(source, connector, selected_item)

    # ── 8. Register & save ────────────────────────────────────────────────
    console.print("\n[bold]Step 7: Saving to database…[/bold]")
    meta = db_mgr.register(
        name=db_name,
        source=source.value,
        source_item=selected_item,
        pipeline_path=pipeline_path,
        extra=extra,
    )
    meta = db_mgr.save(meta, df)

    console.print(
        Panel(
            f"[bold green]✓ Dataset '{db_name}' saved![/bold green]\n"
            f"  Rows: {meta.row_count:,}\n"
            f"  DB:   {meta.db_path}\n"
            f"  Pipeline: {meta.pipeline_path or '(none)'}",
            border_style="green",
        )
    )

    # ── 9. Offer to start API / refresh options ────────────────────────────
    console.print("\n[bold]What would you like to do next?[/bold]")
    console.print("  [bold]1.[/bold] Start the REST API server")
    console.print("  [bold]2.[/bold] Refresh this dataset now")
    console.print("  [bold]3.[/bold] Import another dataset")
    console.print("  [bold]4.[/bold] Exit")

    next_choice = Prompt.ask("Choose", choices=["1", "2", "3", "4"], default="1")

    if next_choice == "1":
        _start_api()
    elif next_choice == "2":
        _refresh_dataset(meta)
    elif next_choice == "3":
        _wizard()


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _start_api():
    from api.server import build_app
    host, port = settings.API_HOST, settings.API_PORT
    console.print(
        Panel(
            f"[bold green]Starting API on http://{host}:{port}[/bold green]\n"
            f"Interactive docs: http://{host}:{port}/docs\n\n"
            "[bold]Useful endpoints:[/bold]\n"
            "  GET  /datasets\n"
            "  GET  /datasets/<name>/data\n"
            "  POST /datasets/<name>/refresh",
            title="REST API",
            border_style="green",
        )
    )
    fastapi_app = build_app()
    uvicorn.run(fastapi_app, host=host, port=port)


def _refresh_dataset(meta: DatasetMeta):
    """Re-pull + re-transform + re-save a dataset."""
    from api.server import _do_refresh
    console.print(f"\n[cyan]Refreshing dataset '{meta.name}'…[/cyan]")
    try:
        df, row_count = _do_refresh(meta)
        console.print(f"[green]✓ Refreshed! {row_count:,} rows.[/green]")
    except Exception as e:
        console.print(f"[red]Refresh error:[/red] {e}")


def _print_items(items: list[dict]):
    table = Table(title=f"Available items ({len(items)} found)")
    table.add_column("#", style="cyan", width=4)
    table.add_column("Name / Label", overflow="fold")
    table.add_column("Type", width=8)
    for i, item in enumerate(items, 1):
        label = item.get("label") or item.get("name") or item.get("id", "")
        itype = item.get("type", "")
        table.add_row(str(i), label, itype)
    console.print(table)


def _print_datasets(metas: list[DatasetMeta]):
    if not metas:
        console.print("[yellow]No datasets registered yet.[/yellow]")
        return
    table = Table(title="Registered Datasets")
    table.add_column("Name", style="cyan")
    table.add_column("Source")
    table.add_column("Rows", justify="right")
    table.add_column("Last Refreshed")
    table.add_column("DB Path", overflow="fold")
    for m in metas:
        refreshed = str(m.last_refreshed)[:19] if m.last_refreshed else "never"
        table.add_row(m.name, m.source, str(m.row_count or 0), refreshed, m.db_path)
    console.print(table)


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_]", "_", Path(name).stem).strip("_") or "dataset"


def _build_extra(source: DataSource, connector: BaseConnector, item: dict) -> dict:
    """Collect whatever is needed to reconnect to the source on refresh."""
    extra: dict[str, Any] = {}

    if source == DataSource.EXCEL:
        extra["file_path"] = str(connector._file_path)  # type: ignore[attr-defined]

    elif source == DataSource.AZURE:
        extra["connection_string"] = connector._conn_str  # type: ignore[attr-defined]
        extra["service_type"] = connector._service_type   # type: ignore[attr-defined]
        # Persist any item-specific extra needed for reload
        if "drive_id" in item:
            extra.setdefault("item_extra", {})["drive_id"] = item["drive_id"]
        if "container" in item:
            extra.setdefault("item_extra", {})["container"] = item["container"]
            extra.setdefault("item_extra", {})["blob"] = item["blob"]

    elif source in {DataSource.SHAREPOINT, DataSource.ONEDRIVE}:
        # Token will be re-acquired via device-flow on refresh
        if "drive_id" in item:
            extra.setdefault("item_extra", {})["drive_id"] = item["drive_id"]
        if "type" in item:
            extra.setdefault("item_extra", {})["type"] = item["type"]

    extra["item_type"] = item.get("type", "")
    return extra


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # No subcommand → run wizard directly
        _wizard()
    else:
        app()
