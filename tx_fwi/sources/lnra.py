"""LNRA / Lake Texana source adapter.

This module intentionally reads the already-maintained Lake Texana daily file.
Your existing PDF parsing / scheduled append process can continue to maintain
that source file; the FWI pipeline only needs a clean daily Series.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from tx_fwi.transforms.temporal import normalize_daily_series


def load_lake_texana_daily_afday(path: str | Path, start=None, end=None) -> pd.Series:
    """
    Load Lake Texana daily release/inflow file and return acre-feet/day.

    Expected flexible formats:
    - whitespace/table file with year month day value
    - CSV with year/month/day/value-like columns
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    try:
        df = pd.read_csv(path)
    except Exception:
        df = pd.read_csv(path, delim_whitespace=True, comment="#", header=0)

    cols_lower = {c.lower(): c for c in df.columns}
    year_col = cols_lower.get("year")
    month_col = cols_lower.get("month")
    day_col = cols_lower.get("day")

    if year_col and month_col and day_col:
        date = pd.to_datetime(dict(year=df[year_col], month=df[month_col], day=df[day_col]), errors="coerce")
        value_cols = [c for c in df.columns if c not in [year_col, month_col, day_col]]
        if not value_cols:
            raise ValueError("Could not find value column in Lake Texana file.")
        value_col = value_cols[-1]
        values = pd.to_numeric(df[value_col], errors="coerce")
        s = pd.Series(values.values, index=date, name="lake_texana_release")
    else:
        date_col = next((c for c in df.columns if "date" in c.lower()), None)
        if date_col is None:
            raise ValueError("Lake Texana file must contain either year/month/day columns or a date column.")
        value_col = next((c for c in df.columns if c != date_col), None)
        if value_col is None:
            raise ValueError("Could not find value column in Lake Texana file.")
        s = pd.Series(pd.to_numeric(df[value_col], errors="coerce").values,
                      index=pd.to_datetime(df[date_col], errors="coerce"),
                      name="lake_texana_release")

    return normalize_daily_series(s.dropna(), start=start, end=end)
