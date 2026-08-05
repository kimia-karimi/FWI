# tx_fwi/sources/usgs.py
"""
USGS Water Data OGC API (daily values) source adapter.
This adapter fetches USGS daily mean discharge using the Water Data OGC API.
Daily mean streamflow:
    parameter_code = 00060
    statistic_id = 00003
The OGC API is paginated. A request without an explicit limit can return only a small default page, such as 10 features. This adapter sets limit=50000 and also follows rel="next" pagination links until all pages are collected.
"""

from __future__ import annotations

import requests
import pandas as pd
from tx_fwi.transforms.temporal import normalize_daily_series
from tx_fwi.transforms.units import cfs_to_afday
import os
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from tx_fwi.sources.base import registry


BASE_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily"
TIMEOUT = 60
# USGS Water Data daily API allows a maximum page size of 50,000.
LIMIT = 50000
# USGS discharge, in cubic feet per second.
PARAMETER_CODE_DISCHARGE = "00060"
# Daily mean statistic.
STATISTIC_ID_MEAN = "00003"

MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.5
# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------
def _api_key() -> str | None:
    """
    Return optional USGS API key.

    Supported environment variables:
        USGS_API_KEY
        API_USGS_PAT

    The API key is optional, but recommended for larger batch jobs.
    """
    return os.getenv("USGS_API_KEY") or os.getenv("API_USGS_PAT")


def _new_session() -> requests.Session:
    """
    Create a new requests session.

    A separate session is created per fetch call, which is safer when using
    ThreadPoolExecutor.
    """
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/geo+json, application/json",
            "User-Agent": "tx_fwi/1.0",
        }
    )
    return session


def _get_next_href(data: dict) -> str | None:
    """
    Extract the OGC pagination next link from a response, if present.
    """
    for link in data.get("links", []):
        if link.get("rel") == "next" and link.get("href"):
            return link["href"]
    return None


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict | None = None,
) -> dict:
    """
    Request JSON with simple retry/backoff handling.

    Retries are used for rate limits and transient gateway/server errors.
    """
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)

            if resp.status_code in (429, 500, 502, 503, 504):
                sleep_s = (BACKOFF_BASE_SECONDS ** attempt) + random.uniform(0, 0.5)
                time.sleep(sleep_s)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as exc:
            last_error = exc
            sleep_s = (BACKOFF_BASE_SECONDS ** attempt) + random.uniform(0, 0.5)
            time.sleep(sleep_s)

    raise RuntimeError(
        f"USGS request failed after {MAX_RETRIES} attempts: {last_error}"
    )


def _fetch_all_daily_features(site_id: str, start: str, end: str) -> list[dict]:
    """
    Fetch all daily-value features for one USGS site by following pagination.
    """
    session = _new_session()

    params = {
        "f": "json",
        "lang": "en-US",
        "monitoring_location_id": f"USGS-{site_id}",
        "parameter_code": PARAMETER_CODE_DISCHARGE,
        "statistic_id": STATISTIC_ID_MEAN,
        "time": f"{start}/{end}",
        "properties": (
            "time,value,parameter_code,statistic_id,"
            "monitoring_location_id,approval_status,qualifier"
        ),
        "skipGeometry": "TRUE",
        "limit": LIMIT,
    }

    key = _api_key()
    if key:
        params["api_key"] = key

    features: list[dict] = []

    next_url = f"{BASE_URL}/items"
    next_params = params

    while next_url:
        data = _request_json(session, next_url, params=next_params)

        page_features = data.get("features", [])
        features.extend(page_features)

        # After the first request, the next link already contains its query string.
        next_url = _get_next_href(data)
        next_params = None

        # Safety stop. If the API returns an empty page, we are done.
        if not page_features:
            break

    return features


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
        name = site_id
    """

    start = pd.to_datetime(start).strftime("%Y-%m-%d")
    end = pd.to_datetime(end).strftime("%Y-%m-%d")
    site_id = str(site_id)

    features = _fetch_all_daily_features(site_id, start, end)
    
    if not features:
        return pd.Series(dtype="float64", name=site_id)

    records = []

    for f in features:
        props = f.get("properties", {})

        dt = props.get("time")
        val = props.get("value")
        parameter_code = props.get("parameter_code")
        statistic_id = props.get("statistic_id")

        if dt is None or val is None:
            continue

        records.append((dt, val))

    if not records:
        return pd.Series(dtype="float64", name=site_id)

    df = pd.DataFrame(records, columns=["date", "value"])

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    
    df = df.dropna(subset=["date"]).assign(date=lambda x: x["date"].dt.normalize()).set_index("date").sort_index()
   # Defensive handling in case the source has duplicate date rows.
    # With statistic_id=00003 this should normally be one row per date.
    df = df.groupby(level=0)["value"].mean().to_frame()
    # Ensure daily continuity
    s = normalize_daily_series(df["value"], start=start, end=end)
    print("rows:", len(s))
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

# ------------------------------------------------------------------
# PARALLEL FETCH FUNCTIONS
# ------------------------------------------------------------------
def fetch_usgs_daily_cfs_many(
    site_ids,
    start,
    end,
    *,
    max_workers: int = 8,
    raise_errors: bool = True,
) -> dict[str, pd.Series]:
    """
    Fetch daily mean streamflow in cfs for multiple USGS sites in parallel.

    Parameters
    ----------
    site_ids : iterable
        USGS site IDs without the USGS- prefix.
    start, end : str or date-like
        Date range.
    max_workers : int, default 8
        Number of worker threads.
    raise_errors : bool, default True
        If True, raise one combined error if any site fails.
        If False, return successful sites only.

    Returns
    -------
    dict[str, pandas.Series]
        keys = site IDs
        values = daily cfs series
    """

    site_ids = [str(s) for s in site_ids]

    results: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}

    if not site_ids:
        return results

    workers = max(1, min(max_workers, len(site_ids)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_site = {
            executor.submit(fetch_usgs_daily_cfs, site_id, start, end): site_id
            for site_id in site_ids
        }

        for future in as_completed(future_to_site):
            site_id = future_to_site[future]

            try:
                results[site_id] = future.result()
            except Exception as exc:
                errors[site_id] = str(exc)

    if errors and raise_errors:
        msg = "\n".join(f"{site}: {err}" for site, err in errors.items())
        raise RuntimeError(
            f"USGS parallel fetch failed for {len(errors)} site(s):\n{msg}"
        )

    return results


def fetch_usgs_daily_afday_many(
    site_ids,
    start,
    end,
    *,
    max_workers: int = 8,
    raise_errors: bool = True,
) -> dict[str, pd.Series]:
    """
    Fetch daily mean streamflow for multiple USGS sites in parallel
    and convert from cfs to acre-feet/day.

    Parameters
    ----------
    site_ids : iterable
        USGS site IDs without the USGS- prefix.
    start, end : str or date-like
        Date range.
    max_workers : int, default 8
        Number of worker threads.
    raise_errors : bool, default True
        If True, raise one combined error if any site fails.
        If False, return successful sites only.

    Returns
    -------
    dict[str, pandas.Series]
        keys = site IDs
        values = daily acre-feet/day series
    """

    site_ids = [str(s) for s in site_ids]

    results: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}

    if not site_ids:
        return results

    workers = max(1, min(max_workers, len(site_ids)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_site = {
            executor.submit(fetch_usgs_daily_afday, site_id, start, end): site_id
            for site_id in site_ids
        }

        for future in as_completed(future_to_site):
            site_id = future_to_site[future]

            try:
                results[site_id] = future.result()
            except Exception as exc:
                errors[site_id] = str(exc)

    if errors and raise_errors:
        msg = "\n".join(f"{site}: {err}" for site, err in errors.items())
        raise RuntimeError(
            f"USGS parallel fetch failed for {len(errors)} site(s):\n{msg}"
        )

    return results


def fetch_usgs_daily_afday_many_df(
    site_ids,
    start,
    end,
    *,
    max_workers: int = 8,
    raise_errors: bool = True,
) -> pd.DataFrame:
    """
    Fetch multiple USGS sites in parallel and return a wide dataframe.

    Parameters
    ----------
    site_ids : iterable
        USGS site IDs without the USGS- prefix.
    start, end : str or date-like
        Date range.
    max_workers : int, default 8
        Number of worker threads.
    raise_errors : bool, default True
        If True, raise one combined error if any site fails.
        If False, return successful sites only.

    Returns
    -------
    pandas.DataFrame
        index = daily date
        columns = USGS site IDs
        values = acre-feet/day
    """

    results = fetch_usgs_daily_afday_many(
        site_ids,
        start,
        end,
        max_workers=max_workers,
        raise_errors=raise_errors,
    )

    if not results:
        return pd.DataFrame()

    return pd.concat(results, axis=1).sort_index()


# ------------------------------------------------------------------
# REGISTRY HOOK
# ------------------------------------------------------------------
@registry.register(source="usgs", special=None)
def usgs_gage_handler(start, end, *, site_id=None, meta=None):
    return fetch_usgs_daily_afday(site_id, start, end)
