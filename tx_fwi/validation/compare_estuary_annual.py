"""Aggregate monthly component comparisons to annual estuary/component totals."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from compare_components import compare_component_tables
from validation_metrics import add_row_diagnostics, summarize_metrics

ANNUAL_KEYS = ["Year", "Estuary", "component"]


def build_annual_comparison(
    monthly: pd.DataFrame,
    *,
    abs_tolerance_af: float = 1.0,
    pct_tolerance: float = 1.0,
    require_complete_year: bool = False,
) -> pd.DataFrame:
    work = monthly.copy()
    work["paired"] = work["historic_af"].notna() & work["new_af"].notna()
    annual = (
        work.groupby(ANNUAL_KEYS, as_index=False, dropna=False)
        .agg(
            historic_af=("historic_af", lambda s: s.sum(min_count=1)),
            new_af=("new_af", lambda s: s.sum(min_count=1)),
            months_present=("Month", "nunique"),
            months_paired=("paired", "sum"),
            historic_months=("historic_af", "count"),
            new_months=("new_af", "count"),
        )
    )
    annual["complete_year"] = (
        annual["months_present"].eq(12)
        & annual["historic_months"].eq(12)
        & annual["new_months"].eq(12)
    )
    if require_complete_year:
        annual = annual[annual["complete_year"]].copy()
    return add_row_diagnostics(
        annual,
        abs_tolerance_af=abs_tolerance_af,
        pct_tolerance=pct_tolerance,
    ).sort_values(ANNUAL_KEYS).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run annual estuary component validation.")
    parser.add_argument("--historic", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--abs-tolerance-af", type=float, default=1.0)
    parser.add_argument("--pct-tolerance", type=float, default=1.0)
    parser.add_argument("--require-complete-year", action="store_true")
    args = parser.parse_args()

    monthly = compare_component_tables(args.historic, args.new,
                                       abs_tolerance_af=args.abs_tolerance_af,
                                       pct_tolerance=args.pct_tolerance)
    annual = build_annual_comparison(
        monthly,
        abs_tolerance_af=args.abs_tolerance_af,
        pct_tolerance=args.pct_tolerance,
        require_complete_year=args.require_complete_year,
    )
    metrics = summarize_metrics(annual, group_cols=["Estuary", "component"])
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    annual.to_csv(output / "annual_component_comparison.csv", index=False)
    metrics.to_csv(output / "annual_component_metrics.csv", index=False)
    print(f"Wrote annual validation outputs to {output}")


if __name__ == "__main__":
    main()
