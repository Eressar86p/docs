# Multi-Source Data Retrieval Tool

Pull data from **Azure**, **SharePoint**, **OneDrive**, or a local **Excel / CSV file**, apply **Power Query**-style transformations, store the result in a local **SQLite database**, and expose everything through a **REST API** that any other Python project can call.

---

## Architecture

```
multi_source_data/
├── main.py               ← Interactive CLI wizard + Typer commands
├── api_client.py         ← Drop-in client for other Python projects
├── config.py             ← Settings (reads from .env)
├── .env.example          ← Copy to .env and fill in credentials
│
├── connectors/
│   ├── excel_connector.py       local .xlsx / .xls / .csv
│   ├── sharepoint_connector.py  Microsoft Graph API
│   ├── onedrive_connector.py    Microsoft Graph API
│   ├── azure_connector.py       Blob Storage + Data Tables
│   └── auth_helper.py           MSAL Device-Code Flow
│
├── power_query/
│   └── query_engine.py   pandas-based transformation pipeline (JSON-saved)
│
├── database/
│   └── db_manager.py     SQLite via SQLAlchemy, dataset registry
│
└── api/
    ├── server.py          FastAPI app with refresh + query endpoints
    └── models.py          Pydantic response models
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env and add your MS_CLIENT_ID (Azure AD app registration)
```

> **Microsoft 365 setup** (SharePoint / OneDrive):
> 1. Go to [portal.azure.com](https://portal.azure.com) → Azure Active Directory → App registrations → New registration
> 2. Set redirect URI to `http://localhost` (type: Public client / native)
> 3. Grant delegated permissions: `User.Read`, `Files.Read`, `Files.Read.All`, `Sites.Read.All`
> 4. Copy the **Application (client) ID** into `MS_CLIENT_ID` in `.env`

### 3. Run the interactive wizard

```bash
python main.py
```

The wizard guides you through:
1. Choose data source (Excel / SharePoint / OneDrive / Azure)
2. Authenticate (device-code login for M365, or connection string for Azure)
3. Browse and select an available file / table
4. Apply Power Query transformations (filter, rename, group, pivot, …)
5. Save to a named SQLite database
6. Optionally start the REST API

---

## CLI commands

```bash
# Interactive wizard (default when no command given)
python main.py

# List all saved datasets
python main.py list

# Refresh a dataset (re-pull + re-apply pipeline)
python main.py refresh my_dataset

# Start the API server
python main.py api
python main.py api --port 9000 --reload   # dev mode
```

---

## REST API

Start the server:

```bash
python main.py api
# → http://localhost:8000
# → http://localhost:8000/docs  (Swagger UI)
```

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/datasets` | List all datasets |
| GET | `/datasets/{name}` | Dataset metadata |
| GET | `/datasets/{name}/data?limit=100&offset=0&filters={"col":"val"}` | Paginated data |
| POST | `/datasets/{name}/refresh` | Re-pull + re-transform |
| DELETE | `/datasets/{name}` | Remove dataset |

---

## Calling from another Python project

Drop `api_client.py` into your project (or install it as a package):

```python
from api_client import DataClient

client = DataClient("http://localhost:8000")

# List datasets
datasets = client.list_datasets()

# Fetch data as a pandas DataFrame
df = client.get_data("sales_report", limit=500, filters={"region": "EU"})

# Trigger a refresh
result = client.refresh("sales_report")
print(result["rows_loaded"])  # → 1234

# Convenience functions (module-level)
from api_client import get_data, refresh, list_datasets

df = get_data("sales_report")
refresh("sales_report")
```

---

## Power Query operations

| Operation | Description |
|-----------|-------------|
| `filter_rows` | Filter by column condition (eq, ne, gt, lt, contains, …) |
| `rename_cols` | Rename one or more columns |
| `select_cols` | Keep only specified columns |
| `remove_cols` | Drop columns |
| `add_col` | Add a calculated column (pandas eval expression) |
| `change_type` | Cast column to str / int / float / bool / datetime |
| `sort` | Sort rows by column(s) |
| `group_by` | Group and aggregate (sum, mean, count, …) |
| `drop_nulls` | Remove rows with nulls |
| `fill_nulls` | Fill nulls with a constant value |
| `trim_strings` | Strip whitespace from string columns |
| `to_upper` / `to_lower` | Case conversion |
| `deduplicate` | Remove duplicate rows |
| `pivot` / `unpivot` | Reshape the table |
| `limit_rows` | Keep first N rows |

Pipelines are saved as JSON in `./pipelines/<dataset_name>.json` and replayed on every refresh.
