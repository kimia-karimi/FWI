# tx_fwi/sources/usgs_utils.py

from __future__ import annotations
import pandas as pd
import requests
import io


BASE_URL = "https://waterservices.usgs.gov/nwis/dv/"
HTTP_TIMEOUT = 60


def fetch_usgs_rdb(site_id: str, params: str, start, end) -> pd.DataFrame:
    """
    Generic USGS RDB fetcher.
    """

    req = {
        "format": "rdb",
        "sites": site_id,
        "parameterCd": params,
        "startDT": pd.to_datetime(start).strftime("%Y-%m-%d"),
        "endDT": pd.to_datetime(end).strftime("%Y-%m-%d"),
    }

    resp = requests.get(BASE_URL, params=req, timeout=HTTP_TIMEOUT)

    if resp.status_code != 200:
        print(f"[USGS] API error {resp.status_code}")
        return pd.DataFrame()

    try:
        df = pd.read_csv(io.StringIO(resp.text), sep="\t", comment="#", dtype=str)
    except Exception as e:
        print(f"[USGS] Parse error: {e}")
        return pd.DataFrame()

    if df.empty or "datetime" not in df.columns:
        return pd.DataFrame()

    # Clean
    if "agency_cd" in df.columns:
        df = df[df["agency_cd"] != "5s"]

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()

    return df


def extract_usgs_mean_columns(df: pd.DataFrame, param_codes: dict) -> pd.DataFrame:
    """
    Extract mean (00003) columns for given USGS parameter codes.

    param_codes example:
    {
        "res_storage": "00054",
        "gage_height": "00065"
    }
    """

    out = {}

    cols = df.columns.tolist()

    for name, code in param_codes.items():
        col = next((c for c in cols if f"_{code}_00003" in c), None)

        if col is None:
            print(f"[USGS] Missing param {code}_00003")
            out[name] = pd.Series(dtype="float64")
        else:
            out[name] = pd.to_numeric(df[col], errors="coerce")

    return pd.DataFrame(out)
