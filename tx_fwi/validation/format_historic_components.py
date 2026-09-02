# format_historic_components.py
"""Map historic daily hydrology values to monthly estuary component totals.

Required inputs
---------------
Daily values:
    id, hydrologyseries_id, value, date, hydrologytype_id
Hydrology series lookup:
    id, subwatershed_id, hydrologytype_id, hydrologystatus_id,
    parameter_id, watershed_id, geography_id, ...
Subwatershed lookup:
    id, watershed_code, geography_id, watershed_id, ...
Watershed lookup:
    id, watershed_code, name, geography_id, ...
Hydrology type lookup:
    id, name, description

Output detail is ready for the comparison scripts:
    Year, Month, Estuary, component, value_af

Mapping path
------------
daily.hydrologyseries_id -> series.id
series.subwatershed_id -> subwatershed.id
series.watershed_id OR subwatershed.watershed_id -> watershed.id
series/daily.hydrologytype_id -> hydrologytype.id -> component

Notes
-----
* gage/gauge becomes gaged.
* model and txrr become model.
* diversion and return remain positive component volumes. Their signs belong in
  the final freshwater-inflow equation, not component-to-component validation.
* fresh_in is excluded because this script prepares component comparisons.
* Values are summed across daily records to monthly acre-feet. This assumes the
  historic ``value`` field is a daily volume in acre-feet, not an average rate.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

COMPARISON_COMPONENTS = ("gaged", "model", "diversion", "return")
TYPE_NAME_MAP = {
    "gage": "gaged",
    "gauge": "gaged",
    "gaged": "gaged",
    "usgs_gauge_flow": "gaged",
    "model": "model",
    "computed_flow": "model",
    "txrr": "model",
    "rainfall_runoff": "model",
    "ungaged": "model",
    "diversion": "diversion",
    "diverted_flow": "diversion",
    "return": "return",
    "return_flow": "return",
}
FALLBACK_TYPE_ID_MAP = {
    1: "gaged",
    2: "model",
    3: "diversion",
    4: "return",
}


def _normalize_label(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [_normalize_label(c) for c in out.columns]
    return out


def _read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path, low_memory=False)
    raise ValueError(f"Unsupported file type {suffix!r}: {path}")


def _require_columns(df: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = sorted(set(required) - set(df.columns))
    if missing:
        raise ValueError(f"{label} is missing columns {missing}. Found: {list(df.columns)}")


def _to_nullable_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _parse_id_filter(text: str | None) -> set[int] | None:
    if text is None or not text.strip():
        return None
    return {int(item.strip()) for item in text.split(",") if item.strip()}


def _assert_unique_id(df: pd.DataFrame, label: str) -> None:
    duplicate = df["id"].duplicated(keep=False)
    if duplicate.any():
        examples = df.loc[duplicate, "id"].head(10).tolist()
        raise ValueError(f"{label}.id is not unique. Examples: {examples}")


def _build_type_lookup(types: pd.DataFrame) -> pd.DataFrame:
    _require_columns(types, ["id", "name"], "hydrology type lookup")
    out = types[["id", "name"]].copy()
    out["id"] = _to_nullable_int(out["id"])
    out["type_name"] = out["name"].map(_normalize_label)
    out["component"] = out["type_name"].map(TYPE_NAME_MAP)
    out["component"] = out["component"].fillna(out["id"].map(FALLBACK_TYPE_ID_MAP))
    _assert_unique_id(out, "hydrology type lookup")
    return out[["id", "type_name", "component"]].rename(columns={"id": "type_lookup_id"})


def prepare_historic_monthly_components(
    daily_path: str | Path,
    series_path: str | Path,
    subwatershed_path: str | Path,
    watershed_path: str | Path,
    hydrologytype_path: str | Path,
    *,
    status_ids: set[int] | None = None,
    parameter_ids: set[int] | None = None,
    active_only: bool = False,
    strict_type_match: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return monthly detail, coverage, totals, and diagnostics."""
    daily = _standardize_columns(_read_table(daily_path))
    series = _standardize_columns(_read_table(series_path))
    sub = _standardize_columns(_read_table(subwatershed_path))
    watershed = _standardize_columns(_read_table(watershed_path))
    hydrotype = _standardize_columns(_read_table(hydrologytype_path))

    _require_columns(
        daily,
        ["id", "hydrologyseries_id", "value", "date", "hydrologytype_id"],
        "daily values",
    )
    _require_columns(
        series,
        ["id", "subwatershed_id", "hydrologytype_id", "hydrologystatus_id",
         "parameter_id", "watershed_id", "geography_id"],
        "hydrology series lookup",
    )
    _require_columns(sub, ["id", "watershed_id"], "subwatershed lookup")
    _require_columns(watershed, ["id", "watershed_code", "name"], "watershed lookup")

    _assert_unique_id(series, "hydrology series lookup")
    _assert_unique_id(sub, "subwatershed lookup")
    _assert_unique_id(watershed, "watershed lookup")

    # Normalize join keys and values.
    daily["hydrologyseries_id"] = _to_nullable_int(daily["hydrologyseries_id"])
    daily["daily_hydrologytype_id"] = _to_nullable_int(daily["hydrologytype_id"])
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily["value_af"] = pd.to_numeric(daily["value"], errors="coerce")

    for col in ["id", "subwatershed_id", "hydrologytype_id", "hydrologystatus_id",
                "parameter_id", "watershed_id", "geography_id"]:
        series[col] = _to_nullable_int(series[col])
    sub["id"] = _to_nullable_int(sub["id"])
    sub["watershed_id"] = _to_nullable_int(sub["watershed_id"])
    watershed["id"] = _to_nullable_int(watershed["id"])

    if status_ids is not None:
        series = series[series["hydrologystatus_id"].isin(status_ids)].copy()
    if parameter_ids is not None:
        series = series[series["parameter_id"].isin(parameter_ids)].copy()
    if active_only:
        if "is_active" not in series.columns:
            raise ValueError("--active-only was requested, but series has no is_active column")
        active = series["is_active"].astype(str).str.strip().str.lower().isin(
            {"1", "true", "t", "yes", "y"}
        )
        series = series[active].copy()

    series_cols = [
        "id", "subwatershed_id", "hydrologytype_id", "hydrologystatus_id",
        "parameter_id", "watershed_id", "geography_id"
    ]
    optional_series_cols = [c for c in ["name", "date_starting", "date_ending", "is_active"]
                            if c in series.columns]
    series_x = series[series_cols + optional_series_cols].rename(
        columns={
            "id": "series_id",
            "hydrologytype_id": "series_hydrologytype_id",
            "watershed_id": "series_watershed_id",
            "geography_id": "series_geography_id",
            "name": "series_name",
        }
    )

    mapped = daily.merge(
        series_x,
        left_on="hydrologyseries_id",
        right_on="series_id",
        how="left",
        validate="many_to_one",
        indicator="series_join",
    )

    # Add the subwatershed-to-estuary lookup. Series-level watershed_id is used
    # for estuary summary series; otherwise the subwatershed watershed_id is used.
    sub_x = sub[["id", "watershed_id"]].rename(
        columns={"id": "subwatershed_lookup_id", "watershed_id": "sub_watershed_id"}
    )
    mapped = mapped.merge(
        sub_x,
        left_on="subwatershed_id",
        right_on="subwatershed_lookup_id",
        how="left",
        validate="many_to_one",
        indicator="subwatershed_join",
    )
    mapped["resolved_watershed_id"] = mapped["series_watershed_id"].fillna(
        mapped["sub_watershed_id"]
    ).astype("Int64")

    watershed_x = watershed[["id", "watershed_code", "name"]].rename(
        columns={
            "id": "watershed_lookup_id",
            "watershed_code": "estuary_code",
            "name": "Estuary",
        }
    )
    mapped = mapped.merge(
        watershed_x,
        left_on="resolved_watershed_id",
        right_on="watershed_lookup_id",
        how="left",
        validate="many_to_one",
        indicator="watershed_join",
    )

    type_lookup = _build_type_lookup(hydrotype)
    mapped = mapped.merge(
        type_lookup,
        left_on="series_hydrologytype_id",
        right_on="type_lookup_id",
        how="left",
        validate="many_to_one",
        indicator="type_join",
    )

    mapped["type_id_matches"] = (
        mapped["daily_hydrologytype_id"].isna()
        | mapped["series_hydrologytype_id"].isna()
        | mapped["daily_hydrologytype_id"].eq(mapped["series_hydrologytype_id"])
    )
    mismatch = mapped["series_join"].eq("both") & ~mapped["type_id_matches"]
    if strict_type_match and mismatch.any():
        examples = mapped.loc[
            mismatch,
            ["id", "hydrologyseries_id", "daily_hydrologytype_id", "series_hydrologytype_id"],
        ].head(10).to_dict("records")
        raise ValueError(
            "Daily hydrologytype_id disagrees with the hydrology series lookup. "
            f"Examples: {examples}. Use --allow-type-mismatch only after reviewing diagnostics."
        )

    mapped["valid_date"] = mapped["date"].notna()
    mapped["valid_value"] = mapped["value_af"].notna()
    mapped["valid_component"] = mapped["component"].isin(COMPARISON_COMPONENTS)
    mapped["valid_estuary"] = mapped["Estuary"].notna() & mapped["Estuary"].astype(str).str.strip().ne("")
    type_ok = mapped["type_id_matches"] if strict_type_match else pd.Series(True, index=mapped.index)
    mapped["included"] = (
        mapped["series_join"].eq("both")
        & mapped["valid_date"]
        & mapped["valid_value"]
        & mapped["valid_component"]
        & mapped["valid_estuary"]
        & type_ok
    )

    mapped["exclusion_reason"] = "included"
    tests = [
        (~mapped["series_join"].eq("both"), "series_not_found_or_filtered"),
        (~mapped["type_id_matches"], "daily_series_type_mismatch"),
        (~mapped["valid_component"], "component_not_compared"),
        (~mapped["valid_estuary"], "estuary_not_resolved"),
        (~mapped["valid_date"], "invalid_date"),
        (~mapped["valid_value"], "invalid_value"),
    ]
    for condition, reason in tests:
        mapped.loc[(mapped["exclusion_reason"] == "included") & condition, "exclusion_reason"] = reason

    good = mapped[mapped["included"]].copy()
    good["Year"] = good["date"].dt.year.astype("Int64")
    good["Month"] = good["date"].dt.month.astype("Int64")
    good["Estuary"] = good["Estuary"].astype(str).str.strip()

    # Sum daily acre-foot volumes across every subwatershed series in an estuary.
    monthly = (
        good.groupby(["Year", "Month", "Estuary", "component"], as_index=False, dropna=False)
        .agg(
            value_af=("value_af", lambda s: s.sum(min_count=1)),
            daily_rows=("id", "size"),
            series_count=("hydrologyseries_id", "nunique"),
            subwatershed_count=("subwatershed_id", "nunique"),
        )
        .sort_values(["Year", "Month", "Estuary", "component"])
        .reset_index(drop=True)
    )

    month_date = pd.to_datetime(
        dict(year=monthly["Year"], month=monthly["Month"], day=1), errors="coerce"
    )
    coverage_source = monthly.assign(month_date=month_date)
    coverage = (
        coverage_source.groupby(["Estuary", "component"], as_index=False)
        .agg(
            first_month=("month_date", "min"),
            last_month=("month_date", "max"),
            months=("month_date", "nunique"),
            total_daily_rows=("daily_rows", "sum"),
            max_series_in_month=("series_count", "max"),
            max_subwatersheds_in_month=("subwatershed_count", "max"),
        )
        .sort_values(["Estuary", "component"])
    )
    totals = (
        monthly.groupby(["Estuary", "component"], as_index=False)
        .agg(total_af=("value_af", lambda s: s.sum(min_count=1)), months=("Month", "size"))
        .sort_values(["Estuary", "component"])
    )

    diagnostic_columns = [
        "id", "hydrologyseries_id", "daily_hydrologytype_id", "series_id",
        "series_hydrologytype_id", "subwatershed_id", "series_watershed_id",
        "sub_watershed_id", "resolved_watershed_id", "estuary_code", "Estuary",
        "component", "date", "value_af", "type_id_matches", "included",
        "exclusion_reason",
    ]
    diagnostics = mapped[[c for c in diagnostic_columns if c in mapped.columns]].copy()
    return monthly, coverage, totals, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map historic daily FWI records to monthly estuary component totals."
    )
    parser.add_argument("--daily", required=True, help="Historic daily values CSV or Parquet")
    parser.add_argument("--series", required=True, help="hydrologyseriesUpdated.csv")
    parser.add_argument("--subwatershed", required=True, help="subwatershed.csv")
    parser.add_argument("--watershed", required=True, help="watershed.csv")
    parser.add_argument("--hydrologytype", required=True, help="hydrologytype.csv")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--status-ids",
        default="3",
        help="Comma-separated hydrologystatus IDs to keep. Default: 3. Use an empty string for all.",
    )
    parser.add_argument(
        "--parameter-ids",
        default="43",
        help="Comma-separated parameter IDs to keep. Default: 43. Use an empty string for all.",
    )
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--allow-type-mismatch", action="store_true")
    parser.add_argument(
        "--write-row-diagnostics",
        action="store_true",
        help="Write a potentially large row-level mapping diagnostics CSV.",
    )
    args = parser.parse_args()

    monthly, coverage, totals, diagnostics = prepare_historic_monthly_components(
        args.daily,
        args.series,
        args.subwatershed,
        args.watershed,
        args.hydrologytype,
        status_ids=_parse_id_filter(args.status_ids),
        parameter_ids=_parse_id_filter(args.parameter_ids),
        active_only=args.active_only,
        strict_type_match=not args.allow_type_mismatch,
    )

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(output / "historic_monthly_components.csv", index=False)
    coverage.to_csv(output / "historic_component_coverage.csv", index=False)
    totals.to_csv(output / "historic_component_totals.csv", index=False)

    diagnostic_summary = (
        diagnostics.groupby("exclusion_reason", dropna=False)
        .size()
        .rename("rows")
        .reset_index()
        .sort_values("rows", ascending=False)
    )
    diagnostic_summary.to_csv(output / "historic_mapping_summary.csv", index=False)
    if args.write_row_diagnostics:
        diagnostics.to_csv(output / "historic_mapping_diagnostics.csv", index=False)

    print(f"Wrote historic monthly components to: {output}")
    print(f"Monthly component rows: {len(monthly):,}")
    print("Mapping summary:")
    print(diagnostic_summary.to_string(index=False))


if __name__ == "__main__":
    main()
