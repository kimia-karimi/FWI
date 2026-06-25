# tx_fwi/sources/lake_houston.py

from __future__ import annotations
import pandas as pd
import numpy as np
import requests
import io



BASE_URL = "https://waterservices.usgs.gov/nwis/dv/"
GAGE_ID = "08072000"
HTTP_TIMEOUT = 60

CFS_TO_AFD = 1.983471


from tx_fwi.sources.usgs_utils import fetch_usgs_rdb, extract_usgs_mean_columns
from tx_fwi.transforms.temporal import normalize_daily_series



# ------------------------------------------------------------------
def lake_houston_afday(start, end) -> pd.Series:
    """
    Production-ready Lake Houston discharge estimation.
    """

    print(f"[Lake Houston] Fetching {start} → {end}")

    # ✅ fetch raw USGS data
    df_raw = fetch_usgs_rdb(GAGE_ID, "00054,00065", start, end)

    if df_raw.empty:
        return pd.Series(dtype="float64")

    # ✅ extract mean columns
    df = extract_usgs_mean_columns(
        df_raw,
        {
            "res_storage": "00054",
            "gage_height": "00065",
        },
    )

    if df.empty:
        return pd.Series(dtype="float64")

    # ✅ apply +2.77 ft correction (only to gage height)
    df["gage_height"] = df["gage_height"] + 2.77

    gh = df["gage_height"]

    # ✅ discharge calculation (vectorized)
    discharge_cfs = np.where(
        gh >= 44.80,
        5418.31 * (gh - 44.50) ** 2 - 1336.29 * (gh - 44.50),
        np.where(
            gh >= 44.50,
            86.76 * (gh - 44.50),
            0.0
        )
    )

    s = pd.Series(discharge_cfs, index=df.index)

    # preserve missing
    s[gh.isna()] = np.nan

    # convert to AFD
    s = s * CFS_TO_AFD

    s = s.dropna().sort_index()
    s.name = "lake_houston"

    return normalize_daily_series(s, start=start, end=end)
    
from tx_fwi.sources.base import registry

@registry.register(source="usgs", special="lake_houston")
def lake_houston_handler(start, end, **kwargs):
    return lake_houston_afday(start, end)
