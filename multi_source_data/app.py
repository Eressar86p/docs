"""
Multi-Source Data Retrieval — Streamlit UI
==========================================
Run with:
    streamlit run app.py
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

# ── Make sure the package root is on the path ─────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from database.db_manager import DatabaseManager, DatasetMeta
from power_query.query_engine import PowerQueryEngine, QueryPipeline, QueryStep

db_mgr = DatabaseManager()
pq_engine = PowerQueryEngine()

# ══════════════════════════════════════════════════════════════════════════════
#  Page config
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Multi-Source Data",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Minimal custom CSS ────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* Slightly tighter metric cards */
    div[data-testid="metric-container"] {
        background: #1e2130;
        border: 1px solid #2e3250;
        border-radius: 10px;
        padding: 14px 18px;
    }
    /* Step badge */
    .step-badge {
        display: inline-block;
        background: #4f6ef7;
        color: white;
        border-radius: 50%;
        width: 28px; height: 28px;
        text-align: center;
        line-height: 28px;
        font-weight: 700;
        margin-right: 8px;
        font-size: 14px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Session-state helpers
# ══════════════════════════════════════════════════════════════════════════════

def ss(key: str, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def reset_import():
    for k in [
        "connector", "items", "selected_item", "raw_df",
        "pipeline_steps", "device_flow", "msal_app",
        "auth_thread", "auth_result", "auth_done",
        "import_done", "final_meta",
    ]:
        st.session_state.pop(k, None)


# ══════════════════════════════════════════════════════════════════════════════
#  Sidebar navigation
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## 🔄 Multi-Source Data")
    st.divider()
    page = st.radio(
        "Navigation",
        ["📥  Import Data", "🗄️  My Datasets", "🌐  API & Integration"],
        label_visibility="collapsed",
    )
    st.divider()

    datasets = db_mgr.list_all()
    st.caption(f"**{len(datasets)}** dataset(s) registered")
    for m in datasets:
        refreshed = m.last_refreshed.strftime("%d %b %H:%M") if m.last_refreshed else "never"
        st.caption(f"• `{m.name}` — {m.row_count:,} rows — {refreshed}")

    st.divider()
    if st.button("🔁  Start new import", use_container_width=True):
        reset_import()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 1 — Import Data
# ══════════════════════════════════════════════════════════════════════════════

if page == "📥  Import Data":
    st.title("📥 Import Data")
    st.caption("Pull from Azure, SharePoint, OneDrive, or a local Excel file.")

    # ── Step 1: Choose source ─────────────────────────────────────────────
    st.markdown('<span class="step-badge">1</span> **Choose data source**', unsafe_allow_html=True)

    source_options = {
        "📊  Excel / CSV (local file)": "excel",
        "📂  SharePoint": "sharepoint",
        "☁️  OneDrive": "onedrive",
        "🔷  Azure (Blob / Tables)": "azure",
    }
    source_label = st.selectbox(
        "Data source",
        list(source_options.keys()),
        label_visibility="collapsed",
        key="source_label",
    )
    source = source_options[source_label]

    st.divider()

    # ── Step 2: Authenticate / locate ────────────────────────────────────
    st.markdown('<span class="step-badge">2</span> **Connect**', unsafe_allow_html=True)

    if source == "excel":
        _upload_file(source)
    elif source == "azure":
        _azure_connect(source)
    else:
        _m365_connect(source)

    # ── Step 3 onward only if connector is ready ─────────────────────────
    if st.session_state.get("connector") and st.session_state.get("auth_done"):
        _step_select_item(source)

    if st.session_state.get("selected_item") is not None and st.session_state.get("raw_df") is not None:
        _step_power_query()

    if st.session_state.get("import_done"):
        _step_done()


# ──────────────────────────────────────────────────────────────────────────────
# Helper sections (called from the Import page)
# ──────────────────────────────────────────────────────────────────────────────

def _upload_file(source):
    uploaded = st.file_uploader(
        "Upload your Excel or CSV file",
        type=["xlsx", "xls", "csv"],
        key="excel_upload",
    )
    if uploaded:
        from connectors import ExcelConnector
        conn = ExcelConnector()
        # Save to a temp path so the connector can reference it on refresh
        tmp = Path(f"/tmp/{uploaded.name}")
        tmp.write_bytes(uploaded.read())
        conn._file_path = tmp
        st.session_state["connector"] = conn
        st.session_state["auth_done"] = True
        st.success(f"✅ File loaded: **{uploaded.name}**")


def _azure_connect(source):
    with st.form("azure_form"):
        svc = st.radio("Service type", ["Blob Storage (CSV/Excel)", "Data Tables"], horizontal=True)
        conn_str = st.text_input(
            "Connection string",
            type="password",
            placeholder="DefaultEndpointsProtocol=https;AccountName=…",
            help="Azure Portal → Storage Account → Access keys",
        )
        submitted = st.form_submit_button("Connect", use_container_width=True, type="primary")

    if submitted:
        if not conn_str:
            st.error("Please enter a connection string.")
        else:
            from connectors import AzureConnector
            conn = AzureConnector()
            conn._conn_str = conn_str
            conn._service_type = "blob" if "Blob" in svc else "table"
            st.session_state["connector"] = conn
            st.session_state["auth_done"] = True
            st.success("✅ Azure connection accepted.")


def _m365_connect(source):
    """Handle MSAL device-code flow inside Streamlit."""
    if not settings.MS_CLIENT_ID:
        st.warning(
            "**MS_CLIENT_ID is not set.**  "
            "Add your Azure AD App Registration Client ID to `.env`.",
            icon="⚠️",
        )
        with st.expander("How to get a Client ID"):
            st.markdown(
                """
1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**
2. Set redirect URI to `http://localhost` (type: *Public client / native*)
3. Grant delegated permissions: `User.Read`, `Files.Read`, `Files.Read.All`, `Sites.Read.All`
4. Copy the **Application (client) ID** into your `.env` as `MS_CLIENT_ID`
                """
            )
        return

    if not st.session_state.get("auth_done"):
        if not st.session_state.get("device_flow"):
            if st.button("🔑  Sign in with Microsoft 365", type="primary"):
                import msal
                msal_app = msal.PublicClientApplication(
                    client_id=settings.MS_CLIENT_ID,
                    authority=f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}",
                )
                flow = msal_app.initiate_device_flow(
                    scopes=["User.Read", "Files.Read", "Files.Read.All", "Sites.Read.All"]
                )
                if "user_code" not in flow:
                    st.error(f"Could not start sign-in: {flow.get('error_description')}")
                    return

                st.session_state["msal_app"] = msal_app
                st.session_state["device_flow"] = flow

                # Kick off token acquisition in a background thread
                def _acquire():
                    result = msal_app.acquire_token_by_device_flow(flow)
                    st.session_state["auth_result"] = result

                t = threading.Thread(target=_acquire, daemon=True)
                t.start()
                st.session_state["auth_thread"] = t
                st.rerun()

        else:
            flow = st.session_state["device_flow"]
            st.info(
                f"**Step 1** — Open this URL in your browser:  \n"
                f"👉 [{flow['verification_uri']}]({flow['verification_uri']})\n\n"
                f"**Step 2** — Enter the code below when prompted:"
            )
            st.code(flow["user_code"], language=None)

            col1, col2 = st.columns([1, 3])
            with col1:
                if st.button("✅  I've signed in", type="primary"):
                    thread: threading.Thread = st.session_state.get("auth_thread")
                    result = st.session_state.get("auth_result")

                    if result is None and thread and thread.is_alive():
                        st.warning("Still waiting for the token… please wait a moment and try again.")
                    elif result and "access_token" in result:
                        # Build the connector with the acquired token
                        _build_m365_connector(source, result["access_token"])
                    else:
                        err = (result or {}).get("error_description", "Unknown error")
                        st.error(f"Authentication failed: {err}")
            with col2:
                st.caption("After entering the code in the browser click the button above.")
    else:
        st.success("✅ Signed in to Microsoft 365.")


def _build_m365_connector(source: str, token: str):
    if source == "sharepoint":
        from connectors import SharePointConnector
        conn = SharePointConnector()
        conn._token = token
        conn._headers = {"Authorization": f"Bearer {token}"}
    else:
        from connectors import OneDriveConnector
        conn = OneDriveConnector()
        conn._token = token
        conn._headers = {"Authorization": f"Bearer {token}"}

    st.session_state["connector"] = conn
    st.session_state["auth_done"] = True
    st.rerun()


def _step_select_item(source: str):
    st.divider()
    st.markdown('<span class="step-badge">3</span> **Select item**', unsafe_allow_html=True)

    if "items" not in st.session_state:
        with st.spinner("Fetching available items…"):
            try:
                items = st.session_state["connector"].list_available()
                st.session_state["items"] = items
            except Exception as e:
                st.error(f"Error listing items: {e}")
                return

    items: list[dict] = st.session_state["items"]
    if not items:
        st.warning("No compatible items found for this account.")
        return

    labels = [i.get("label") or i.get("name") or i.get("id", f"Item {n}") for n, i in enumerate(items, 1)]
    sel_label = st.selectbox(f"{len(items)} item(s) found", labels, key="item_selectbox")
    sel_idx = labels.index(sel_label)
    selected = items[sel_idx]

    if st.button("📂  Load selected item", type="primary"):
        with st.spinner("Loading data…"):
            try:
                df = st.session_state["connector"].load(selected)
                st.session_state["selected_item"] = selected
                st.session_state["raw_df"] = df
                st.session_state.pop("pipeline_steps", None)
                st.rerun()
            except Exception as e:
                st.error(f"Error loading data: {e}")


def _step_power_query():
    df: pd.DataFrame = st.session_state["raw_df"]
    st.divider()
    st.markdown('<span class="step-badge">4</span> **Power Query transformations**', unsafe_allow_html=True)

    col_l, col_r = st.columns([1, 2])
    with col_l:
        st.metric("Rows", f"{len(df):,}")
    with col_r:
        st.metric("Columns", len(df.columns))

    steps: list[dict] = ss("pipeline_steps", [])

    # ── Current pipeline ──────────────────────────────────────────────────
    if steps:
        with st.expander(f"Current pipeline — {len(steps)} step(s)", expanded=True):
            for i, s in enumerate(steps):
                c1, c2 = st.columns([5, 1])
                op_params = {k: v for k, v in s.items() if k != "op"}
                c1.markdown(f"`{i+1}.` **{s['op']}**  `{json.dumps(op_params)}`")
                if c2.button("✕", key=f"rm_step_{i}", help="Remove this step"):
                    steps.pop(i)
                    st.session_state["pipeline_steps"] = steps
                    st.rerun()

    # ── Add step form ─────────────────────────────────────────────────────
    _OPS = [
        "filter_rows", "rename_cols", "select_cols", "remove_cols",
        "add_col", "change_type", "sort", "group_by",
        "drop_nulls", "fill_nulls", "trim_strings", "to_upper",
        "to_lower", "deduplicate", "pivot", "unpivot", "limit_rows",
    ]
    cols = list(df.columns)

    with st.expander("➕ Add a transformation step"):
        op = st.selectbox("Operation", _OPS, key="new_op")
        params = _render_op_form(op, cols)
        if st.button("Add step", key="add_step_btn", type="primary"):
            step = {"op": op, **params}
            steps.append(step)
            st.session_state["pipeline_steps"] = steps
            st.rerun()

    # ── Preview ───────────────────────────────────────────────────────────
    try:
        pipeline = QueryPipeline(name="_preview", steps=[QueryStep.from_dict(dict(s)) for s in steps])
        result_df = pq_engine.apply_pipeline(df.copy(), pipeline)
    except Exception as e:
        st.error(f"Pipeline error: {e}")
        result_df = df

    with st.expander("🔍 Data preview", expanded=True):
        st.dataframe(result_df.head(100), use_container_width=True)
        st.caption(f"Showing up to 100 of {len(result_df):,} rows after transformations.")

    # ── Name & Save ───────────────────────────────────────────────────────
    st.divider()
    st.markdown('<span class="step-badge">5</span> **Save to database**', unsafe_allow_html=True)

    item_name = st.session_state.get("selected_item", {}).get("name", "dataset")
    import re
    default_name = re.sub(r"[^a-zA-Z0-9_]", "_", Path(item_name).stem).strip("_") or "dataset"

    db_name = st.text_input("Dataset name", value=default_name, key="db_name_input")

    if st.button("💾  Save dataset", type="primary", key="save_dataset_btn"):
        with st.spinner("Applying pipeline and saving…"):
            try:
                # Save pipeline JSON
                pipeline_path: str | None = None
                if steps:
                    pl = QueryPipeline(
                        name=db_name,
                        steps=[QueryStep.from_dict(dict(s)) for s in steps],
                    )
                    pl.save(pq_engine.pipelines_dir / f"{db_name}.json")
                    pipeline_path = str(pq_engine.pipelines_dir / f"{db_name}.json")

                # Build extra metadata for refresh
                source = st.session_state.get("source_label", "")
                connector = st.session_state.get("connector")
                selected_item = st.session_state["selected_item"]
                extra = _build_extra_meta(connector, selected_item)

                meta = db_mgr.register(
                    name=db_name,
                    source=_source_key(source),
                    source_item=selected_item,
                    pipeline_path=pipeline_path,
                    extra=extra,
                )
                meta = db_mgr.save(meta, result_df)
                st.session_state["import_done"] = True
                st.session_state["final_meta"] = meta
                st.rerun()
            except Exception as e:
                st.error(f"Save error: {e}")


def _step_done():
    meta: DatasetMeta = st.session_state["final_meta"]
    st.success(
        f"✅ **Dataset '{meta.name}' saved!**  "
        f"{meta.row_count:,} rows · `{meta.db_path}`"
    )
    c1, c2 = st.columns(2)
    with c1:
        if c1.button("📥  Import another dataset", use_container_width=True):
            reset_import()
            st.rerun()
    with c2:
        if c2.button("🗄️  View in My Datasets", use_container_width=True):
            reset_import()
            st.session_state["goto_datasets"] = meta.name
            st.rerun()


# ── Op form builder ────────────────────────────────────────────────────────────

def _render_op_form(op: str, cols: list[str]) -> dict:
    """Return a params dict for the selected op based on user inputs."""
    p: dict[str, Any] = {}

    if op == "filter_rows":
        c1, c2, c3 = st.columns(3)
        p["column"] = c1.selectbox("Column", cols, key="fr_col")
        p["operator"] = c2.selectbox("Operator", ["eq","ne","gt","lt","ge","le","contains","startswith"], key="fr_op")
        p["value"] = c3.text_input("Value", key="fr_val")

    elif op == "rename_cols":
        old = st.selectbox("Column to rename", cols, key="rc_old")
        new = st.text_input("New name", key="rc_new")
        p["mapping"] = {old: new} if old and new else {}

    elif op == "select_cols":
        p["columns"] = st.multiselect("Columns to keep", cols, default=cols, key="sc_cols")

    elif op == "remove_cols":
        p["columns"] = st.multiselect("Columns to remove", cols, key="remcols")

    elif op == "add_col":
        p["name"] = st.text_input("New column name", key="ac_name")
        st.caption(f"Available columns: {', '.join(cols)}")
        p["expr"] = st.text_input("pandas eval expression  (e.g. `col_a + col_b`)", key="ac_expr")

    elif op == "change_type":
        c1, c2 = st.columns(2)
        p["column"] = c1.selectbox("Column", cols, key="ct_col")
        p["dtype"] = c2.selectbox("Target type", ["str","int","float","bool","datetime64[ns]"], key="ct_dtype")

    elif op == "sort":
        p["by"] = st.multiselect("Sort by", cols, key="sort_by")
        p["ascending"] = st.checkbox("Ascending", value=True, key="sort_asc")

    elif op == "group_by":
        p["by"] = st.multiselect("Group by", cols, key="gb_by")
        agg_col = st.selectbox("Aggregate column", cols, key="gb_aggcol")
        agg_func = st.selectbox("Function", ["sum","mean","count","min","max","std"], key="gb_func")
        agg_name = st.text_input("Result column name", value=f"{agg_func}_{agg_col}", key="gb_name")
        p["agg"] = {agg_name: [agg_col, agg_func]}

    elif op == "drop_nulls":
        p["columns"] = st.multiselect("In columns (blank = all)", cols, key="dn_cols") or None

    elif op == "fill_nulls":
        c1, c2 = st.columns(2)
        p["column"] = c1.selectbox("Column", cols, key="fn_col")
        p["value"] = c2.text_input("Fill value", key="fn_val")

    elif op in {"trim_strings", "to_upper", "to_lower"}:
        p["columns"] = st.multiselect("Columns", cols, key=f"{op}_cols")

    elif op == "deduplicate":
        p["columns"] = st.multiselect("Subset columns (blank = all)", cols, key="dd_cols") or None

    elif op == "pivot":
        c1, c2, c3 = st.columns(3)
        p["index"] = c1.selectbox("Index", cols, key="pv_idx")
        p["columns"] = c2.selectbox("Columns field", cols, key="pv_cols")
        p["values"] = c3.selectbox("Values", cols, key="pv_vals")

    elif op == "unpivot":
        p["id_vars"] = st.multiselect("ID columns (keep these)", cols, key="up_id")
        p["value_name"] = st.text_input("Value column name", value="value", key="up_vname")

    elif op == "limit_rows":
        p["n"] = st.number_input("Number of rows", min_value=1, value=1000, step=100, key="lr_n")

    return p


# ── Metadata helpers ───────────────────────────────────────────────────────────

def _source_key(label: str) -> str:
    mapping = {"excel": "excel", "sharepoint": "sharepoint", "onedrive": "onedrive", "azure": "azure"}
    for k in mapping:
        if k in label.lower():
            return k
    return "excel"


def _build_extra_meta(connector, item: dict) -> dict:
    extra: dict = {}
    cls = type(connector).__name__

    if cls == "ExcelConnector":
        extra["file_path"] = str(connector._file_path)
    elif cls == "AzureConnector":
        extra["connection_string"] = connector._conn_str or ""
        extra["service_type"] = connector._service_type or "blob"
        if "container" in item:
            extra.setdefault("item_extra", {})["container"] = item["container"]
            extra.setdefault("item_extra", {})["blob"] = item["blob"]
    elif cls in {"SharePointConnector", "OneDriveConnector"}:
        if "drive_id" in item:
            extra.setdefault("item_extra", {})["drive_id"] = item["drive_id"]
        if "type" in item:
            extra.setdefault("item_extra", {})["type"] = item["type"]

    extra["item_type"] = item.get("type", "")
    return extra


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2 — My Datasets
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🗄️  My Datasets":
    st.title("🗄️ My Datasets")

    datasets = db_mgr.list_all()

    if not datasets:
        st.info("No datasets yet. Go to **Import Data** to add one.")
    else:
        # ── Summary cards ─────────────────────────────────────────────────
        cols = st.columns(min(len(datasets), 4))
        for i, m in enumerate(datasets):
            with cols[i % 4]:
                refreshed = m.last_refreshed.strftime("%d %b %H:%M") if m.last_refreshed else "never"
                st.metric(label=m.name, value=f"{m.row_count:,} rows", delta=f"via {m.source}")

        st.divider()

        # ── Dataset picker ────────────────────────────────────────────────
        preselect = st.session_state.pop("goto_datasets", None)
        ds_names = [m.name for m in datasets]
        default_idx = ds_names.index(preselect) if preselect in ds_names else 0

        selected_name = st.selectbox("Select dataset", ds_names, index=default_idx)
        meta = db_mgr.get(selected_name)

        if meta:
            # ── Metadata row ──────────────────────────────────────────────
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Source", meta.source.title())
            c2.metric("Rows", f"{meta.row_count:,}")
            refreshed = meta.last_refreshed.strftime("%d %b %Y %H:%M") if meta.last_refreshed else "Never"
            c3.metric("Last refreshed", refreshed)
            c4.metric("Pipeline steps", _count_pipeline_steps(meta.pipeline_path))

            # ── Action buttons ────────────────────────────────────────────
            ba, bb, bc = st.columns([1, 1, 4])
            with ba:
                if st.button("🔁  Refresh", type="primary", use_container_width=True):
                    with st.spinner(f"Refreshing '{selected_name}'…"):
                        try:
                            from api.server import _do_refresh
                            _, row_count = _do_refresh(meta)
                            st.success(f"✅ Refreshed — {row_count:,} rows loaded.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Refresh error: {e}")

            with bb:
                if st.button("🗑️  Delete", use_container_width=True):
                    db_mgr.delete(selected_name)
                    st.success(f"Dataset '{selected_name}' deleted.")
                    st.rerun()

            st.divider()

            # ── Data browser ──────────────────────────────────────────────
            tab_data, tab_chart, tab_info = st.tabs(["📋 Data", "📊 Chart", "ℹ️ Info"])

            with tab_data:
                try:
                    limit = st.slider("Rows to display", 10, 5000, 200, step=10, key="data_limit")
                    filter_col = st.selectbox(
                        "Filter column (optional)", ["(none)"] + db_mgr.columns(meta), key="filter_col"
                    )
                    filter_val = None
                    if filter_col != "(none)":
                        filter_val = st.text_input("Filter value", key="filter_val")

                    filters = {filter_col: filter_val} if filter_col != "(none)" and filter_val else None
                    df = db_mgr.read(meta, limit=limit, filters=filters)
                    st.dataframe(df, use_container_width=True, height=420)
                    st.caption(f"Showing {len(df):,} of {meta.row_count:,} total rows.")

                    # Download button
                    buf = BytesIO()
                    df.to_excel(buf, index=False, engine="openpyxl")
                    st.download_button(
                        "⬇️  Download as Excel",
                        data=buf.getvalue(),
                        file_name=f"{selected_name}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                except Exception as e:
                    st.error(f"Could not load data: {e}")

            with tab_chart:
                try:
                    df_chart = db_mgr.read(meta, limit=2000)
                    num_cols = df_chart.select_dtypes("number").columns.tolist()
                    cat_cols = df_chart.select_dtypes(exclude="number").columns.tolist()

                    if not num_cols:
                        st.info("No numeric columns to chart.")
                    else:
                        chart_type = st.selectbox("Chart type", ["Bar", "Line", "Scatter", "Histogram"], key="chart_type")
                        x_col = st.selectbox("X axis", cat_cols + num_cols, key="chart_x")
                        y_col = st.selectbox("Y axis", num_cols, key="chart_y")
                        color_col = st.selectbox("Color (optional)", ["(none)"] + cat_cols, key="chart_color")
                        color = None if color_col == "(none)" else color_col

                        if chart_type == "Bar":
                            fig = px.bar(df_chart, x=x_col, y=y_col, color=color)
                        elif chart_type == "Line":
                            fig = px.line(df_chart, x=x_col, y=y_col, color=color)
                        elif chart_type == "Scatter":
                            fig = px.scatter(df_chart, x=x_col, y=y_col, color=color)
                        else:
                            fig = px.histogram(df_chart, x=x_col, color=color)

                        st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.error(f"Chart error: {e}")

            with tab_info:
                st.json({
                    "name": meta.name,
                    "source": meta.source,
                    "source_item": meta.source_item_name,
                    "db_path": meta.db_path,
                    "table_name": meta.table_name,
                    "row_count": meta.row_count,
                    "pipeline_path": meta.pipeline_path,
                    "created_at": str(meta.created_at),
                    "last_refreshed": str(meta.last_refreshed),
                })

                if meta.pipeline_path and Path(meta.pipeline_path).exists():
                    with st.expander("Pipeline steps (JSON)"):
                        st.json(json.loads(Path(meta.pipeline_path).read_text()))


def _count_pipeline_steps(pipeline_path: str | None) -> int:
    if not pipeline_path:
        return 0
    p = Path(pipeline_path)
    if not p.exists():
        return 0
    try:
        return len(json.loads(p.read_text()).get("steps", []))
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 3 — API & Integration
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🌐  API & Integration":
    st.title("🌐 API & Integration")
    st.caption("Start the REST API and call it from any other Python project.")

    # ── Server status ─────────────────────────────────────────────────────
    st.subheader("API Server")

    c1, c2 = st.columns([3, 1])
    api_url = c1.text_input("API base URL", value=f"http://localhost:{settings.API_PORT}", key="api_url_input")
    with c2:
        st.write("")
        st.write("")
        if st.button("Ping", use_container_width=True):
            try:
                import requests
                r = requests.get(f"{api_url}/", timeout=3)
                if r.status_code == 200:
                    st.success("Online ✅")
                else:
                    st.warning(f"HTTP {r.status_code}")
            except Exception:
                st.error("Unreachable ❌")

    st.info(
        "Start the API server from the terminal:\n"
        "```bash\n"
        "python main.py api\n"
        "# or\n"
        f"uvicorn api.server:build_app --factory --port {settings.API_PORT}\n"
        "```",
        icon="ℹ️",
    )

    st.divider()

    # ── Endpoint reference ────────────────────────────────────────────────
    st.subheader("Endpoints")

    datasets = db_mgr.list_all()
    example_name = datasets[0].name if datasets else "my_dataset"

    endpoints = [
        ("GET",    "/",                              "Health check"),
        ("GET",    "/datasets",                      "List all datasets"),
        ("GET",    f"/datasets/{example_name}",      "Dataset metadata"),
        ("GET",    f"/datasets/{example_name}/data?limit=100", "Paginated data"),
        ("POST",   f"/datasets/{example_name}/refresh", "Refresh data"),
        ("DELETE", f"/datasets/{example_name}",      "Delete dataset"),
    ]

    tbl = st.table(
        pd.DataFrame(endpoints, columns=["Method", "Path", "Description"])
    )

    st.divider()

    # ── Code snippets ─────────────────────────────────────────────────────
    st.subheader("Using `api_client.py` from another project")

    tab1, tab2, tab3 = st.tabs(["List & Query", "Refresh", "Full example"])

    with tab1:
        st.code(
            f"""\
from api_client import DataClient

client = DataClient("{api_url}")

# List all datasets
for ds in client.list_datasets():
    print(ds["name"], "-", ds["row_count"], "rows")

# Fetch data as a pandas DataFrame
df = client.get_data("{example_name}", limit=500)
print(df.head())

# With column filter
df = client.get_data("{example_name}", filters={{"region": "EU"}})
""",
            language="python",
        )

    with tab2:
        st.code(
            f"""\
from api_client import DataClient

client = DataClient("{api_url}")

# Trigger a refresh (re-pulls from source + re-applies pipeline)
result = client.refresh("{example_name}")
print(result["rows_loaded"])    # → 1234
print(result["refreshed_at"])   # → 2026-03-06T12:00:00

# Or with the module-level shorthand
from api_client import refresh
refresh("{example_name}")
""",
            language="python",
        )

    with tab3:
        st.code(
            f"""\
\"\"\"
Example: scheduled refresh + data export
\"\"\"
import schedule
import time
from api_client import DataClient
import pandas as pd

client = DataClient("{api_url}")

def job():
    print("Refreshing…")
    client.refresh("{example_name}")
    df = client.get_all_data("{example_name}")      # fetches all pages
    df.to_csv("latest_export.csv", index=False)
    print(f"Exported {{len(df)}} rows.")

schedule.every(1).hours.do(job)

while True:
    schedule.run_pending()
    time.sleep(60)
""",
            language="python",
        )

    st.divider()

    # ── Direct HTTP (no client library) ──────────────────────────────────
    st.subheader("Direct HTTP (any language)")
    st.code(
        f"""\
# curl
curl {api_url}/datasets
curl "{api_url}/datasets/{example_name}/data?limit=50"
curl -X POST {api_url}/datasets/{example_name}/refresh

# PowerShell
Invoke-RestMethod -Uri "{api_url}/datasets/{example_name}/refresh" -Method POST
""",
        language="bash",
    )
