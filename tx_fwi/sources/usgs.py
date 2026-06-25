# tx_fwi/sources/usgs.py
"""
USGS Water Data OGC API (daily values) source adapter.
"""

from __future__ import annotations

import requests
import pandas as pd
from tx_fwi.transforms.temporal import normalize_daily_series
from tx_fwi.transforms.units import cfs_to_afday


BASE_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily"
TIMEOUT = 60


# ------------------------------------------------------------------
# CORE FETCH FUNCTION (OGC API)
# ------------------------------------------------------------------
def fetch_usgs_daily_cfs(site_id: str, start, end) -> pd.Series:
    """
    Fetch daily mean streamflow (00060) from USGS OGC API.

    Returns
    -------
    pandas.Series
        index = date
        values = discharge (cfs)
    """

    start = pd.to_datetime(start).strftime("%Y-%m-%d")
    end = pd.to_datetime(end).strftime("%Y-%m-%d")

    params = {
        "monitoring_location_id": f"USGS-{site_id}",
        "parameter_code": "00060",  # discharge
        "time": f"{start}/{end}",
    }

    resp = requests.get(f"{BASE_URL}/items", params=params, timeout=TIMEOUT)
    resp.raise_for_status()

    data = resp.json()

    features = data.get("features", [])
    if not features:
        return pd.Series(dtype="float64", name=site_id)

    records = []

    for f in features:
        props = f.get("properties", {})

        dt = props.get("time")
        val = props.get("value")

        if dt is None or val is None:
            continue

        records.append((dt, val))

    if not records:
        return pd.Series(dtype="float64", name=site_id)

    df = pd.DataFrame(records, columns=["date", "value"])

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    df = df.dropna(subset=["date"]).set_index("date").sort_index()

    # Ensure daily continuity
    s = normalize_daily_series(df["value"], start=start, end=end)

    s.name = site_id

    return s


# ------------------------------------------------------------------
# CONVERT TO ACRE-FEET/DAY
# ------------------------------------------------------------------
def fetch_usgs_daily_afday(site_id: str, start, end) -> pd.Series:
    """
    Fetch USGS daily streamflow and convert to acre-feet per day.
    """

    s = fetch_usgs_daily_cfs(site_id, start=start, end=end)

    if s.empty:
        return s

    out = cfs_to_afday(s)
    out.name = site_id

    return out

from tx_fwi.sources.base import registry

@registry.register(source="usgs", special=None)
def usgs_gage_handler(start, end, *, site_id=None, meta=None):
    return fetch_usgs_daily_afday(site_id, start, end)
