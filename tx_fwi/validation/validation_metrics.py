"""Reusable metrics and validation helpers for FWI component comparisons."""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

KEY_COLUMNS = ["Year", "Month", "Estuary", "component"]
COMPONENTS = ["gaged", "model", "diversion", "return"]


def safe_percent_difference(new: pd.Series, old: pd.Series) -> pd.Series:
    """Return 100 * (new - old) / old; undefined when old is zero."""
    old_num = pd.to_numeric(old, errors="coerce")
    new_num = pd.to_numeric(new, errors="coerce")
    denominator = old_num.mask(old_num == 0)
    return 100.0 * (new_num - old_num) / denominator


def add_row_diagnostics(
    comparison: pd.DataFrame,
    *,
    old_col: str = "historic_af",
    new_col: str = "new_af",
    abs_tolerance_af: float = 1.0,
    pct_tolerance: float = 1.0,
) -> pd.DataFrame:
    """Add residual, percent-difference, match-status, and tolerance columns."""
    out = comparison.copy()
    out[old_col] = pd.to_numeric(out[old_col], errors="coerce")
    out[new_col] = pd.to_numeric(out[new_col], errors="coerce")
    out["difference_af"] = out[new_col] - out[old_col]
    out["abs_difference_af"] = out["difference_af"].abs()
    out["pct_difference"] = safe_percent_difference(out[new_col], out[old_col])

    old_missing = out[old_col].isna()
    new_missing = out[new_col].isna()
    both_zero = out[old_col].eq(0) & out[new_col].eq(0)
    within_abs = out["abs_difference_af"].le(abs_tolerance_af)
    within_pct = out["pct_difference"].abs().le(pct_tolerance)

    out["status"] = np.select(
        [old_missing & ~new_missing, new_missing & ~old_missing, old_missing & new_missing,
         both_zero, within_abs | within_pct],
        ["new_only", "historic_only", "missing_both", "both_zero", "within_tolerance"],
        default="different",
    )
    out["passes_tolerance"] = out["status"].isin(["both_zero", "within_tolerance"])
    return out


def _pearson(group: pd.DataFrame, old_col: str, new_col: str) -> float:
    paired = group[[old_col, new_col]].dropna()
    if len(paired) < 2 or paired[old_col].nunique() < 2 or paired[new_col].nunique() < 2:
        return float("nan")
    return float(paired[old_col].corr(paired[new_col]))


def summarize_metrics(
    comparison: pd.DataFrame,
    *,
    group_cols: Iterable[str] = ("Estuary", "component"),
    old_col: str = "historic_af",
    new_col: str = "new_af",
) -> pd.DataFrame:
    """Summarize coverage and error metrics for paired historic/new values."""
    groups = list(group_cols)
    rows: list[dict] = []
    for keys, g in comparison.groupby(groups, dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        paired = g[[old_col, new_col]].dropna()
        residual = paired[new_col] - paired[old_col]
        record = dict(zip(groups, keys))
        record.update(
            rows_total=len(g),
            months_paired=len(paired),
            historic_only=int((g[old_col].notna() & g[new_col].isna()).sum()),
            new_only=int((g[old_col].isna() & g[new_col].notna()).sum()),
            historic_total_af=float(paired[old_col].sum()) if len(paired) else np.nan,
            new_total_af=float(paired[new_col].sum()) if len(paired) else np.nan,
            bias_af=float(residual.mean()) if len(residual) else np.nan,
            mae_af=float(residual.abs().mean()) if len(residual) else np.nan,
            rmse_af=float(np.sqrt(np.mean(np.square(residual)))) if len(residual) else np.nan,
            max_abs_difference_af=float(residual.abs().max()) if len(residual) else np.nan,
            correlation=_pearson(g, old_col, new_col),
            n_within_tolerance=int(g.get("passes_tolerance", pd.Series(False, index=g.index)).sum()),
        )
        denom = record["historic_total_af"]
        record["total_pct_difference"] = (
            100.0 * (record["new_total_af"] - denom) / denom
            if pd.notna(denom) and denom != 0 else np.nan
        )
        rows.append(record)
    return pd.DataFrame(rows)


def assert_unique_keys(df: pd.DataFrame, keys: Iterable[str], *, label: str) -> None:
    """Raise with examples when a table has duplicate comparison keys."""
    keys = list(keys)
    duplicate = df.duplicated(keys, keep=False)
    if duplicate.any():
        examples = df.loc[duplicate, keys].head(10).to_dict("records")
        raise ValueError(f"{label} has duplicate keys {keys}. Examples: {examples}")
