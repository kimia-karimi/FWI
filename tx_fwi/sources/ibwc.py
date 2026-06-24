"""IBWC Aquarius daily rounded discharge source adapter."""
from __future__ import annotations
import io
import time
import requests
import pandas as pd
#from tx_fwi.transforms.units import cfs_to_afday
#from tx_fwi.transforms.temporal import normalize_daily_series

from __future__ import annotations
import pandas as pd


def normalize_daily_series(series: pd.Series, start=None, end=None) -> pd.Series:
    """
    Normalize a time-indexed Series to daily frequency.

    Parameters
    ----------
    series : pandas.Series
        Index must be datetime-like; values are numeric.
    start, end : optional
        Optional daily date bounds.
    """
    if series is None or series.empty:
        return pd.Series(dtype="float64")

    s = series.copy()
    s.index = pd.to_datetime(s.index).normalize()
    s = pd.to_numeric(s, errors="coerce")
    s = s.groupby(level=0).mean().sort_index()

    if start is not None or end is not None:
        start = pd.to_datetime(start).normalize() if start is not None else s.index.min()
        end = pd.to_datetime(end).normalize() if end is not None else s.index.max()
        full = pd.date_range(start, end, freq="D")
        s = s.reindex(full)

    return s

CFS_TO_AFD = 1.983471
MGD_TO_AFD = 3.06888328
INCH_TO_FEET = 1.0 / 12.0


def cfs_to_afday(value):
    """Convert cubic feet per second to acre-feet per day."""
    return value * CFS_TO_AFD



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

import zipfile
# ------------------------------------------------------------------
def fetch_ibwc_daily_rounded_cfs(station_id: str, start, end) -> pd.Series:

    params = build_ibwc_params(station_id, start, end)

    resp = requests.get(EXPORT_ENDPOINT, params=params, timeout=HTTP_TIMEOUT)
    print("DEBUG STATUS:", resp.status_code)
    print("DEBUG URL:", resp.url)
    print("DEBUG RESPONSE:", resp.text[:500])
    resp.raise_for_status()

    if resp.status_code != 200:
        print(f"[IBWC] Request failed: {resp.status_code}")
        return pd.Series(dtype="float64", name=station_id)

    text = resp.text.strip()
    

    if not text:
        return pd.Series(dtype="float64", name=station_id)

    df = pd.read_csv(io.StringIO(text), skiprows=4)

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

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # ------------------------------------------------------
    # ✅ detect value column
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
    # ✅ build series
    # ------------------------------------------------------
    s = pd.Series(
        pd.to_numeric(df[value_col], errors="coerce").values,
        index=df[date_col],
    )

    s = s.dropna().sort_index()
    s.name = station_id

    return normalize_daily_series(s, start=start, end=end)



