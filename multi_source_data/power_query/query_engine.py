"""
Power Query Engine
==================
A pandas-based transformation pipeline that mimics the most common
Power Query (M language) operations.

A *pipeline* is a list of *steps*, each described as a dict:

    {"op": "filter_rows",  "column": "Status", "operator": "eq",  "value": "Active"}
    {"op": "rename_cols",  "mapping": {"old_name": "new_name"}}
    {"op": "select_cols",  "columns": ["id", "name", "value"]}
    {"op": "remove_cols",  "columns": ["temp_col"]}
    {"op": "add_col",      "name": "full_name", "expr": "first_name + ' ' + last_name"}
    {"op": "change_type",  "column": "amount",  "dtype": "float"}
    {"op": "sort",         "by": ["date", "name"], "ascending": [false, true]}
    {"op": "group_by",     "by": ["region"], "agg": {"total": ["amount", "sum"]}}
    {"op": "drop_nulls",   "columns": ["email"]}
    {"op": "fill_nulls",   "column": "score", "value": 0}
    {"op": "trim_strings", "columns": ["name", "city"]}
    {"op": "to_upper",     "columns": ["country"]}
    {"op": "to_lower",     "columns": ["email"]}
    {"op": "deduplicate",  "columns": ["id"]}
    {"op": "pivot",        "index": "region", "columns": "product", "values": "sales"}
    {"op": "unpivot",      "id_vars": ["id", "name"], "value_name": "metric"}
    {"op": "limit_rows",   "n": 1000}

Pipelines are saved as JSON so users can edit / reuse them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


# ── Step descriptor ────────────────────────────────────────────────────────────

@dataclass
class QueryStep:
    op: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"op": self.op, **self.params}

    @staticmethod
    def from_dict(d: dict) -> "QueryStep":
        op = d.pop("op")
        return QueryStep(op=op, params=d)


@dataclass
class QueryPipeline:
    name: str
    steps: list[QueryStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "steps": [s.to_dict() for s in self.steps]}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @staticmethod
    def load(path: Path) -> "QueryPipeline":
        data = json.loads(path.read_text())
        steps = [QueryStep.from_dict(s) for s in data.get("steps", [])]
        return QueryPipeline(name=data["name"], steps=steps)


# ── Engine ─────────────────────────────────────────────────────────────────────

class PowerQueryEngine:
    """
    Interactive builder + executor for transformation pipelines.
    """

    _OPERATIONS = [
        ("filter_rows",  "Filter rows by column condition"),
        ("rename_cols",  "Rename columns"),
        ("select_cols",  "Keep only selected columns"),
        ("remove_cols",  "Remove columns"),
        ("add_col",      "Add calculated column (safe eval)"),
        ("change_type",  "Change column data type"),
        ("sort",         "Sort rows"),
        ("group_by",     "Group by & aggregate"),
        ("drop_nulls",   "Drop rows with null in column(s)"),
        ("fill_nulls",   "Fill nulls with a value"),
        ("trim_strings", "Trim whitespace from string columns"),
        ("to_upper",     "Convert strings to UPPER CASE"),
        ("to_lower",     "Convert strings to lower case"),
        ("deduplicate",  "Remove duplicate rows"),
        ("pivot",        "Pivot table"),
        ("unpivot",      "Unpivot (melt) columns"),
        ("limit_rows",   "Keep first N rows"),
    ]

    def __init__(self, pipelines_dir: Path = Path("./pipelines")) -> None:
        self.pipelines_dir = pipelines_dir
        self.pipelines_dir.mkdir(parents=True, exist_ok=True)

    # ── Interactive builder ────────────────────────────────────────────────

    def interactive_build(self, df: pd.DataFrame, db_name: str) -> QueryPipeline:
        """Walk the user through building a pipeline step-by-step."""
        pipeline_file = self.pipelines_dir / f"{db_name}.json"

        if pipeline_file.exists() and Confirm.ask(
            f"[cyan]Found existing pipeline '{db_name}'.[/cyan] Load it?",
            default=True,
        ):
            pipeline = QueryPipeline.load(pipeline_file)
            console.print(f"[green]✓ Loaded {len(pipeline.steps)} step(s).[/green]")
        else:
            pipeline = QueryPipeline(name=db_name)

        console.print("\n[bold]Current data preview (5 rows):[/bold]")
        self._preview(df)

        while True:
            console.print("\n[bold cyan]Power Query Builder[/bold cyan]")
            console.print("  [bold]a[/bold] – Add a transformation step")
            console.print(f"  [bold]v[/bold] – View current pipeline ({len(pipeline.steps)} steps)")
            console.print("  [bold]p[/bold] – Preview result of current pipeline")
            console.print("  [bold]r[/bold] – Remove last step")
            console.print("  [bold]d[/bold] – Done (apply & continue)")

            cmd = Prompt.ask("Action", choices=["a", "v", "p", "r", "d"], default="d")

            if cmd == "a":
                step = self._prompt_step(df)
                if step:
                    pipeline.steps.append(step)
                    df = self.apply_step(df, step)
                    console.print(f"[green]✓ Step added. Data shape: {df.shape}[/green]")
            elif cmd == "v":
                self._show_pipeline(pipeline)
            elif cmd == "p":
                try:
                    result = self.apply_pipeline(df, pipeline)
                    self._preview(result)
                    console.print(f"Result shape: {result.shape}")
                except Exception as e:
                    console.print(f"[red]Preview error:[/red] {e}")
            elif cmd == "r":
                if pipeline.steps:
                    removed = pipeline.steps.pop()
                    console.print(f"[yellow]Removed step:[/yellow] {removed.op}")
                else:
                    console.print("[yellow]No steps to remove.[/yellow]")
            elif cmd == "d":
                pipeline.save(pipeline_file)
                console.print(f"[green]✓ Pipeline saved to {pipeline_file}[/green]")
                return pipeline

    # ── Apply pipeline / individual steps ────────────────────────────────

    def apply_pipeline(self, df: pd.DataFrame, pipeline: QueryPipeline) -> pd.DataFrame:
        for step in pipeline.steps:
            df = self.apply_step(df, step)
        return df

    def apply_step(self, df: pd.DataFrame, step: QueryStep) -> pd.DataFrame:
        op = step.op
        p = step.params

        if op == "filter_rows":
            df = self._filter_rows(df, p["column"], p["operator"], p["value"])
        elif op == "rename_cols":
            df = df.rename(columns=p["mapping"])
        elif op == "select_cols":
            df = df[p["columns"]]
        elif op == "remove_cols":
            df = df.drop(columns=p["columns"], errors="ignore")
        elif op == "add_col":
            df = df.copy()
            df[p["name"]] = df.eval(p["expr"])
        elif op == "change_type":
            df = df.copy()
            df[p["column"]] = df[p["column"]].astype(p["dtype"])
        elif op == "sort":
            asc = p.get("ascending", True)
            df = df.sort_values(by=p["by"], ascending=asc)
        elif op == "group_by":
            agg: dict[str, Any] = {}
            for new_col, (src_col, func) in p["agg"].items():
                agg[src_col] = func
            df = df.groupby(p["by"], as_index=False).agg(agg)
        elif op == "drop_nulls":
            cols = p.get("columns") or None
            df = df.dropna(subset=cols)
        elif op == "fill_nulls":
            df = df.copy()
            df[p["column"]] = df[p["column"]].fillna(p["value"])
        elif op == "trim_strings":
            for col in p["columns"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip()
        elif op == "to_upper":
            for col in p["columns"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.upper()
        elif op == "to_lower":
            for col in p["columns"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.lower()
        elif op == "deduplicate":
            cols = p.get("columns") or None
            df = df.drop_duplicates(subset=cols)
        elif op == "pivot":
            df = df.pivot_table(
                index=p["index"],
                columns=p["columns"],
                values=p["values"],
                aggfunc="sum",
            ).reset_index()
        elif op == "unpivot":
            df = df.melt(
                id_vars=p["id_vars"],
                var_name=p.get("var_name", "variable"),
                value_name=p.get("value_name", "value"),
            )
        elif op == "limit_rows":
            df = df.head(int(p["n"]))
        else:
            console.print(f"[yellow]Unknown operation '{op}' – skipped.[/yellow]")

        return df.reset_index(drop=True)

    # ── Step prompt ───────────────────────────────────────────────────────

    def _prompt_step(self, df: pd.DataFrame) -> QueryStep | None:
        table = Table(title="Available Operations")
        table.add_column("#", style="cyan")
        table.add_column("Operation")
        table.add_column("Description")
        for i, (op, desc) in enumerate(self._OPERATIONS, 1):
            table.add_row(str(i), op, desc)
        console.print(table)

        choices = [str(i) for i in range(1, len(self._OPERATIONS) + 1)] + ["0"]
        num = Prompt.ask("Choose operation number (0 to cancel)", choices=choices)
        if num == "0":
            return None

        op, _ = self._OPERATIONS[int(num) - 1]
        cols = list(df.columns)

        try:
            if op == "filter_rows":
                col = Prompt.ask("Column", choices=cols)
                ops = ["eq", "ne", "gt", "lt", "ge", "le", "contains", "startswith"]
                operator = Prompt.ask("Operator", choices=ops)
                value = Prompt.ask("Value")
                return QueryStep("filter_rows", {"column": col, "operator": operator, "value": value})

            elif op == "rename_cols":
                mapping = {}
                while True:
                    old = Prompt.ask("Old column name (blank to stop)")
                    if not old:
                        break
                    new = Prompt.ask(f"New name for '{old}'")
                    mapping[old] = new
                return QueryStep("rename_cols", {"mapping": mapping})

            elif op == "select_cols":
                console.print(f"Columns: {cols}")
                selected = Prompt.ask("Comma-separated column names to keep")
                return QueryStep("select_cols", {"columns": [c.strip() for c in selected.split(",")]})

            elif op == "remove_cols":
                console.print(f"Columns: {cols}")
                removed = Prompt.ask("Comma-separated column names to remove")
                return QueryStep("remove_cols", {"columns": [c.strip() for c in removed.split(",")]})

            elif op == "add_col":
                name = Prompt.ask("New column name")
                console.print(f"Available columns: {cols}")
                console.print("Write a pandas eval expression, e.g.: col_a + col_b")
                expr = Prompt.ask("Expression")
                return QueryStep("add_col", {"name": name, "expr": expr})

            elif op == "change_type":
                col = Prompt.ask("Column", choices=cols)
                dtype = Prompt.ask("Target dtype", choices=["str", "int", "float", "bool", "datetime64[ns]"])
                return QueryStep("change_type", {"column": col, "dtype": dtype})

            elif op == "sort":
                by_raw = Prompt.ask("Comma-separated columns to sort by")
                by = [c.strip() for c in by_raw.split(",")]
                asc_raw = Prompt.ask("Ascending? (y/n per column, comma-sep)", default="y")
                ascending = [v.strip().lower() != "n" for v in asc_raw.split(",")]
                if len(ascending) == 1:
                    ascending = ascending[0]
                return QueryStep("sort", {"by": by, "ascending": ascending})

            elif op == "group_by":
                by_raw = Prompt.ask("Group by columns (comma-sep)")
                by = [c.strip() for c in by_raw.split(",")]
                agg: dict = {}
                console.print("Define aggregations. Format: new_col=source_col:func (e.g. total=amount:sum)")
                while True:
                    entry = Prompt.ask("Aggregation (blank to stop)")
                    if not entry:
                        break
                    new_col, rest = entry.split("=", 1)
                    src_col, func = rest.split(":", 1)
                    agg[new_col.strip()] = [src_col.strip(), func.strip()]
                return QueryStep("group_by", {"by": by, "agg": agg})

            elif op == "drop_nulls":
                cols_raw = Prompt.ask("Columns (blank = all)")
                columns = [c.strip() for c in cols_raw.split(",")] if cols_raw else None
                return QueryStep("drop_nulls", {"columns": columns})

            elif op == "fill_nulls":
                col = Prompt.ask("Column", choices=cols)
                value = Prompt.ask("Fill value")
                return QueryStep("fill_nulls", {"column": col, "value": value})

            elif op in {"trim_strings", "to_upper", "to_lower"}:
                console.print(f"Columns: {cols}")
                selected = Prompt.ask("Comma-separated columns")
                return QueryStep(op, {"columns": [c.strip() for c in selected.split(",")]})

            elif op == "deduplicate":
                console.print(f"Columns: {cols}")
                subset_raw = Prompt.ask("Subset columns (blank = all)")
                columns = [c.strip() for c in subset_raw.split(",")] if subset_raw else None
                return QueryStep("deduplicate", {"columns": columns})

            elif op == "pivot":
                index = Prompt.ask("Index column", choices=cols)
                pivot_col = Prompt.ask("Columns field", choices=cols)
                values = Prompt.ask("Values field", choices=cols)
                return QueryStep("pivot", {"index": index, "columns": pivot_col, "values": values})

            elif op == "unpivot":
                id_vars_raw = Prompt.ask("ID columns (keep these, comma-sep)")
                id_vars = [c.strip() for c in id_vars_raw.split(",")]
                value_name = Prompt.ask("Value column name", default="value")
                return QueryStep("unpivot", {"id_vars": id_vars, "value_name": value_name})

            elif op == "limit_rows":
                n = Prompt.ask("Number of rows", default="1000")
                return QueryStep("limit_rows", {"n": int(n)})

        except Exception as e:
            console.print(f"[red]Error building step:[/red] {e}")
            return None

        return None

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _filter_rows(df: pd.DataFrame, column: str, operator: str, value: str) -> pd.DataFrame:
        col = df[column]
        # Try numeric cast
        try:
            num = float(value)
            use_num = True
        except ValueError:
            use_num = False

        v = num if use_num else value

        ops = {
            "eq": col == v,
            "ne": col != v,
            "gt": col > v,
            "lt": col < v,
            "ge": col >= v,
            "le": col <= v,
            "contains": col.astype(str).str.contains(str(value), na=False),
            "startswith": col.astype(str).str.startswith(str(value), na=False),
        }
        mask = ops.get(operator)
        if mask is None:
            raise ValueError(f"Unknown operator: {operator}")
        return df[mask]

    def _show_pipeline(self, pipeline: QueryPipeline) -> None:
        if not pipeline.steps:
            console.print("[yellow]Pipeline is empty.[/yellow]")
            return
        table = Table(title=f"Pipeline: {pipeline.name}")
        table.add_column("#", style="cyan")
        table.add_column("Operation")
        table.add_column("Parameters")
        for i, step in enumerate(pipeline.steps, 1):
            table.add_row(str(i), step.op, str(step.params))
        console.print(table)

    @staticmethod
    def _preview(df: pd.DataFrame, n: int = 5) -> None:
        table = Table(show_lines=True)
        for col in df.columns:
            table.add_column(str(col), overflow="fold", max_width=30)
        for _, row in df.head(n).iterrows():
            table.add_row(*[str(v) for v in row])
        console.print(table)
