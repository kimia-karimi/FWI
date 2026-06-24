"""IBWC Aquarius daily rounded discharge source adapter."""
from __future__ import annotations
import io
import time
import requests
import pandas as pd
from tx_fwi.transforms.units import cfs_to_afday
from tx_fwi.transforms.temporal import normalize_daily_series

EXPORT_ENDPOINT = "https://waterdata.ibwc.gov/AQWebportal/Export/DataSet"
DATASET_NAME = "Discharge.Daily Rounded"
TIMEZONE_OFFSET = -6
HTTP_TIMEOUT = 60


def build_ibwc_params(station_id: str, start, end) -> dict:
    """Build Aquarius WebPortal export parameters for daily rounded discharge."""
    start = pd.to_datetime(start).normalize()
    end = pd.to_datetime(end).normalize()
    dataset_ref = f"{station_id}@{DATASET_NAME}"
    return {
        "DataSet": dataset_ref,
        "Calendar": "CALENDARYEAR",
        "DateRange": "Custom",
        "StartTime": start.strftime("%Y-%m-%dT00:00:00"),
        "EndTime": end.strftime("%Y-%m-%dT23:59:59"),
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
        "Timezone": str(TIMEZONE_OFFSET),
        "_": str(int(time.time() * 1000)),
    }


def fetch_ibwc_daily_rounded_cfs(station_id: str, start, end) -> pd.Series:
    """
    Fetch IBWC daily rounded discharge as cfs.

    The exact exported column names can vary; this parser looks for a date-like
    column and the first numeric value column. Adjust here if IBWC export format
    changes.
    """
    params = build_ibwc_params(station_id, start, end)
    resp = requests.get(EXPORT_ENDPOINT, params=params, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()

    text = resp.text.strip()
    if not text:
        return pd.Series(dtype="float64", name=station_id)

    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        return pd.Series(dtype="float64", name=station_id)

    date_col = next((c for c in df.columns if "date" in c.lower() or "time" in c.lower()), df.columns[0])
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce", infer_datetime_format=True)
    
    numeric_candidates = []
    for c in df.columns:
        if c == date_col:
            continue
        vals = pd.to_numeric(df[c], errors="coerce")
        if vals.notna().sum() > 0:
            numeric_candidates.append(c)

    if not numeric_candidates:
        return pd.Series(dtype="float64", name=station_id)

    value_col = numeric_candidates[0]
    df["value"] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()
    s = normalize_daily_series(df["value"], start=start, end=end)
    s.name = station_id
    return s


def fetch_ibwc_daily_rounded_afday(station_id: str, start, end) -> pd.Series:
    """Fetch IBWC daily rounded discharge and convert cfs to acre-feet/day."""
    s = fetch_ibwc_daily_rounded_cfs(station_id, start, end)
    out = cfs_to_afday(s)
    out.name = station_id
    return out

s = fetch_ibwc_daily_rounded_afday("2023-01-01", "2025-01-01")
print(len(s), s.head(), s.tail())