from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import streamlit as st

from huevault_streamlit.bootstrap import APP_ROOT, ensure_local_paths

ensure_local_paths()

from huevault.models import ColourFormat, ColourInput, RelationshipConfig, SimilarityMode  # type: ignore[import-not-found]

from huevault_streamlit.bridge import (
    APP_POLICY_PRESETS,
    DEFAULT_RUNTIME_CONFIG,
    canonicalize_input,
    current_catalogue_snapshot,
    diagnostics_report,
    get_catalogue_rows,
    get_colour_row,
    get_service,
    ingest_records,
    load_records_from_file,
    load_records_from_upload,
    resolve_policy,
    run_relationship_query,
    run_similarity,
    sample_files,
)
from huevault_streamlit.rendering import chip_html, format_triplet, hue_difference, lab_to_hex, mean, provenance_summary, safe_float


st.set_page_config(page_title="HueVault Streamlit Test App", page_icon="🎨", layout="wide")


def state_dir() -> Path:
    directory = APP_ROOT / ".streamlit_state"
    directory.mkdir(exist_ok=True)
    return directory


def build_db_path() -> str:
    return str((state_dir() / f"huevault_{uuid.uuid4().hex[:10]}.db").resolve())


def ensure_session() -> None:
    session = st.session_state
    if "session_id" not in session:
        session.session_id = uuid.uuid4().hex[:10]
    if "db_path" not in session:
        session.db_path = build_db_path()
    session.setdefault("load_events", [])
    session.setdefault("loaded_batches", [])
    session.setdefault("selected_catalogue_id", None)
    session.setdefault("similarity_query_catalogue_id", None)
    session.setdefault("relationship_query_catalogue_id", None)
    session.setdefault("last_similarity_rows", [])
    session.setdefault("last_relationship_rows", [])


def service():
    return get_service(st.session_state.db_path)


def rotate_database() -> None:
    previous_path = Path(st.session_state.db_path)
    st.session_state.session_id = uuid.uuid4().hex[:10]
    st.session_state.db_path = build_db_path()
    if previous_path.exists():
        try:
            previous_path.unlink()
        except PermissionError:
            pass


def clear_session_data() -> None:
    rotate_database()
    st.session_state.load_events = []
    st.session_state.loaded_batches = []
    st.session_state.selected_catalogue_id = None
    st.session_state.similarity_query_catalogue_id = None
    st.session_state.relationship_query_catalogue_id = None
    st.session_state.last_similarity_rows = []
    st.session_state.last_relationship_rows = []


def reload_loaded_batches() -> None:
    batches = list(st.session_state.loaded_batches)
    rotate_database()
    st.session_state.load_events = []
    st.session_state.selected_catalogue_id = None
    st.session_state.similarity_query_catalogue_id = None
    st.session_state.relationship_query_catalogue_id = None
    st.session_state.last_similarity_rows = []
    st.session_state.last_relationship_rows = []
    for batch in batches:
        result = ingest_records(service(), batch["records"], batch["source"])
        st.session_state.load_events.append(result)
    rows = get_catalogue_rows(service())
    if rows:
        st.session_state.selected_catalogue_id = rows[0]["catalogue_id"]


def ensure_catalogue_ready() -> None:
    if st.session_state.loaded_batches and not Path(st.session_state.db_path).exists():
        reload_loaded_batches()


def append_batch(records: list[dict], source_name: str, replace_existing: bool) -> None:
    if replace_existing:
        clear_session_data()
    result = ingest_records(service(), records, source_name)
    st.session_state.loaded_batches.append({"source": source_name, "records": records})
    st.session_state.load_events.append(result)
    rows = get_catalogue_rows(service())
    if rows and st.session_state.selected_catalogue_id is None:
        st.session_state.selected_catalogue_id = rows[0]["catalogue_id"]


def catalogue_rows() -> list[dict]:
    ensure_catalogue_ready()
    return get_catalogue_rows(service())


def build_colour_option_label(row: dict) -> str:
    parts = [str(row["catalogue_id"]), str(row["colour_id"])]
    if row.get("name"):
        parts.append(str(row["name"]))
    if bool(row.get("is_collision_member")):
        if bool(row.get("is_collision_origin")):
            parts.append("collision origin")
        else:
            parts.append(f"collision member #{int(row.get('collision_rank') or 1)}")
    return " | ".join(parts)


def colour_option_maps(rows: list[dict]) -> tuple[list[str], dict[str, str], dict[str, str]]:
    option_to_catalogue: dict[str, str] = {}
    catalogue_to_option: dict[str, str] = {}
    options: list[str] = []
    for row in rows:
        option = build_colour_option_label(row)
        options.append(option)
        option_to_catalogue[option] = row["catalogue_id"]
        catalogue_to_option[row["catalogue_id"]] = option
    return options, option_to_catalogue, catalogue_to_option


def detail_target() -> dict | None:
    identifier = st.session_state.selected_catalogue_id
    if not identifier:
        rows = catalogue_rows()
        if rows:
            st.session_state.selected_catalogue_id = rows[0]["catalogue_id"]
            identifier = st.session_state.selected_catalogue_id
    if not identifier:
        return None
    return get_colour_row(service(), identifier)


def manual_colour_form(prefix: str, allow_ingest: bool) -> tuple[ColourInput | None, dict | None]:
    format_name = st.selectbox("Format", ["hex", "rgb", "lab", "cmyk"], key=f"{prefix}_format")
    input_id = st.text_input("Input ID", value=f"{prefix}-{uuid.uuid4().hex[:6]}", key=f"{prefix}_input_id")
    name = st.text_input("Name", key=f"{prefix}_name")
    source_id = st.text_input("Source ID / Group", key=f"{prefix}_source_id")
    source_profile = st.text_input("Source Profile", key=f"{prefix}_source_profile")

    if format_name == "hex":
        raw_values = st.text_input("HEX", value="#3366CC", key=f"{prefix}_hex")
    elif format_name == "rgb":
        channels = st.columns(3)
        raw_values = [
            channels[0].number_input("R", min_value=0.0, max_value=255.0, value=51.0, key=f"{prefix}_rgb_r"),
            channels[1].number_input("G", min_value=0.0, max_value=255.0, value=102.0, key=f"{prefix}_rgb_g"),
            channels[2].number_input("B", min_value=0.0, max_value=255.0, value=204.0, key=f"{prefix}_rgb_b"),
        ]
    elif format_name == "lab":
        channels = st.columns(3)
        raw_values = [
            channels[0].number_input("L*", min_value=0.0, max_value=100.0, value=50.0, key=f"{prefix}_lab_l"),
            channels[1].number_input("a*", min_value=-128.0, max_value=128.0, value=20.0, key=f"{prefix}_lab_a"),
            channels[2].number_input("b*", min_value=-128.0, max_value=128.0, value=-35.0, key=f"{prefix}_lab_b"),
        ]
    else:
        channels = st.columns(4)
        raw_values = [
            channels[0].number_input("C", min_value=0.0, max_value=100.0, value=75.0, key=f"{prefix}_cmyk_c"),
            channels[1].number_input("M", min_value=0.0, max_value=100.0, value=50.0, key=f"{prefix}_cmyk_m"),
            channels[2].number_input("Y", min_value=0.0, max_value=100.0, value=0.0, key=f"{prefix}_cmyk_y"),
            channels[3].number_input("K", min_value=0.0, max_value=100.0, value=0.0, key=f"{prefix}_cmyk_k"),
        ]

    colour_input = ColourInput(
        input_id=input_id,
        source_id=source_id or None,
        name=name or None,
        source_format=ColourFormat(format_name),
        source_values=raw_values,
        source_profile=source_profile or None,
        provenance={"ui_manual_entry": True, "form_prefix": prefix},
    )

    preview = None
    try:
        preview = canonicalize_input(colour_input)
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))

    if preview:
        st.markdown(chip_html(preview["hex"], preview["canonical"].name or "Preview"), unsafe_allow_html=True)
        preview_cols = st.columns(3)
        preview_cols[0].metric("Colour ID", preview["canonical"].colour_id)
        preview_cols[1].metric("Lab", format_triplet(preview["canonical"].lab))
        preview_cols[2].metric("LCh", format_triplet(preview["canonical"].lch))
        st.code(provenance_summary(preview["canonical"].provenance), language="json")

    if allow_ingest and st.button("Ingest Manual Colour", key=f"{prefix}_ingest", use_container_width=True):
        append_batch(
            [
                {
                    "input_id": colour_input.input_id,
                    "name": colour_input.name,
                    "source_id": colour_input.source_id,
                    "source_profile": colour_input.source_profile,
                    "source_format": colour_input.source_format.value,
                    "source_values": colour_input.source_values,
                    "provenance": colour_input.provenance,
                }
            ],
            f"manual:{colour_input.input_id}",
            replace_existing=False,
        )
        st.success("Manual colour ingested into the current session catalogue.")

    return colour_input, preview


def render_results(rows: list[dict], kind: str) -> None:
    if not rows:
        st.info(f"No {kind} results.")
        return

    for row in rows:
        left, middle, right = st.columns([1.2, 2.6, 2.2])
        with left:
            st.markdown(chip_html(row["hex"], row.get("name") or row["candidate_catalogue_id"], 48), unsafe_allow_html=True)
        with middle:
            st.markdown(f"**{row['candidate_catalogue_id']}**  `{row['candidate_colour_id']}`")
            st.caption(row.get("name") or "Unnamed colour")
            if kind == "similarity":
                st.write(f"Delta E `{row['delta_e']}`  |  band `{row['relationship_band']}`  |  rank `{row['rank']}`")
            else:
                st.write(f"relationship `{row['relationship_type']}`  |  score `{row['score']}`  |  rank `{row['rank']}`")
            if row.get("is_collision_member"):
                lineage = "origin" if row.get("is_collision_origin") else f"member #{row.get('collision_rank', 1)}"
                st.caption(
                    f"collision lineage: {lineage}  |  "
                    f"group `{row.get('collision_group_id')}`  |  "
                    f"origin `{row.get('collision_origin_catalogue_id')}`"
                )
        with right:
            st.write(f"Hue Delta `{row['hue_difference']}`")
            st.write(f"Status `{row['status']}`")
            st.caption(provenance_summary(row.get("provenance")))
        st.divider()


def filtered_catalogue(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    search = st.text_input("Search by colour ID, catalogue ID, or name", key="browse_search").strip().lower()
    hue_range = st.slider("Hue Range", 0.0, 360.0, (0.0, 360.0), key="browse_hue")
    lightness_range = st.slider("Lightness Range", 0.0, 100.0, (0.0, 100.0), key="browse_lightness")
    chroma_cap = max(float(row["lch_c"]) for row in rows)
    chroma_range = st.slider("Chroma Range", 0.0, max(1.0, chroma_cap), (0.0, max(1.0, chroma_cap)), key="browse_chroma")
    sort_key = st.selectbox("Sort By", ["order_key", "hue", "lightness", "chroma"], key="browse_sort")

    filtered = []
    for row in rows:
        haystack = " ".join(str(row.get(key, "")) for key in ("catalogue_id", "colour_id", "name")).lower()
        if search and search not in haystack:
            continue
        if not (hue_range[0] <= float(row["lch_h"]) <= hue_range[1]):
            continue
        if not (lightness_range[0] <= float(row["lch_l"]) <= lightness_range[1]):
            continue
        if not (chroma_range[0] <= float(row["lch_c"]) <= chroma_range[1]):
            continue
        filtered.append(row)

    sorters = {
        "order_key": lambda item: item["order_key"],
        "hue": lambda item: (float(item["lch_h"]), item["order_key"]),
        "lightness": lambda item: (float(item["lch_l"]), item["order_key"]),
        "chroma": lambda item: (float(item["lch_c"]), item["order_key"]),
    }
    return sorted(filtered, key=sorters[sort_key])


ensure_session()

st.markdown(
    """
    <style>
    :root {
      --hv-bg: #0d0f12;
      --hv-bg-elevated: #14181d;
      --hv-bg-panel: #181d22;
      --hv-border: rgba(255, 255, 255, 0.10);
      --hv-text: #f5f1eb;
      --hv-text-muted: #b9b1a8;
      --hv-accent: #c8102e;
      --hv-accent-soft: rgba(200, 16, 46, 0.14);
      --hv-shadow: 0 18px 36px rgba(0, 0, 0, 0.28);
    }
    .stApp {
      background:
        radial-gradient(circle at 18% 8%, rgba(200, 16, 46, 0.08), transparent 20%),
        linear-gradient(180deg, #0d0f12 0%, #13171b 100%);
      color: var(--hv-text);
      font-family: Aptos, "Segoe UI Variable", "Segoe UI", sans-serif;
    }
    .stApp,
    .stApp p,
    .stApp label,
    .stApp span,
    .stApp li,
    .stApp div,
    .stMarkdown,
    .stMarkdown p,
    .stCaption,
    .stCode,
    h1, h2, h3, h4, h5, h6 {
      color: var(--hv-text);
    }
    h1, h2, h3 {
      letter-spacing: -0.03em;
      font-weight: 620;
    }
    [data-testid="stAppViewBlockContainer"] {
      max-width: 1420px;
      padding-top: 2rem;
      padding-bottom: 3rem;
    }
    [data-testid="stSidebar"] {
      background: linear-gradient(180deg, #121519 0%, #0f1216 100%);
      border-right: 1px solid var(--hv-border);
    }
    [data-testid="stSidebar"] * {
      color: var(--hv-text);
    }
    [data-testid="stHeader"] {
      background: rgba(13, 15, 18, 0.94);
      border-bottom: 1px solid var(--hv-border);
    }
    [data-testid="stMetric"] {
      background: linear-gradient(180deg, #1a2026 0%, #151a1f 100%);
      border: 1px solid var(--hv-border);
      border-radius: 16px;
      padding: 0.75rem 0.9rem;
      box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
    }
    [data-testid="stMetricLabel"],
    [data-testid="stMetricValue"],
    [data-testid="stMetricDelta"] {
      color: var(--hv-text) !important;
    }
    [data-baseweb="select"] > div,
    [data-baseweb="base-input"] > div,
    .stTextInput input,
    .stTextArea textarea,
    .stNumberInput input {
      background: #191d22 !important;
      color: var(--hv-text) !important;
      border: 1px solid rgba(255, 255, 255, 0.12) !important;
      border-radius: 14px !important;
    }
    .stTextInput input:focus,
    .stTextArea textarea:focus,
    .stNumberInput input:focus {
      border-color: rgba(200, 16, 46, 0.44) !important;
      box-shadow: 0 0 0 1px rgba(200, 16, 46, 0.14) !important;
    }
    [data-baseweb="select"] svg,
    .stSelectbox svg {
      fill: var(--hv-text-muted) !important;
    }
    [data-baseweb="tab-list"] {
      gap: 0.35rem;
      border-bottom: 1px solid rgba(148, 163, 184, 0.12);
      margin-bottom: 1.1rem;
    }
    button[kind="secondary"],
    button[kind="primary"],
    .stDownloadButton button,
    .stButton button {
      background: linear-gradient(180deg, #1c2228 0%, #151a20 100%) !important;
      color: var(--hv-text) !important;
      border: 1px solid rgba(255, 255, 255, 0.12) !important;
      border-radius: 14px;
      box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
      font-weight: 560;
      min-height: 2.85rem;
    }
    .stButton button:hover,
    .stDownloadButton button:hover {
      border-color: rgba(200, 16, 46, 0.38) !important;
      color: var(--hv-text) !important;
      background: linear-gradient(180deg, #242a31 0%, #181d23 100%) !important;
      box-shadow: 0 10px 22px rgba(0, 0, 0, 0.22);
    }
    .stButton button:focus,
    .stDownloadButton button:focus {
      outline: none !important;
      border-color: rgba(200, 16, 46, 0.42) !important;
      box-shadow: 0 0 0 3px rgba(200, 16, 46, 0.12) !important;
    }
    [data-baseweb="tab"] {
      color: var(--hv-text-muted);
      background: rgba(255, 255, 255, 0.04);
      border-radius: 12px 12px 0 0;
      padding-left: 0.9rem;
      padding-right: 0.9rem;
      border: 1px solid rgba(255, 255, 255, 0.06);
    }
    [aria-selected="true"][data-baseweb="tab"] {
      color: var(--hv-text) !important;
      background: rgba(255, 255, 255, 0.08);
      border-color: rgba(255, 255, 255, 0.12);
      box-shadow: inset 0 -2px 0 var(--hv-accent);
    }
    .stCheckbox label,
    .stRadio label,
    .stFileUploader,
    .stCodeBlock,
    pre,
    code {
      color: var(--hv-text);
    }
    .stAlert {
      background: #181d22;
      border: 1px solid rgba(255, 255, 255, 0.10);
      color: var(--hv-text);
      border-radius: 14px;
    }
    [data-testid="stFileUploaderDropzone"] {
      background: #161b20;
      border: 1px dashed rgba(255, 255, 255, 0.12);
      border-radius: 18px;
    }
    [data-testid="stDataFrame"],
    [data-testid="stTable"] {
      background: #181d22;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.10);
    }
    [data-testid="stToolbar"] {
      right: 1rem;
    }
    .hv-masthead {
      padding: 1.35rem 1.4rem 1.15rem 1.4rem;
      margin-bottom: 1.35rem;
      border: 1px solid rgba(255, 255, 255, 0.10);
      border-radius: 22px;
      background: linear-gradient(180deg, #181d22 0%, #14181d 100%);
      box-shadow: 0 14px 30px rgba(0, 0, 0, 0.2);
      position: relative;
      overflow: hidden;
    }
    .hv-masthead::after {
      content: "";
      position: absolute;
      inset: auto -10% -35% 42%;
      height: 180px;
      background: radial-gradient(circle, rgba(200, 16, 46, 0.10), transparent 62%);
      pointer-events: none;
    }
    .hv-kicker {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      margin-bottom: 0.65rem;
      padding: 0.28rem 0.6rem;
      border-radius: 999px;
      background: rgba(200, 16, 46, 0.10);
      border: 1px solid rgba(200, 16, 46, 0.16);
      color: #ffd9df;
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-weight: 700;
    }
    .hv-title {
      margin: 0;
      font-size: clamp(2.15rem, 4.2vw, 3.35rem);
      line-height: 0.95;
      max-width: 11ch;
    }
    .hv-subtitle {
      max-width: 60rem;
      margin: 0.8rem 0 1rem 0;
      color: var(--hv-text-muted);
      font-size: 1rem;
      line-height: 1.55;
    }
    .hv-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .hv-tag {
      padding: 0.34rem 0.64rem;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.10);
      background: rgba(255, 255, 255, 0.04);
      color: #d7d0c8;
      font-size: 0.82rem;
    }
    .stButton {
      width: 100%;
    }
    .stButton > button {
      width: 100%;
    }
    .stApp a {
      color: #f26b7d;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <section class="hv-masthead">
      <div class="hv-kicker">HueVault Studio</div>
      <h1 class="hv-title">Colour Registry Workbench</h1>
      <p class="hv-subtitle">
        A local, highly visual test surface for ingestion, canonicalization, similarity policy tuning,
        and relationship queries. Built for practitioners working between design judgement and system behaviour.
      </p>
      <div class="hv-tags">
        <span class="hv-tag">Visual Matching</span>
        <span class="hv-tag">Registry Diagnostics</span>
        <span class="hv-tag">Policy Comparison</span>
        <span class="hv-tag">Structural Relationships</span>
      </div>
    </section>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    rows = catalogue_rows()
    st.subheader("Session")
    st.metric("Loaded Colours", len(rows))
    st.metric("Loaded Batches", len(st.session_state.loaded_batches))
    st.metric("Load Events", len(st.session_state.load_events))
    st.caption(
        f"Runtime: collision `{DEFAULT_RUNTIME_CONFIG.collision_mode.value}`, "
        f"dedupe `{DEFAULT_RUNTIME_CONFIG.deduplication_mode.value}`"
    )
    if st.button("Clear Session State", use_container_width=True):
        clear_session_data()
        st.rerun()

tabs = st.tabs(
    [
        "Load Data",
        "Browse Catalogue",
        "Colour Detail",
        "Similarity Search",
        "Relationship Search",
        "Diagnostics",
    ]
)

with tabs[0]:
    st.subheader("Dataset Loading")
    left, right = st.columns([1.4, 1.1])
    with left:
        replace_existing = st.checkbox("Replace current catalogue on load", value=False)
        sample_options = sample_files()
        if sample_options:
            selected_sample = st.selectbox("Sample Files", sample_options, format_func=lambda path: path.name)
            if st.button("Load Selected Sample", use_container_width=True):
                records, source_name = load_records_from_file(str(selected_sample))
                append_batch(records, source_name, replace_existing)
                st.success(f"Loaded `{source_name}`.")

        path_value = st.text_input("Load JSON/CSV from local path")
        if st.button("Load From Path", use_container_width=True):
            records, source_name = load_records_from_file(path_value)
            append_batch(records, source_name, replace_existing)
            st.success(f"Loaded `{source_name}`.")

        uploaded = st.file_uploader("Upload JSON or CSV", type=["json", "csv"])
        if uploaded is not None and st.button("Load Upload", use_container_width=True):
            records, source_name = load_records_from_upload(uploaded.name, uploaded.getvalue())
            append_batch(records, source_name, replace_existing)
            st.success(f"Loaded `{source_name}`.")

        snapshot_json, snapshot_csv = current_catalogue_snapshot(service())
        st.download_button("Download Snapshot JSON", data=snapshot_json, file_name="catalogue_snapshot.json", mime="application/json", use_container_width=True)
        st.download_button("Download Snapshot CSV", data=snapshot_csv, file_name="catalogue_snapshot.csv", mime="text/csv", use_container_width=True)

    with right:
        st.subheader("Manual Colour Entry")
        manual_colour_form("load_manual", allow_ingest=True)

    if st.session_state.load_events:
        st.subheader("Load History")
        for event in reversed(st.session_state.load_events):
            st.write(
                f"`{event['source']}` attempted `{event['attempted']}`  |  ingested `{event['ingested']}`  |  "
                f"errors `{len(event['errors'])}`  |  warnings `{len(event['warnings'])}`"
            )

with tabs[1]:
    st.subheader("Browse Catalogue")
    rows = catalogue_rows()
    if not rows:
        st.info("Load a dataset or ingest a manual colour first.")
    else:
        filtered = filtered_catalogue(rows)
        page_size = st.selectbox("Page Size", [10, 20, 50, 100], index=1)
        total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
        page = int(st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1))
        start = (page - 1) * page_size
        end = start + page_size
        st.caption(f"Showing {min(len(filtered), start + 1)}-{min(len(filtered), end)} of {len(filtered)} filtered colours.")

        for row in filtered[start:end]:
            preview_hex = lab_to_hex((float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"])))
            left, middle, right = st.columns([1.2, 2.7, 1.3])
            with left:
                st.markdown(chip_html(preview_hex, row.get("name") or row["catalogue_id"], 46), unsafe_allow_html=True)
            with middle:
                st.markdown(f"**{row['catalogue_id']}**  `{row['colour_id']}`")
                st.write(f"Lab `{format_triplet((float(row['lab_l']), float(row['lab_a']), float(row['lab_b'])))} `")
                st.write(f"LCh `{format_triplet((float(row['lch_l']), float(row['lch_c']), float(row['lch_h'])))} `")
                if bool(row.get("is_collision_member")):
                    lineage = "origin" if bool(row.get("is_collision_origin")) else f"member #{int(row.get('collision_rank') or 1)}"
                    st.caption(
                        f"collision lineage: {lineage}  |  "
                        f"group `{row.get('collision_group_id')}`  |  "
                        f"origin `{row.get('collision_origin_catalogue_id')}`"
                    )
                st.caption(provenance_summary(row["provenance"]))
            with right:
                if st.button("Inspect", key=f"inspect_{row['catalogue_id']}", use_container_width=True):
                    st.session_state.selected_catalogue_id = row["catalogue_id"]
                    st.session_state.similarity_query_catalogue_id = row["catalogue_id"]
                    st.session_state.relationship_query_catalogue_id = row["catalogue_id"]
        st.divider()

with tabs[2]:
    st.subheader("Colour Detail")
    rows = catalogue_rows()
    if not rows:
        st.info("No catalogue loaded.")
    else:
        options, option_to_catalogue, catalogue_to_option = colour_option_maps(rows)
        default_option = catalogue_to_option.get(st.session_state.selected_catalogue_id, options[0])
        current_option = st.selectbox(
            "Selected Colour",
            options,
            index=options.index(default_option),
            help="Search by unique catalogue_id, deterministic colour_id, or name.",
        )
        st.session_state.selected_catalogue_id = option_to_catalogue[current_option]
        row = detail_target()
        if row:
            preview_hex = lab_to_hex((float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"])))
            top_left, top_mid, top_right = st.columns([1.1, 1.5, 1.4])
            with top_left:
                st.markdown(chip_html(preview_hex, row.get("name") or row["catalogue_id"], 84), unsafe_allow_html=True)
            with top_mid:
                st.write(f"Colour ID: `{row['colour_id']}`")
                st.write(f"Catalogue ID: `{row['catalogue_id']}`")
                st.write(f"Status: `{row['status']}`")
                st.write(f"Order Key: `{row['order_key']}`")
                st.write(f"Name: `{row.get('name') or 'None'}`")
            with top_right:
                st.write(f"Raw Source Format: `{row['source_format']}`")
                st.write(f"Raw Source Values: `{row['source_values']}`")
                st.write(f"Source ID: `{row.get('source_id') or 'None'}`")
                st.write(f"Source Profile: `{row.get('source_profile') or 'None'}`")

            metrics = st.columns(3)
            metrics[0].metric("Canonical Lab", format_triplet((float(row["lab_l"]), float(row["lab_a"]), float(row["lab_b"]))))
            metrics[1].metric("Derived LCh", format_triplet((float(row["lch_l"]), float(row["lch_c"]), float(row["lch_h"]))))
            if bool(row.get("is_collision_member")):
                collision_state = "origin" if bool(row.get("is_collision_origin")) else f"member #{int(row.get('collision_rank') or 1)}"
            else:
                collision_state = "none"
            metrics[2].metric("Collision Lineage", collision_state)
            collision_cols = st.columns(3)
            collision_cols[0].write(f"Collision Group: `{row.get('collision_group_id') or 'None'}`")
            collision_cols[1].write(f"Origin Catalogue ID: `{row.get('collision_origin_catalogue_id') or 'None'}`")
            collision_cols[2].write(f"Status: `{row.get('status')}`")
            st.code(provenance_summary(row["provenance"]), language="json")

            actions = st.columns(3)
            if actions[0].button("Use In Similarity Search", use_container_width=True):
                st.session_state.similarity_query_catalogue_id = row["catalogue_id"]
            if actions[1].button("Use In Relationship Search", use_container_width=True):
                st.session_state.relationship_query_catalogue_id = row["catalogue_id"]
            if actions[2].button("Set As Selected Detail", use_container_width=True):
                st.session_state.selected_catalogue_id = row["catalogue_id"]

with tabs[3]:
    st.subheader("Similarity Search")
    rows = catalogue_rows()
    if not rows:
        st.info("Load a catalogue before running similarity queries.")
    else:
        query_mode = st.radio("Query Source", ["catalogue", "manual"], horizontal=True)
        query_identifier = None
        manual_query = None

        if query_mode == "catalogue":
            options, option_to_catalogue, catalogue_to_option = colour_option_maps(rows)
            default_option = catalogue_to_option.get(st.session_state.similarity_query_catalogue_id, options[0])
            selected_option = st.selectbox(
                "Query Colour",
                options,
                index=options.index(default_option),
                help="Search by entry ID, deterministic colour ID, collision lineage, or name.",
            )
            query_identifier = option_to_catalogue[selected_option]
            st.session_state.similarity_query_catalogue_id = query_identifier
            selected = get_colour_row(service(), query_identifier)
            if selected:
                query_preview_hex = lab_to_hex((float(selected["lab_l"]), float(selected["lab_a"]), float(selected["lab_b"])))
                st.markdown(chip_html(query_preview_hex, selected.get("name") or query_identifier, 60), unsafe_allow_html=True)
        else:
            manual_query, _ = manual_colour_form("similarity_manual", allow_ingest=False)

        preset_ids = list(APP_POLICY_PRESETS.keys())
        selected_presets = st.multiselect("Policy Presets", preset_ids, default=["visual_match"])
        compare_mode = st.checkbox("Compare all selected presets", value=True)
        delta_override = st.number_input("Delta E Threshold Override", min_value=0.0, value=3.0)
        mode_override = st.selectbox("Mode Override", [mode.value for mode in SimilarityMode], index=0)
        top_k_override = st.number_input("Top-k Override", min_value=1, value=8)
        hue_tolerance = st.number_input("Hue Tolerance Filter (optional)", min_value=0.0, value=360.0)

        if st.button("Run Similarity Query", use_container_width=True):
            if not selected_presets:
                st.warning("Select at least one policy preset.")
                st.stop()
            preset_run_ids = selected_presets if compare_mode else selected_presets[:1]
            combined_rows = []
            for preset_id in preset_run_ids:
                policy = resolve_policy(service(), preset_id, delta_override, mode_override, int(top_k_override))
                st.write(f"Resolved policy `{preset_id}`")
                st.json(
                    {
                        "policy_id": policy.policy_id,
                        "delta_e_threshold": policy.delta_e_threshold,
                        "mode": policy.mode.value,
                        "top_k": policy.top_k,
                        "description": policy.description,
                    }
                )
                result_rows = run_similarity(
                    service(),
                    query_identifier=query_identifier,
                    manual_query=manual_query,
                    policy=policy,
                    hue_tolerance=None if hue_tolerance >= 360.0 else hue_tolerance,
                )
                for row in result_rows:
                    row["policy_id"] = preset_id
                render_results(result_rows, "similarity")
                combined_rows.extend(result_rows)
            st.session_state.last_similarity_rows = combined_rows

        if st.session_state.last_similarity_rows:
            st.caption(
                f"Last similarity run: {len(st.session_state.last_similarity_rows)} results, "
                f"mean Delta E {mean([safe_float(row['delta_e']) for row in st.session_state.last_similarity_rows]):.3f}"
            )
            st.download_button(
                "Download Last Similarity Results",
                data=json.dumps(st.session_state.last_similarity_rows, indent=2, sort_keys=True),
                file_name="similarity_results.json",
                mime="application/json",
                use_container_width=True,
            )

with tabs[4]:
    st.subheader("Structural Relationship Search")
    rows = catalogue_rows()
    if not rows:
        st.info("Load a catalogue before running relationship queries.")
    else:
        query_mode = st.radio("Relationship Query Source", ["catalogue", "manual"], horizontal=True)
        query_identifier = None
        manual_query = None
        if query_mode == "catalogue":
            options, option_to_catalogue, catalogue_to_option = colour_option_maps(rows)
            default_option = catalogue_to_option.get(st.session_state.relationship_query_catalogue_id, options[0])
            selected_option = st.selectbox(
                "Query Colour",
                options,
                index=options.index(default_option),
                key="relationship_catalogue_query",
                help="Search by entry ID, deterministic colour ID, collision lineage, or name.",
            )
            query_identifier = option_to_catalogue[selected_option]
            st.session_state.relationship_query_catalogue_id = query_identifier
        else:
            manual_query, _ = manual_colour_form("relationship_manual", allow_ingest=False)

        relationship_type = st.selectbox("Relationship Type", ["complementary", "analogous"])
        complementary_tolerance = st.number_input("Complementary Hue Tolerance", min_value=0.1, value=10.0)
        analogous_tolerance = st.number_input("Analogous Hue Window", min_value=0.1, value=30.0)
        require_lightness = st.checkbox("Require Lightness Similarity")
        require_chroma = st.checkbox("Require Chroma Similarity")
        lightness_tolerance = st.number_input("Lightness Tolerance", min_value=0.1, value=10.0)
        chroma_tolerance = st.number_input("Chroma Tolerance", min_value=0.1, value=15.0)

        relationship_config = RelationshipConfig(
            complementary_tolerance=float(complementary_tolerance),
            analogous_tolerance=float(analogous_tolerance),
            require_lightness_similarity=require_lightness,
            require_chroma_similarity=require_chroma,
            lightness_tolerance=float(lightness_tolerance),
            chroma_tolerance=float(chroma_tolerance),
        )
        st.json(
            {
                "relationship_type": relationship_type,
                "config": {
                    "complementary_tolerance": relationship_config.complementary_tolerance,
                    "analogous_tolerance": relationship_config.analogous_tolerance,
                    "require_lightness_similarity": relationship_config.require_lightness_similarity,
                    "require_chroma_similarity": relationship_config.require_chroma_similarity,
                    "lightness_tolerance": relationship_config.lightness_tolerance,
                    "chroma_tolerance": relationship_config.chroma_tolerance,
                },
            }
        )

        if st.button("Run Relationship Query", use_container_width=True):
            result_rows = run_relationship_query(
                service(),
                query_identifier=query_identifier,
                manual_query=manual_query,
                relationship_type=relationship_type,
                relationship_config=relationship_config,
            )
            render_results(result_rows, "relationship")
            st.session_state.last_relationship_rows = result_rows

        if st.session_state.last_relationship_rows:
            st.download_button(
                "Download Last Relationship Results",
                data=json.dumps(st.session_state.last_relationship_rows, indent=2, sort_keys=True),
                file_name="relationship_results.json",
                mime="application/json",
                use_container_width=True,
            )

with tabs[5]:
    st.subheader("Diagnostics")
    rows = catalogue_rows()
    report = diagnostics_report(service())
    top = st.columns(4)
    top[0].metric("Catalogue Size", report["catalogue_size"])
    top[1].metric("Duplicate Lab Groups", len(report["duplicate_lab_values"]))
    top[2].metric("ID Collision Groups", len(report["id_collisions"]))
    top[3].metric("Transformation Log", report["transformation_log_count"])

    st.markdown("**Validation Checks**")
    st.write(f"Lab/LCh consistency failures: `{len(report['lab_lch_consistency_failures'])}`")
    st.write(f"ID quantization failures: `{len(report['id_quantization_failures'])}`")
    st.write(f"Reproducibility failures: `{len(report['reproducibility_failures'])}`")
    st.write(f"Missing provenance rows: `{len(report['missing_provenance'])}`")
    st.write(f"Out-of-bounds rows: `{len(report['bounds_issues'])}`")

    if st.session_state.load_events:
        st.markdown("**Load Warnings and Errors**")
        for event in reversed(st.session_state.load_events):
            for item in event["errors"] + event["warnings"]:
                st.write(f"[{item['level']}] `{item.get('source')}` row `{item.get('row_number')}`: {item['message']}")
                if item.get("row_preview"):
                    st.caption(item["row_preview"])

    if report["duplicate_lab_values"]:
        st.markdown("**Duplicate Lab Values**")
        st.json(report["duplicate_lab_values"])

    if report["id_collisions"]:
        st.markdown("**ID Collisions**")
        st.json(report["id_collisions"])

    if report["bounds_issues"]:
        st.markdown("**Bounds Issues**")
        for item in report["bounds_issues"]:
            st.write(item)

    if rows:
        st.markdown("**Current Session Determinism Snapshot**")
        example = rows[0]
        st.write(
            f"Sample `{example['catalogue_id']}` hue distance to itself: "
            f"`{hue_difference(float(example['lch_h']), float(example['lch_h'])):.3f}`"
        )
