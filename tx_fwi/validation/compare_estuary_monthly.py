"""Create monthly component comparison outputs and validation summaries."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from compare_components import compare_component_tables
from validation_metrics import summarize_metrics


def build_monthly_outputs(
    historic_path: str | Path,
    new_path: str | Path,
    *,
    abs_tolerance_af: float = 1.0,
    pct_tolerance: float = 1.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    detail = compare_component_tables(
        historic_path, new_path,
        abs_tolerance_af=abs_tolerance_af,
        pct_tolerance=pct_tolerance,
    )
    metrics = summarize_metrics(detail)
    coverage = (
        detail.assign(month_date=pd.to_datetime(dict(year=detail["Year"], month=detail["Month"], day=1)))
        .groupby(["Estuary", "component"], as_index=False, dropna=False)
        .agg(
            first_month=("month_date", "min"),
            last_month=("month_date", "max"),
            months_total=("month_date", "size"),
            months_paired=("status", lambda s: int((~s.isin(["historic_only", "new_only", "missing_both"])).sum())),
            historic_only=("status", lambda s: int((s == "historic_only").sum())),
            new_only=("status", lambda s: int((s == "new_only").sum())),
        )
    )
    return detail, metrics, coverage


def main() -> None:
    parser = argparse.ArgumentParser(description="Run monthly estuary component validation.")
    parser.add_argument("--historic", required=True, help="Historic monthly long or wide CSV/Parquet.")
    parser.add_argument("--new", required=True, help="New monthly long or wide CSV/Parquet.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--abs-tolerance-af", type=float, default=1.0)
    parser.add_argument("--pct-tolerance", type=float, default=1.0)
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    detail, metrics, coverage = build_monthly_outputs(
        args.historic, args.new,
        abs_tolerance_af=args.abs_tolerance_af,
        pct_tolerance=args.pct_tolerance,
    )
    detail.to_csv(output / "monthly_component_comparison.csv", index=False)
    metrics.to_csv(output / "monthly_component_metrics.csv", index=False)
    coverage.to_csv(output / "monthly_component_coverage.csv", index=False)
    print(f"Wrote monthly validation outputs to {output}")


if __name__ == "__main__":
    main()
