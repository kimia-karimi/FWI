from __future__ import annotations

import pandas as pd

from tx_fwi.qaqc.checks import (
    check_date_coverage,
    check_duplicate_keys,
    check_numeric_values,
    check_required_fields,
)

CANONICAL_REQUIRED_COLS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "source",
    "value_afday",
    "flow_role",
    "count_in_basin_sum",
    "note",
]

CANONICAL_KEY_COLS = [
    "date",
    "id",
    "id_type",
    "component",
    "source",
    "flow_role",
]


def validate_canonical_component(
    df: pd.DataFrame,
    *,
    component_result,
    run_manager,
    component: str,
    start,
    end,
    coverage_group_cols=None,
    duplicate_key_cols=None,
    allow_negative_values: bool = False,
) -> None:
    required_check = check_required_fields(
        df,
        required_cols=CANONICAL_REQUIRED_COLS,
        check_id="canonical_required_fields",
    )
    component_result.add_check(required_check)
    if required_check.status == "FAIL":
        return

    duplicate_check, duplicates = check_duplicate_keys(
        df,
        key_cols=duplicate_key_cols or CANONICAL_KEY_COLS,
        check_id="canonical_duplicate_keys",
    )
    duplicate_path = run_manager.write_exception_csv(
        component=component,
        name="duplicate_output_keys",
        df=duplicates,
    )
    if duplicate_path:
        duplicate_check.details["exception_file"] = duplicate_path
    component_result.add_check(duplicate_check)

    coverage_check, missing_dates = check_date_coverage(
        df,
        start=start,
        end=end,
        date_col="date",
        group_cols=coverage_group_cols or ["id"],
        check_id="output_date_coverage",
    )
    missing_path = run_manager.write_exception_csv(
        component=component,
        name="missing_output_dates",
        df=missing_dates,
    )
    if missing_path:
        coverage_check.details["exception_file"] = missing_path
    component_result.add_check(coverage_check)

    numeric_check, invalid_values = check_numeric_values(
        df,
        value_col="value_afday",
        allow_negative=allow_negative_values,
        check_id="output_numeric_values",
    )
    invalid_path = run_manager.write_exception_csv(
        component=component,
        name="invalid_output_values",
        df=invalid_values,
    )
    if invalid_path:
        numeric_check.details["exception_file"] = invalid_path
    component_result.add_check(numeric_check)

    if not df.empty:
        dates = pd.to_datetime(df["date"], errors="coerce").dropna()
        component_result.output_rows = len(df)
        component_result.output_ids = int(df["id"].nunique(dropna=True))
        component_result.expected_dates = len(pd.date_range(start, end, freq="D"))
        if not dates.empty:
            component_result.output_start = str(dates.min().date())
            component_result.output_end = str(dates.max().date())
