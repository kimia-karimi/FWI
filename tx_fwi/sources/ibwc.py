"""IBWC Aquarius daily rounded discharge source adapter."""
from __future__ import annotations
import io
import time
import requests
import pandas as pd
from tx_fwi.transforms.units import cfs_to_afday
from tx_fwi.transforms.temporal import normalize_daily_series
CFS_TO_AFD = 1.983471
MGD_TO_AFD = 3.06888328
INCH_TO_FEET = 1.0 / 12.0

EXPORT_ENDPOINT = "https://waterdata.ibwc.gov/AQWebportal/Export/DataSet"
HTTP_TIMEOUT = 60


# ------------------------------------------------------------------
def build_ibwc_params(station_id: str, start, end) -> dict:

    return {
        "DataSet": f"Discharge.Daily Rounded@{station_id}",
        "DateRange": "Custom",
        "StartTime": pd.to_datetime(start).strftime("%Y-%m-%dT00:00:00"),
        "EndTime": pd.to_datetime(end).strftime("%Y-%m-%dT23:59:59"),
        "UnitID": "128",
        "Conversion": "Instantaneous",
        "IntervalPoints": "PointsAsRecorded",
        "ApprovalLevels": "False",
        "Qualifiers": "False",
        "Step": "1",
        "ExportFormat": "csv",
        "Compressed": "false",
        "RoundData": "True",
        "GradeCodes": "False",
        "InterpolationTypes": "False",
        "Timezone": "-6",
        "_": str(int(time.time() * 1000)),
    }

# ------------------------------------------------------------------
def fetch_ibwc_daily_rounded_afday(station_id: str, start, end) -> pd.Series:

    params = build_ibwc_params(station_id, start, end)

    resp = requests.get(EXPORT_ENDPOINT, params=params, timeout=HTTP_TIMEOUT)
    #print("DEBUG STATUS:", resp.status_code)
    print("[IBWC]", resp.url)
    #print("DEBUG RESPONSE:", resp.text[:500])
    resp.raise_for_status()

    if resp.status_code != 200:
        print(f"[IBWC] Request failed: {resp.status_code}")
        return pd.Series(dtype="float64", name=station_id)

    text = resp.text.strip()
    

    if not text:
        return pd.Series(dtype="float64", name=station_id)

    df = pd.read_csv(io.StringIO(text), skiprows=4)
    date_col = df.columns[0]
    # Remove disclaimer/footer rows
    df = df[df[date_col].astype(str).str.contains(r"^\d{4}-\d{2}-\d{2}", regex=True)]


    if df.empty:
        return pd.Series(dtype="float64", name=station_id)
    print("DEBUG columns:", df.columns.tolist())
    # ------------------------------------------------------
    #detect date column
    # ------------------------------------------------------
    date_col = next(
        (c for c in df.columns if "date" in c.lower() or "time" in c.lower()),
        df.columns[0]
    )

    df[date_col] = pd.to_datetime(df[date_col]).dt.tz_localize(None)

    # ------------------------------------------------------
    # detect value column
    # ------------------------------------------------------
    value_col = None
    for c in df.columns:
        if c == date_col:
            continue

        vals = pd.to_numeric(df[c], errors="coerce")

        if vals.notna().sum() > 0:
            value_col = c
            break

    if value_col is None:
        print("[IBWC] No numeric column detected")
        return pd.Series(dtype="float64", name=station_id)

    # ------------------------------------------------------
    #  build series
    # ------------------------------------------------------
    s = pd.Series(
        pd.to_numeric(df[value_col], errors="coerce").values,
        index=df[date_col],
    )
    s = s * CFS_TO_AFD
    s = s.dropna().sort_index()
    s.name = station_id

    return normalize_daily_series(s, start=start, end=end)
    
from tx_fwi.sources.base import registry

@registry.register(source="ibwc", special=None)
def ibwc_handler(start, end, *, site_id=None, meta=None):
    return fetch_ibwc_daily_rounded_afday(site_id, start, end)



