# tx_fwi/sources/wdft_watercycle.py

from __future__ import annotations

import pandas as pd

BASE = "https://waterdatafortexas.org/lake-evaporation-rainfall/api/watercycle-data"
PRECIP_URL = f"{BASE}/precipitation?data_format=json"
EVAP_URL   = f"{BASE}/gross_evaporation?data_format=json"
HTTP_TIMEOUT = 120
def _fetch_metric(
    endpoint: str,
    start: str,
    end: str | None = None,
):

    params = {
        "data_format": "json",
        "start_date": start,
    }

    if end:
        params["end_date"] = end

    r = requests.get(
        endpoint,
        params=params,
        timeout=HTTP_TIMEOUT,
    )

    r.raise_for_status()

    payload = r.json()

    if not isinstance(payload, list):
        raise ValueError(
            f"Unexpected payload type: {type(payload)}"
        )

    #
    # WDFT returns wide data:
    # period + quad columns
    #

    wide = pd.DataFrame(payload)

    if wide.empty:
        return pd.DataFrame()

    id_col = wide.columns[0]

    long = wide.melt(
        id_vars=[id_col],
        var_name="quad",
        value_name="value",
    )

    long = long.rename(
        columns={
            id_col: "period",
        }
    )

    long["period"] = pd.to_datetime(
        long["period"],
        format="%Y-%m",
        errors="coerce",
    )

    long["year"] = long["period"].dt.year
    long["month"] = long["period"].dt.month

    long["quad"] = (
        long["quad"]
        .astype(str)
        .str.strip()
    )

    long["value"] = pd.to_numeric(
        long["value"],
        errors="coerce",
    )

    return long
def fetch_watercycle(
    start,
    end,
  
):
  """
    Return monthly WDFT precipitation and
    evaporation by quad.

    Expected output:

        year
        month
        quad
        precip_inches
        evap_inches
    """
    precip = _fetch_metric(
        PRECIP_ENDPOINT,
        start,
        end,
    )

    evap = _fetch_metric(
        EVAP_ENDPOINT,
        start,
        end,
    )
    if precip.empty:
        return pd.DataFrame()

    precip = precip.rename(
        columns={
            "value": "precip_inches",
        }
    )

    evap = evap.rename(
        columns={
            "value": "evap_inches",
        }
    )
    return precip.merge(
        evap[
            [
                "period",
                "quad",
                "evap_inches",
            ]
        ],
        on=[
            "period",
            "quad",
        ],
        how="outer",
    )

    
def fetch_precipitation(start, end):

    df = _request_watercycle(
        start=start,
        end=end,
        metric="precip",
    )

    if df.empty:
        return df

    return (
        df.rename(
            columns={
                "value": "precip_inches"
            }
        )

def fetch_evaporation(start, end):

    df = _request_watercycle(
        start=start,
        end=end,
        metric="evap",
    )

    if df.empty:
        return df

    return (
        df.rename(
            columns={
                "value": "evap_inches"
            }
        )
    )
