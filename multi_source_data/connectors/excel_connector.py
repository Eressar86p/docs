"""Local Excel file connector – no credentials required."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console
from rich.prompt import Prompt

from .base import BaseConnector, DataSource

console = Console()


class ExcelConnector(BaseConnector):
    source = DataSource.EXCEL

    def __init__(self) -> None:
        self._file_path: Path | None = None

    # ── BaseConnector interface ────────────────────────────────────────────

    def authenticate(self) -> None:
        """'Authentication' for local files means choosing / validating the path."""
        while True:
            raw = Prompt.ask(
                "[bold cyan]Enter the path to your Excel file[/bold cyan] "
                "(.xlsx / .xls / .csv)"
            )
            path = Path(raw.strip()).expanduser().resolve()
            if not path.exists():
                console.print(f"[red]File not found:[/red] {path}")
                continue
            if path.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
                console.print(
                    "[red]Unsupported extension.[/red] "
                    "Please provide an .xlsx, .xls, or .csv file."
                )
                continue
            self._file_path = path
            console.print(f"[green]✓ File loaded:[/green] {path.name}")
            break

    def list_available(self) -> list[dict[str, Any]]:
        """
        Returns the list of sheets in the workbook.
        For CSV files returns a single 'Sheet' entry.
        """
        if self._file_path is None:
            raise RuntimeError("Call authenticate() first.")

        if self._file_path.suffix.lower() == ".csv":
            return [{"id": "csv", "name": "CSV Data", "sheet": None}]

        xl = pd.ExcelFile(self._file_path)
        return [
            {"id": name, "name": name, "sheet": name}
            for name in xl.sheet_names
        ]

    def load(self, item: dict[str, Any]) -> pd.DataFrame:
        if self._file_path is None:
            raise RuntimeError("Call authenticate() first.")

        if self._file_path.suffix.lower() == ".csv":
            return pd.read_csv(self._file_path)

        return pd.read_excel(self._file_path, sheet_name=item["sheet"])
