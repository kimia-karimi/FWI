
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import geopandas as gpd

try:
    import ulmo
except ImportError:
    ulmo = None

CFS_TO_AFD = 1.983471

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
ROOT = Path(r"\\fileserver\CoastalScience\Data\Hydrology\fwi_master")
MASTER_FILE = ROOT / "fwi_timeseries.parquet"
STATE_FILE = ROOT / "watermarks.json"
WATERSHED_SHP = Path(r"\\fileserver\CoastalScience\Data\Hydrology\fwi_master\coastal_watersheds_registry.shp")
UPSTREAM_GAGES_JSON = Path(r"\\fileserver\CoastalScience\Data\Hydrology\fwi_master\upstream_gages.json")

REQUIRED_OUT_COLS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "source",
    "value_afday",
    "flow_role",
    "count_in_basin_sum",
    "data_as_of",
    "note"
]

# ------------------------------------------------------------------
# WATERMARKS + MASTER STORAGE
# ------------------------------------------------------------------
def load_watermarks() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_watermarks(wm: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(wm, indent=2), encoding="utf-8")


def get_watermark(key: str, default: str | None = None) -> pd.Timestamp | None:
    wm = load_watermarks()
    val = wm.get(key, default)
    return pd.to_datetime(val) if val else None


def set_watermark(key: str, dt: pd.Timestamp) -> None:
    wm = load_watermarks()
    wm[key] = pd.to_datetime(dt).strftime("%Y-%m-%d")
    save_watermarks(wm)


def append_master(df_new: pd.DataFrame) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    if MASTER_FILE.exists():
        df_old = pd.read_parquet(MASTER_FILE)
        df = pd.concat([df_old, df_new], ignore_index=True)
        df = df.drop_duplicates(subset=["date", "id", "component", "source", "flow_role"], keep="last")
    else:
        df = df_new.copy()

    df = df.sort_values(["date", "estuary", "id_type", "id", "component"]).reset_index(drop=True)
    df.to_parquet(MASTER_FILE, index=False)

# ------------------------------------------------------------------
# INPUT READERS
# ------------------------------------------------------------------
def load_registry() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(WATERSHED_SHP)
    gdf["WS_ID"] = gdf["WS_ID"].astype(str).str.strip()
    # normalize to 5-digit strings where numeric
    gdf["WS_ID"] = gdf["WS_ID"].apply(lambda x: x.zfill(5) if x.isdigit() else x)
    return gdf


def load_upstream_gages() -> dict:
    return json.loads(UPSTREAM_GAGES_JSON.read_text(encoding="utf-8"))

# ------------------------------------------------------------------
# SOURCE FETCHERS (skeletons; replace/extend as needed)
# ------------------------------------------------------------------
def fetch_usgs_daily_afday(site_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    if ulmo is None:
        raise ImportError("ulmo is required for USGS fetches")
    raw = ulmo.usgs.nwis.get_site_data(site_id, start=start, end=end, service="dv")
    values = raw["00060:00003"]["values"]
    df = pd.DataFrame(values)
    if df.empty:
        return pd.Series(dtype="float64")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    s = df["value"].resample("D").mean() * CFS_TO_AFD
    s.name = site_id
    return s


def fetch_local_gage_timeseries(source: str, gage_id: str, start: pd.Timestamp, end: pd.Timestamp, special: str | None = None) -> pd.Series:
    if source == "usgs":
        # NOTE: special cases like colorado_adjusted and lake_houston should call custom functions.
        # This skeleton uses direct USGS for now.
        return fetch_usgs_daily_afday(gage_id, start, end)
    elif source == "ibwc":
        raise NotImplementedError("Hook your IBWC fetcher here")
    else:
        raise NotImplementedError(f"Local source not implemented: {source}")


def fetch_upstream_component_timeseries(source: str, gage_id: str, start: pd.Timestamp, end: pd.Timestamp, special: str | None = None) -> pd.Series:
    if source == "usgs":
        return fetch_usgs_daily_afday(gage_id, start, end)
    elif source == "lnra":
        raise NotImplementedError("Hook your Lake Texana/LNRA fetcher here")
    elif source == "ibwc":
        raise NotImplementedError("Hook your IBWC fetcher here if you add estuary-level IBWC components")
    else:
        raise NotImplementedError(f"Upstream source not implemented: {source}")

# ------------------------------------------------------------------
# COMPONENT BUILDERS
# ------------------------------------------------------------------
def build_local_gaged(registry: gpd.GeoDataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    reg = registry[registry["HAS_GAGED"] == 1].copy()

    for _, r in reg.iterrows():
        ws_id = str(r["WS_ID"])
        estuary = r.get("ESTUARY")
        source = r.get("G_SOURCE")
        gage_id = r.get("GAGE_ID")
        special = r.get("SPECIAL_T")

        if pd.isna(source) or pd.isna(gage_id):
            continue

        s = fetch_local_gage_timeseries(source, str(gage_id), start, end, special=str(special) if pd.notna(special) else None)
        if s.empty:
            continue

        df = s.reset_index()
        df.columns = ["date", "value_afday"]
        df["id"] = ws_id
        df["id_type"] = "watershed"
        df["estuary"] = estuary
        df["component"] = "gaged_local"
        df["source"] = source
        df["flow_role"] = "adjusted" if pd.notna(special) else "direct"
        df["count_in_basin_sum"] = 1
        df["note"] = special if pd.notna(special) else None
        rows.append(df)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=REQUIRED_OUT_COLS)


def build_upstream_gaged(upstream_cfg: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for estuary, components in upstream_cfg.items():
        for comp in components:
            component_id = comp["component_id"]
            source = comp["source"]
            gage_id = comp["gage_id"]
            label = comp.get("label")
            special = comp.get("special")

            s = fetch_upstream_component_timeseries(source, gage_id, start, end, special=special)
            if s.empty:
                continue

            df = s.reset_index()
            df.columns = ["date", "value_afday"]
            df["id"] = component_id
            df["id_type"] = "system"
            df["estuary"] = estuary
            df["component"] = "gaged_upstream"
            df["source"] = source
            df["flow_role"] = "system_counted"
            df["count_in_basin_sum"] = 1
            df["note"] = label if label else special
            rows.append(df)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=REQUIRED_OUT_COLS)


# Optional utility for deriving annual diversion/return applicability from data instead of static shapefile flags.
def derive_component_applicability_from_annual_data(df: pd.DataFrame, ws_col: str = "WS_ID", year_col: str = "YEAR") -> pd.DataFrame:
    """
    Returns one row per WS_ID/year with an existence flag based on actual available annual data.
    Use separately for diversion / return datasets.
    """
    temp = df.copy()
    temp[ws_col] = temp[ws_col].astype(str).str.strip().apply(lambda x: x.zfill(5) if x.isdigit() else x)
    out = temp.dropna(subset=[ws_col, year_col]).groupby([ws_col, year_col], as_index=False).size()
    out.rename(columns={"size": "record_count"}, inplace=True)
    out["has_component"] = (out["record_count"] > 0).astype(int)
    return out

# ------------------------------------------------------------------
# RUNNER
# ------------------------------------------------------------------
def run_all(start: str | None = None, end: str | None = None):
    registry = load_registry()
    upstream_cfg = load_upstream_gages()

    # Separate watermarks for local and upstream gaged components.
    start_local = pd.to_datetime(start) if start else (get_watermark("gaged_local", default="2015-01-01") + pd.Timedelta(days=1) if get_watermark("gaged_local", default="2015-01-01") is not None else pd.Timestamp("2015-01-01"))
    start_upstream = pd.to_datetime(start) if start else (get_watermark("gaged_upstream", default="2015-01-01") + pd.Timedelta(days=1) if get_watermark("gaged_upstream", default="2015-01-01") is not None else pd.Timestamp("2015-01-01"))
    end_dt = pd.to_datetime(end).normalize() if end else pd.Timestamp.utcnow().normalize()

    df_local = build_local_gaged(registry, start_local, end_dt)
    df_upstream = build_upstream_gaged(upstream_cfg, start_upstream, end_dt)

    all_frames = [df for df in [df_local, df_upstream] if not df.empty]
    if not all_frames:
        print("No new rows built.")
        return

    out = pd.concat(all_frames, ignore_index=True)
    out = out[REQUIRED_OUT_COLS]
    append_master(out)

    if not df_local.empty:
        set_watermark("gaged_local", pd.to_datetime(df_local["date"]).max())
    if not df_upstream.empty:
        set_watermark("gaged_upstream", pd.to_datetime(df_upstream["date"]).max())

    print(f"Wrote {len(out):,} rows to {MASTER_FILE}")


if __name__ == "__main__":
    run_all()
