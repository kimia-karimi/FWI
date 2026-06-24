# tx_fwi/sources/lake_houston.py

from __future__ import annotations
import pandas as pd
import numpy as np
import requests
import io

from tx_fwi.transforms.temporal import normalize_daily_series


BASE_URL = "https://waterservices.usgs.gov/nwis/dv/"
GAGE_ID = "08072000"
HTTP_TIMEOUT = 60

CFS_TO_AFD = 1.983471


# ------------------------------------------------------------------
def fetch_lake_houston_raw(start, end) -> pd.DataFrame:
    """
    Fetch Lake Houston data (storage + gage height) from USGS.
    """

    params = {
        "format": "rdb",
        "sites": GAGE_ID,
        "parameterCd": "00054,00065",
        "startDT": pd.to_datetime(start).strftime("%Y-%m-%d"),
        "endDT": pd.to_datetime(end).strftime("%Y-%m-%d"),
    }

    resp = requests.get(BASE_URL, params=params, timeout=HTTP_TIMEOUT)

    if resp.status_code != 200:
        print(f"[Lake Houston] API error: {resp.status_code}")
        return pd.DataFrame()

    text = resp.text

    if not text.strip():
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep="\t",
            comment="#",
            dtype=str
        )
    except Exception as e:
        print(f"[Lake Houston] Failed parsing response: {e}")
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    # --------------------------------------------------
    # Clean + normalize
    # --------------------------------------------------
    if "agency_cd" in df.columns:
        df = df[df["agency_cd"] != "5s"]

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")

    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()

    # Identify relevant columns dynamically
    cols = [c for c in df.columns if "00054" in c or "00065" in c]

    if len(cols) < 2:
        print("[Lake Houston] Missing expected parameters")
        return pd.DataFrame()

    df = df[cols]

    df.columns = ["res_storage", "gage_height"]

    df = df.apply(pd.to_numeric, errors="coerce")

    return df


# ------------------------------------------------------------------
def compute_lake_houston_discharge(df: pd.DataFrame) -> pd.Series:
    """
    Apply rating curve + corrections to compute discharge.
    """

    if df is None or df.empty:
        return pd.Series(dtype="float64")

    df = df.copy()

    # ✅ Apply +2.77 ft correction (matches your original code)
    df["gage_height"] = df["gage_height"] + 2.77

    gh = df["gage_height"]

    # --------------------------------------------------
    # Vectorized discharge logic
    # --------------------------------------------------
    discharge_cfs = np.where(
        gh >= 44.80,
        5418.31 * (gh - 44.50) ** 2 - 1336.29 * (gh - 44.50),  # parabolic
        np.where(
            gh >= 44.50,
            86.76 * (gh - 44.50),  # linear
            0.0
        )
    )

    discharge = pd.Series(discharge_cfs, index=df.index)

    # Preserve missing values
    discharge[gh.isna()] = np.nan

    # Convert to acre-feet/day
    discharge_afday = discharge * CFS_TO_AFD

    discharge_afday.name = "lake_houston"

    return discharge_afday


# ------------------------------------------------------------------
def lake_houston_afday(start, end) -> pd.Series:
    """
    Production-ready Lake Houston discharge function
    (returns acre-feet/day time series).
    """

    print(f"[Lake Houston] Fetching data {start} → {end}")

    df = fetch_lake_houston_raw(start, end)

    if df.empty:
        return pd.Series(dtype="float64")

    s = compute_lake_houston_discharge(df)

    if s.empty:
        return s

    # Clean + normalize
    s = s.dropna().sort_index()

    return normalize_daily_series(s, start=start, end=end)
