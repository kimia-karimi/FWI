# tx_fwi/sources/colorado.py

from __future__ import annotations
import pandas as pd

from tx_fwi.sources.usgs import fetch_usgs_daily_afday


# ------------------------------------------------------------------
# CONFIG (based on your legacy script)
# ------------------------------------------------------------------
BAY_CITY_GAGE = "08162500"
WHARTON_GAGE = "08162000"

CUTOFF_DATE = pd.Timestamp("2013-10-01")
LOW_FLOW_THRESHOLD = 2300  # acre-ft/day (same threshold used in your script)


# ------------------------------------------------------------------
def colorado_adjusted_afday(start, end) -> pd.Series:
    """
    Colorado River adjusted flow (Bay City adjusted with Wharton).

    Rules:
    1) Fill missing Bay City with Wharton
    2) After 2013-10-01, if Bay City < 2300 → replace with Wharton
    """

    print("[Colorado] Fetching Bay City + Wharton...")

    bay_city = fetch_usgs_daily_afday(BAY_CITY_GAGE, start, end)
    wharton = fetch_usgs_daily_afday(WHARTON_GAGE, start, end)

    if bay_city.empty:
        return bay_city

    if wharton.empty:
        # fallback — if upstream fails, return Bay City as-is
        return bay_city

    # ------------------------------------------------------
    # ✅ ALIGN TIMESERIES
    # ------------------------------------------------------
    df = pd.concat(
        [
            bay_city.rename("bay_city"),
            wharton.rename("wharton"),
        ],
        axis=1
    )

    # ------------------------------------------------------
    # ✅ RULE 1: Fill missing Bay City
    # ------------------------------------------------------
    df["bay_city"] = df["bay_city"].fillna(df["wharton"])

    # ------------------------------------------------------
    # ✅ RULE 2: Low-flow replacement AFTER cutoff date
    # ------------------------------------------------------
    mask = (
        (df.index > CUTOFF_DATE) &
        (df["bay_city"] < LOW_FLOW_THRESHOLD)
    )

    df.loc[mask, "bay_city"] = df.loc[mask, "wharton"]

    # ------------------------------------------------------
    # ✅ RESULT
    # ------------------------------------------------------
    s = df["bay_city"].copy()
    s.name = "colorado_adjusted"
    s = s.sort_index()

    return s
