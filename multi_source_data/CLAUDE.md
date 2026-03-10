# CLAUDE.md — Multi-Source Data Retrieval

This file gives Claude Code full context about the project so it can assist
without needing to re-explore the codebase every session.

---

## Project Overview

A Python tool for pulling tabular data from four sources (Excel/CSV, SharePoint,
OneDrive, Azure Blob/Tables), applying optional Power Query–style transformations,
storing results in SQLite, and serving them via a REST API.

There are **two UIs**:
- **CLI wizard** (`python main.py`) — Rich/Typer interactive terminal wizard
- **Streamlit web app** (`streamlit run app.py`) — browser-based equivalent

---

## Directory Structure

```
multi_source_data/
├── CLAUDE.md                  ← you are here
├── README.md                  ← user-facing docs
├── requirements.txt           ← all Python deps
├── .env.example               ← environment variable template
├── Makefile                   ← common dev commands
│
├── main.py                    ← CLI entry point (Typer + Rich wizard)
├── app.py                     ← Streamlit UI (3-page web app)
├── config.py                  ← Pydantic settings (reads .env)
├── api_client.py              ← drop-in client for external projects
│
├── connectors/
│   ├── __init__.py            ← exports all four connectors
│   ├── base.py                ← BaseConnector (abstract)
│   ├── auth_helper.py         ← MSAL device-code flow helper
│   ├── excel_connector.py     ← local .xlsx / .xls / .csv
│   ├── sharepoint_connector.py← Microsoft Graph (Sites → Drives → Files)
│   ├── onedrive_connector.py  ← Microsoft Graph (OneDrive search)
│   └── azure_connector.py     ← Azure Blob Storage + Data Tables
│
├── database/
│   ├── __init__.py
│   └── db_manager.py          ← SQLAlchemy + SQLite; registry + data storage
│
├── power_query/
│   ├── __init__.py
│   └── query_engine.py        ← pandas transformation pipeline engine
│
└── api/
    ├── __init__.py
    ├── server.py              ← FastAPI app (factory: build_app())
    └── models.py              ← Pydantic response schemas
```

**Generated at runtime** (not in version control):
```
databases/
├── registry.db                ← central dataset registry (DatasetMeta rows)
├── <dataset_name>.db          ← one SQLite file per dataset
└── ...

pipelines/
└── <dataset_name>.json        ← saved Power Query pipeline (optional)
```

---

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in environment variables
cp .env.example .env
# Edit .env — at minimum set MS_CLIENT_ID for SharePoint/OneDrive
```

### Required `.env` keys

| Key | Required for | Default |
|-----|-------------|---------|
| `MS_CLIENT_ID` | SharePoint, OneDrive | `""` (feature disabled) |
| `MS_TENANT_ID` | SharePoint, OneDrive | `"common"` |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure connector | `""` |
| `DATABASE_DIR` | All (data storage) | `./databases` |
| `API_HOST` | API server | `0.0.0.0` |
| `API_PORT` | API server | `8000` |

---

## Running the Project

```bash
# CLI interactive wizard (primary interface)
python main.py

# Streamlit web UI
streamlit run app.py

# Start REST API server
python main.py api
# or directly:
uvicorn api.server:build_app --factory --port 8000 --reload

# List registered datasets
python main.py list

# Refresh a dataset by name
python main.py refresh <dataset_name>
```

### Makefile shortcuts

```bash
make ui        # streamlit run app.py
make api       # uvicorn with --reload
make wizard    # python main.py
make install   # pip install -r requirements.txt
make clean     # delete databases/ and pipelines/
```

---

## Architecture & Data Flow

### Import flow (both CLI and Streamlit follow the same path)

```
User picks source
       │
       ▼
Connector.authenticate()
  ├── Excel:       prompt for file path
  ├── SharePoint:  MSAL device-code → access token
  ├── OneDrive:    MSAL device-code → access token
  └── Azure:       connection string from .env or user input
       │
       ▼
Connector.list_available()  → list[dict] of items
       │
       ▼
User selects one item
       │
       ▼
Connector.load(item)  → pd.DataFrame (raw data)
       │
       ▼
PowerQueryEngine.apply_pipeline(df, pipeline)  → pd.DataFrame (transformed)
       │
       ▼
DatabaseManager.save(meta, df)
  ├── writes DataFrame to ./databases/<name>.db  (table "data")
  └── upserts DatasetMeta row in ./databases/registry.db
```

### Refresh flow (API endpoint or CLI `refresh` command)

```
_do_refresh(meta)
  ├── deserialise extra_json  → connector params
  ├── rebuild Connector instance  (no interactive prompts)
  ├── Connector.load(source_item)  → fresh DataFrame
  ├── reload pipeline from JSON  (if pipeline_path is set)
  ├── PowerQueryEngine.apply_pipeline(df, pipeline)
  └── DatabaseManager.save(meta, df)  → updates row_count + last_refreshed
```

---

## Module Reference

### `config.py`

```python
from config import settings

settings.MS_CLIENT_ID
settings.API_PORT
settings.db_path("my_dataset")   # → Path("./databases/my_dataset.db")
```

Single Pydantic `Settings` instance. All values readable via `settings.<KEY>`.

---

### `connectors/`

All connectors implement `BaseConnector`:

```python
class BaseConnector(ABC):
    def authenticate(self) -> None: ...
    def list_available(self) -> list[dict[str, Any]]: ...
    def load(self, item: dict) -> pd.DataFrame: ...
```

**Item dict keys by connector**:

| Connector | Keys returned by `list_available()` |
|-----------|-------------------------------------|
| Excel | `name`, `type` ("sheet"\|"csv"), `sheet` |
| SharePoint | `id`, `drive_id`, `label`, `name`, `type` ("xlsx"\|"csv") |
| OneDrive | `id`, `name`, `path`, `type` ("xlsx"\|"csv") |
| Azure Blob | `container`, `blob`, `name`, `type` ("csv"\|"xlsx") |
| Azure Tables | `name`, `type` ("table") |

```python
from connectors import ExcelConnector, SharePointConnector, OneDriveConnector, AzureConnector
```

**MSAL device-code flow** lives in `connectors/auth_helper.py → get_access_token()`.
It tries the token cache first, then falls back to the device-code prompt.
In the Streamlit UI the flow is handled inline via `threading.Thread` +
`st.session_state` to avoid blocking the event loop.

---

### `database/db_manager.py`

```python
from database.db_manager import DatabaseManager, DatasetMeta

db = DatabaseManager()           # opens/creates registry.db

# Register a new dataset (or update if name already exists)
meta = db.register(
    name="sales_q1",
    source="sharepoint",         # "excel" | "sharepoint" | "onedrive" | "azure"
    source_item=item_dict,       # the dict from list_available()
    pipeline_path=None,          # str path to JSON pipeline or None
    extra={},                    # connector-specific reconnect params
)

# Write DataFrame to SQLite
meta = db.save(meta, df)         # updates row_count + last_refreshed

# Read data back
df = db.read(meta, limit=100, offset=0, filters={"region": "EU"})

# Other helpers
db.list_all()                    # → list[DatasetMeta]
db.get("sales_q1")               # → DatasetMeta | None
db.delete("sales_q1")            # removes registry row + drops .db file
db.columns(meta)                 # → list[str]  (column names from schema)
```

**DatasetMeta fields**: `id`, `name`, `source`, `source_item_id`,
`source_item_name`, `pipeline_path`, `db_path`, `table_name` (always `"data"`),
`row_count`, `last_refreshed`, `created_at`, `extra_json`.

The `extra_json` field stores JSON-serialised connector params used by
`_do_refresh()` to rebuild the connector without user interaction.

---

### `power_query/query_engine.py`

```python
from power_query.query_engine import PowerQueryEngine, QueryPipeline, QueryStep

engine = PowerQueryEngine()      # pipelines_dir defaults to ./pipelines/

# Build a pipeline programmatically
pipeline = QueryPipeline(
    name="clean_sales",
    steps=[
        QueryStep(op="filter_rows",  params={"column": "status", "operator": "eq", "value": "Active"}),
        QueryStep(op="rename_cols",  params={"mapping": {"old_name": "new_name"}}),
        QueryStep(op="drop_nulls",   params={"columns": ["revenue"]}),
        QueryStep(op="sort",         params={"by": ["date"], "ascending": False}),
    ]
)

# Apply to a DataFrame
result_df = engine.apply_pipeline(df, pipeline)

# Save / load pipeline JSON
pipeline.save(Path("pipelines/clean_sales.json"))
pipeline = QueryPipeline.load(Path("pipelines/clean_sales.json"))
```

**All 17 supported operations**:

| op | Required params | Notes |
|----|----------------|-------|
| `filter_rows` | `column`, `operator`, `value` | operators: eq ne gt lt ge le contains startswith |
| `rename_cols` | `mapping: {old: new, ...}` | |
| `select_cols` | `columns: [...]` | keep only listed columns |
| `remove_cols` | `columns: [...]` | drop listed columns |
| `add_col` | `name`, `expr` | uses `df.eval(expr)` |
| `change_type` | `column`, `dtype` | any pandas dtype string |
| `sort` | `by: [...]`, `ascending: bool` | |
| `group_by` | `by: [...]`, `agg: {result_col: [src_col, func]}` | |
| `drop_nulls` | `columns: [...]\|None` | None = all columns |
| `fill_nulls` | `column`, `value` | |
| `trim_strings` | `columns: [...]` | strip whitespace |
| `to_upper` | `columns: [...]` | |
| `to_lower` | `columns: [...]` | |
| `deduplicate` | `columns: [...]\|None` | |
| `pivot` | `index`, `columns`, `values` | |
| `unpivot` | `id_vars: [...]`, `value_name` | pandas melt |
| `limit_rows` | `n: int` | |

---

### `api/server.py`

```python
from api.server import build_app   # factory pattern
app = build_app()                  # returns FastAPI instance
```

**Endpoints**:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| GET | `/datasets` | List all datasets |
| GET | `/datasets/{name}` | Single dataset metadata |
| GET | `/datasets/{name}/data` | Paginated data (params: `limit`, `offset`, `filters` JSON) |
| POST | `/datasets/{name}/refresh` | Re-pull from source + re-apply pipeline |
| DELETE | `/datasets/{name}` | Remove dataset |

OpenAPI docs auto-generated at `http://localhost:8000/docs`.

---

### `api_client.py`

Drop-in client for other Python projects:

```python
from api_client import DataClient

client = DataClient("http://localhost:8000")

# Query
df = client.get_data("sales_q1", limit=500, filters={"region": "EU"})
df = client.get_all_data("sales_q1")          # fetches all pages

# Manage
client.refresh("sales_q1")
client.list_datasets()
client.delete_dataset("sales_q1")
```

Module-level convenience functions (use a singleton client):

```python
from api_client import list_datasets, get_data, refresh

refresh("sales_q1")
df = get_data("sales_q1", limit=100)
```

---

## Key Design Decisions

1. **Separate SQLite files per dataset** — keeps datasets isolated; deleting one
   doesn't risk others. The central `registry.db` only stores metadata.

2. **`extra_json` for headless refresh** — all information needed to re-pull
   data without user interaction is serialised into `DatasetMeta.extra_json`.
   Adding a new connector means adding its reconnect params here and updating
   `_do_refresh()` in `api/server.py`.

3. **Factory pattern for FastAPI** (`build_app()`) — lets the API be imported
   cleanly and tested without starting a server.

4. **No async in connectors** — connectors use synchronous `requests` /
   `azure-sdk` calls. The FastAPI endpoints use `def` (not `async def`) to
   avoid blocking the event loop on I/O-bound connector calls via a thread pool.

5. **`df.eval()` for `add_col`** — convenient but note it gives access to
   column arithmetic. Don't expose this endpoint publicly without auth.

6. **Streamlit threading for MSAL** — `acquire_token_by_device_flow()` is
   blocking. The Streamlit UI runs it in a `daemon` thread and polls via a
   button click, checking `st.session_state["auth_result"]`.

---

## Adding a New Connector

1. Create `connectors/my_connector.py` implementing `BaseConnector`.
2. Export it in `connectors/__init__.py`.
3. Add it to the source picker in `main.py` (`_wizard()`) and `app.py`.
4. Add reconnect params to `_build_extra()` in `main.py` and `app.py`.
5. Add a new branch in `_do_refresh()` in `api/server.py`.

---

## Adding a New Power Query Operation

1. Add a method `_my_op(self, df, **params)` to `PowerQueryEngine`.
2. Add a branch in `apply_step()` dispatching to that method.
3. Add a case in `_render_op_form()` in `app.py` for the Streamlit form.
4. Add a case in `_prompt_step()` in `query_engine.py` for the CLI prompt.

---

## Common Tasks

### Inspect a dataset without starting the server
```python
from database.db_manager import DatabaseManager
db = DatabaseManager()
meta = db.get("sales_q1")
df = db.read(meta, limit=1000)
print(df.head())
```

### Manually trigger a refresh from Python
```python
from database.db_manager import DatabaseManager
from api.server import _do_refresh
db = DatabaseManager()
meta = db.get("sales_q1")
df, row_count = _do_refresh(meta)
```

### Check what pipeline steps are saved
```python
import json
from pathlib import Path
print(json.loads(Path("pipelines/sales_q1.json").read_text()))
```

### Reset everything
```bash
make clean       # deletes databases/ and pipelines/
```

---

## No Tests Yet

There is currently no test suite. When adding tests:
- Use `pytest`
- Put tests in `tests/`
- The `PowerQueryEngine` and `DatabaseManager` are the best starting points
  since they have no external dependencies at unit-test time
- For connectors, mock the `requests` calls and MSAL flows

---

## Dependency Notes

- **msal ≥ 1.28** — required for `PublicClientApplication` with device-code flow
- **sqlalchemy ≥ 2.0** — uses the 2.x Session/engine style
- **pydantic ≥ 2.6** — uses `model_config = SettingsConfigDict(...)` style
- **streamlit ≥ 1.35** — required for `st.status`, `st.navigation` availability
- **plotly ≥ 5.22** — used in the Datasets chart tab
