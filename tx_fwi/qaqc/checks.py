# tx_fwi/qaqc/checks.py
from __future__ import annotations

import pandas as pd

from tx_fwi.qaqc.models import CheckResult

def check_date_coverage(
    df: pd.DataFrame,
    *,
    start,
    end,
    date_col: str = "date",
    group_cols: list[str] | None = None,
    allow_missing_dates: int = 0,
    check_id: str = "date_coverage",
) -> tuple[CheckResult, pd.DataFrame]:
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()

    expected_dates = pd.date_range(
        start_ts,
        end_ts,
        freq="D",
    )

    work = df.copy()
    work[date_col] = pd.to_datetime(
        work[date_col],
        errors="coerce",
    ).dt.normalize()

    group_cols = group_cols or []

    if not group_cols:
        actual_dates = pd.DatetimeIndex(
            work[date_col].dropna().unique()
        )

        missing_dates = expected_dates.difference(
            actual_dates
        )

        details = pd.DataFrame(
            {
                "missing_date": missing_dates,
            }
        )

        missing_count = len(missing_dates)

        return (
            CheckResult(
                check_id=check_id,
                status=(
                    "PASS"
                    if missing_count
                    <= allow_missing_dates
                    else "FAIL"
                ),
                message=(
                    "Date coverage is complete"
                    if missing_count == 0
                    else (
                        f"{missing_count} expected "
                        "dates are missing"
                    )
                ),
                expected={
                    "start": str(start_ts.date()),
                    "end": str(end_ts.date()),
                    "dates": len(expected_dates),
                },
                actual={
                    "start": (
                        str(work[date_col].min().date())
                        if work[date_col].notna().any()
                        else None
                    ),
                    "end": (
                        str(work[date_col].max().date())
                        if work[date_col].notna().any()
                        else None
                    ),
                    "dates": work[
                        date_col
                    ].nunique(),
                    "missing_dates": missing_count,
                },
            ),
            details,
        )

    failures = []

    for group_key, group in work.groupby(
        group_cols,
        dropna=False,
    ):
        actual_dates = pd.DatetimeIndex(
            group[date_col]
            .dropna()
            .unique()
        )

        missing = expected_dates.difference(
            actual_dates
        )

        if len(missing) <= allow_missing_dates:
            continue

        if not isinstance(group_key, tuple):
            group_key = (group_key,)

        identity = dict(
            zip(group_cols, group_key)
        )

        failures.append(
            {
                **identity,
                "actual_start": group[
                    date_col
                ].min(),
                "actual_end": group[
                    date_col
                ].max(),
                "actual_dates": group[
                    date_col
                ].nunique(),
                "expected_dates": len(
                    expected_dates
                ),
                "missing_dates": len(missing),
                "first_missing_date": (
                    missing.min()
                    if len(missing)
                    else pd.NaT
                ),
                "last_missing_date": (
                    missing.max()
                    if len(missing)
                    else pd.NaT
                ),
            }
        )

    failure_df = pd.DataFrame(failures)

    return (
        CheckResult(
            check_id=check_id,
            status=(
                "PASS"
                if failure_df.empty
                else "FAIL"
            ),
            message=(
                "All groups cover the expected period"
                if failure_df.empty
                else (
                    f"{len(failure_df)} groups have "
                    "incomplete date coverage"
                )
            ),
            expected={
                "start": str(start_ts.date()),
                "end": str(end_ts.date()),
                "dates_per_group": len(
                    expected_dates
                ),
            },
            actual={
                "groups_checked": (
                    work[group_cols]
                    .drop_duplicates()
                    .shape[0]
                ),
                "groups_incomplete": len(
                    failure_df
                ),
            },
        ),
        failure_df,
    )
	
def check_duplicate_keys(
    df: pd.DataFrame,
    *,
    key_cols: list[str],
    check_id: str,
) -> tuple[CheckResult, pd.DataFrame]:
    duplicates = df.loc[
        df.duplicated(
            subset=key_cols,
            keep=False,
        )
    ].sort_values(key_cols)

    return (
        CheckResult(
            check_id=check_id,
            status=(
                "PASS"
                if duplicates.empty
                else "FAIL"
            ),
            message=(
                "No duplicate output keys"
                if duplicates.empty
                else (
                    f"{len(duplicates)} rows "
                    "participate in duplicate keys"
                )
            ),
            expected={
                "duplicate_rows": 0,
                "key": key_cols,
            },
            actual={
                "duplicate_rows": len(
                    duplicates
                ),
                "duplicate_keys": (
                    duplicates[key_cols]
                    .drop_duplicates()
                    .shape[0]
                ),
            },
        ),
        duplicates,
    )
def check_required_fields(
    df: pd.DataFrame,
    *,
    required_cols: list[str],
    check_id: str,
) -> CheckResult:
    missing_columns = [
        col
        for col in required_cols
        if col not in df.columns
    ]

    if missing_columns:
        return CheckResult(
            check_id=check_id,
            status="FAIL",
            message="Required columns are absent",
            expected=required_cols,
            actual={
                "missing_columns": missing_columns,
            },
        )

    null_counts = (
        df[required_cols]
        .isna()
        .sum()
    )

    null_counts = null_counts.loc[
        null_counts.gt(0)
    ].to_dict()

    return CheckResult(
        check_id=check_id,
        status=(
            "PASS"
            if not null_counts
            else "FAIL"
        ),
        message=(
            "All required fields are populated"
            if not null_counts
            else "Required fields contain null values"
        ),
        expected={"null_values": 0},
        actual={"null_counts": null_counts},
    )
	
def check_flow_values(
    df,
    *,
    value_col="value_afday",
    allow_negative=False,
):
    values = pd.to_numeric(
        df[value_col],
        errors="coerce",
    )

    invalid_numeric = values.isna()
    infinite = ~values.replace(
        [float("inf"), float("-inf")],
        pd.NA,
    ).notna()

    negative = (
        values.lt(0)
        if not allow_negative
        else pd.Series(False, index=df.index)
    )

    bad = df.loc[
        invalid_numeric
        | infinite
        | negative
    ].copy()

    return CheckResult(
        check_id="flow_value_validity",
        status="PASS" if bad.empty else "FAIL",
        message=(
            "All flow values are valid"
            if bad.empty
            else f"{len(bad)} invalid flow rows"
        ),
        expected={
            "numeric": True,
            "finite": True,
            "negative_allowed": allow_negative,
        },
        actual={
            "invalid_rows": len(bad),
        },
    ), bad
def check_volume_conservation(
    *,
    input_total,
    assigned_total,
    unresolved_total=0.0,
    tolerance=1e-6,
    check_id="volume_conservation",
):
    accounted = (
        assigned_total
        + unresolved_total
    )

    difference = (
        float(input_total)
        - float(accounted)
    )

    allowed = max(
        tolerance,
        abs(float(input_total)) * 1e-10,
    )

    return CheckResult(
        check_id=check_id,
        status=(
            "PASS"
            if abs(difference) <= allowed
            else "FAIL"
        ),
        message=(
            "Input volume is conserved"
            if abs(difference) <= allowed
            else "Input and output volumes differ"
        ),
        expected={
            "difference": 0.0,
            "tolerance": allowed,
        },
        actual={
            "input_total": float(input_total),
            "assigned_total": float(
                assigned_total
            ),
            "unresolved_total": float(
                unresolved_total
            ),
            "accounted_total": accounted,
            "difference": difference,
        },
    )
