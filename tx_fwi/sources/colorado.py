# tx_fwi/sources/colorado.py

from __future__ import annotations
import pandas as pd

from tx_fwi.sources.usgs_utils import fetch_usgs_rdb, extract_usgs_mean_columns
from tx_fwi.transforms.temporal import normalize_daily_series


# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
BAY_CITY_GAGE = "08162500"
WHARTON_GAGE = "08162000"

CUTOFF_DATE = pd.Timestamp("2013-10-01")

CFS_TO_AFD = 1.983471
LOW_FLOW_THRESHOLD_AFD = 2300 * CFS_TO_AFD  # conversion


# ------------------------------------------------------------------
def _fetch_discharge_afday(site_id: str, start, end) -> pd.Series:
    """
    Internal helper: fetch USGS discharge (00060 mean) as AFD.
    """

    df_raw = fetch_usgs_rdb(site_id, "00060", start, end)

    if df_raw.empty:
        return pd.Series(dtype="float64")

    df = extract_usgs_mean_columns(
        df_raw,
        {"flow": "00060"},
    )

    if df.empty:
        return pd.Series(dtype="float64")

    s = df["flow"] * CFS_TO_AFD

    s = s.dropna().sort_index()

    return normalize_daily_series(s, start=start, end=end)


# ------------------------------------------------------------------
def colorado_adjusted_afday(start, end) -> pd.Series:
    """
    Colorado River adjusted flow.

    Rules:
    1) Fill missing Bay City with Wharton
    2) After 2013-10-01, if Bay City < 2300 cfs → use Wharton
    """

    print("[Colorado] Fetching Bay City + Wharton via USGS utils...")

    bay_city = _fetch_discharge_afday(BAY_CITY_GAGE, start, end)
    wharton = _fetch_discharge_afday(WHARTON_GAGE, start, end)

    if bay_city.empty:
        return bay_city

    if wharton.empty:
        return bay_city

    # ------------------------------------------------------
    # ALIGN TIMESERIES
    # ------------------------------------------------------
    df = pd.concat(
        [
            bay_city.rename("bay_city"),
            wharton.rename("wharton"),
        ],
        axis=1
    )

    # ------------------------------------------------------
    #  RULE 1: Fill missing Bay City
    # ------------------------------------------------------
    df["bay_city"] = df["bay_city"].fillna(df["wharton"])

    # ------------------------------------------------------
    #  RULE 2: Low-flow replacement (after cutoff)
    # ------------------------------------------------------
    mask = (
        (df.index > CUTOFF_DATE) &
        (df["bay_city"] < LOW_FLOW_THRESHOLD_AFD)
    )

    df.loc[mask, "bay_city"] = df.loc[mask, "wharton"]

    # ------------------------------------------------------
    #  FINAL SERIES
    # ------------------------------------------------------
    s = df["bay_city"].copy()

    s = s.dropna().sort_index()
    s.name = "colorado_adjusted"

    return s

from tx_fwi.sources.base import registry

@registry.register(source="usgs", special="colorado_adjusted")
def colorado_handler(start, end, *, site_id=None, meta=None):
    return colorado_adjusted_afday(start, end)
