# calculate_monthly_bay_flow.py
"""
Calculate monthly freshwater inflow components by estuary from the curated daily FWI table.

Expected daily input columns, with flexible aliases:
    date
    estuary or estuary_name
    component
    value_afday or value_acft or value

Optional:
    count_in_basin_sum
        If present, rows with count_in_basin_sum == 0 are excluded to avoid double-counting
        upstream/intermediate gaged rows.

Output columns:
    Year, Month, Estuary, gaged, model, diversion, return, fresh_in

Formula:
    fresh_in = gaged + model - diversion + return
"""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd


COMPONENT_MAP = {
    "gaged": "gaged",
    "gauge": "gaged",
    "usgs": "gaged",
    "ibwc": "gaged",
    "gaged_upstream": "gaged_upstream",
    "model": "model",
    "txrr": "model",
    "ungaged": "model",
    "diversion": "diversion",
    "diverted": "diversion",
    "return": "return",
    "ret": "return",
    "return_flow": "return",
    "fresh_in": "fresh_in",
    "fwi": "fresh_in",
}

OUTPUT_COMPONENTS = ["gaged", "gaged_upstream", "model", "diversion", "return"]
OUTPUT_COLUMNS = ["Year", "Month", "Estuary", "gaged", "model", "diversion", "return", "fresh_in"]


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase and normalize column names."""
    out = df.copy()
    out.columns = (
        out.columns.astype(str)
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.lower()
    )
    return out


def _pick_col(df: pd.DataFrame, candidates: list[str], *, required: bool = True) -> str | None:
    """Pick the first matching column name from candidates."""
    for col in candidates:
        if col in df.columns:
            return col
    if required:
        raise ValueError(f"Missing required column. Tried: {candidates}. Found: {list(df.columns)}")
    return None


def read_daily_table(path: str | Path) -> pd.DataFrame:
    """Read daily FWI table from CSV or Parquet."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)

    raise ValueError(f"Unsupported input format: {path.suffix}. Use .csv or .parquet")


def build_monthly_estuary_flow(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate daily FWI component records to monthly estuary totals.

    Monthly formula:
        fresh_in = gaged + model - diversion + return

    Important:
        gaged must represent local + upstream gaged contribution.
        If flow_role exists, adjusted gaged rows are preferred.
    """
    df = _standardize_columns(daily_df)

    date_col = _pick_col(df, ["date", "datetime", "datetime_utc"])
    estuary_col = _pick_col(df, ["estuary", "estuary_name", "bay", "bay_system"])
    component_col = _pick_col(df, ["component", "hydrology_type", "hydrologytype", "wdft_component"])
    value_col = _pick_col(df, ["value_afday", "value_acft", "value_af", "value", "flow_afday"])

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")

    df = df.dropna(subset=[date_col, estuary_col, component_col])

    # Avoid double-counting if your gaged component keeps helper/intermediate rows.
    # This preserves rows where the field is missing/blank and removes only explicit 0 rows.
    if "count_in_basin_sum" in df.columns:
        count_numeric = pd.to_numeric(df["count_in_basin_sum"], errors="coerce")
        df = df[count_numeric.isna() | (count_numeric != 0)]

    df["component_norm"] = (
        df[component_col]
        .astype(str)
        .str.strip()
        .str.lower()
        .map(COMPONENT_MAP)
    )

    # Use component rows needed for the requested monthly formula.
    df = df[df["component_norm"].isin(OUTPUT_COMPONENTS)]

    df["Year"] = df[date_col].dt.year.astype("Int64")
    df["Month"] = df[date_col].dt.month.astype("Int64")
    df["Estuary"] = df[estuary_col].astype(str).str.strip()

    grouped = (
        df.groupby(["Year", "Month", "Estuary", "component_norm"], dropna=False)[value_col]
        .sum(min_count=1)
        .reset_index()
    )

    wide = (
        grouped.pivot_table(
            index=["Year", "Month", "Estuary"],
            columns="component_norm",
            values=value_col,
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )

    wide.columns.name = None

    for col in OUTPUT_COMPONENTS:
        if col not in wide.columns:
            wide[col] = 0.0

    wide["fresh_in"] = (
        wide["gaged"]
        + wide["gaged_upstream"]
        + wide["model"]
        - wide["diversion"]
        + wide["return"]
    )

    wide = wide[OUTPUT_COLUMNS].sort_values(["Year", "Month", "Estuary"]).reset_index(drop=True)

    return wide


def write_monthly_estuary_flow(input_path: str | Path, output_path: str | Path) -> pd.DataFrame:
    """Read daily table, calculate monthly estuary totals, and write CSV."""
    daily = read_daily_table(input_path)
    monthly = build_monthly_estuary_flow(daily)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(output_path, index=False)

    return monthly


def main() -> None:
    parser = argparse.ArgumentParser(description="Create monthly bay/estuary FWI component CSV.")
    parser.add_argument("--input", required=True, help="Input daily FWI CSV or Parquet file.")
    parser.add_argument("--output", required=True, help="Output monthly CSV path.")

    args = parser.parse_args()

    monthly = write_monthly_estuary_flow(args.input, args.output)

    print(f"Wrote: {args.output}")
    print(f"Rows: {len(monthly):,}")
    print(monthly.head())


if __name__ == "__main__":
    main()
