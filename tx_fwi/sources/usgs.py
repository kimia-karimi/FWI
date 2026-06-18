"""USGS NWIS daily-value source adapter."""
from __future__ import annotations
import pandas as pd
from tx_fwi.transforms.units import cfs_to_afday
from tx_fwi.transforms.temporal import normalize_daily_series

try:
    import ulmo
except ImportError:  # Allows import in environments before ulmo is installed.
    ulmo = None


def fetch_usgs_daily_cfs(site_id: str, start, end) -> pd.Series:
    """Fetch USGS daily mean discharge, parameter 00060:00003, in cfs."""
    if ulmo is None:
        raise ImportError("ulmo is required for USGS retrieval. Install/import it in your runtime environment.")

    raw = ulmo.usgs.nwis.get_site_data(site_id, start=pd.to_datetime(start), end=pd.to_datetime(end), service="dv")
    values = raw.get("00060:00003", {}).get("values", [])
    df = pd.DataFrame(values)
    if df.empty:
        return pd.Series(dtype="float64", name=site_id)

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    s = normalize_daily_series(df["value"], start=start, end=end)
    s.name = site_id
    return s


def fetch_usgs_daily_afday(site_id: str, start, end) -> pd.Series:
    """Fetch USGS daily mean discharge and convert cfs to acre-feet/day."""
    s = fetch_usgs_daily_cfs(site_id, start=start, end=end)
    out = cfs_to_afday(s)
    out.name = site_id
    return out
