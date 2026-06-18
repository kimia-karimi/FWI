"""Temporal transformations for FWI components."""
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


def expand_monthly_to_daily(
    df_monthly: pd.DataFrame,
    *,
    year_col: str = "year",
    month_col: str = "month",
    value_col: str = "value",
    id_col: str = "ws_id",
    estuary_col: str | None = "estuary",
    value_is_monthly_total: bool = True,
) -> pd.DataFrame:
    """
    Expand monthly diversion/return records to daily values.

    If value_is_monthly_total=True, each month is divided by the number
    of days in that month; this matches the FWI convention you described
    for monthly diversion and return data.
    """
    if df_monthly.empty:
        return pd.DataFrame(columns=["date", id_col, "value_afday"])

    rows = []
    work = df_monthly.copy()
    work[year_col] = work[year_col].astype(int)
    work[month_col] = work[month_col].astype(int)

    for _, r in work.iterrows():
        first = pd.Timestamp(year=int(r[year_col]), month=int(r[month_col]), day=1)
        days = first.days_in_month
        val = pd.to_numeric(r[value_col], errors="coerce")
        daily_val = val / days if value_is_monthly_total else val
        dates = pd.date_range(first, periods=days, freq="D")

        d = pd.DataFrame({
            "date": dates,
            id_col: str(r[id_col]).zfill(5) if str(r[id_col]).isdigit() else str(r[id_col]),
            "value_afday": daily_val,
        })
        if estuary_col and estuary_col in work.columns:
            d[estuary_col] = r[estuary_col]
        rows.append(d)

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
