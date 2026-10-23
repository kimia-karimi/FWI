# tx_fwi/components/gaged_upstream.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.components.qaqc_mixin import QAComponentMixin
from tx_fwi.qaqc.component_checks import (
    validate_canonical_component,
)
from tx_fwi.qaqc.models import CheckResult
from tx_fwi.sources.usgs import (
    fetch_usgs_daily_afday,
)


REQUIRED_OUT_COLS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "source",
    "value_afday",
    "flow_role",
    "count_in_basin_sum",
    "data_as_of",
    "note",
]


INTERNAL_QA_COLS = [
    "_source_gage",
    "_source_component_id",
    "_source_expected_start",
    "_source_expected_end",
]


class UpstreamGagedComponent(
    QAComponentMixin,
):
    """
    Build estuary-level upstream gaged inflows.

    Upstream gages are stored as system components. They are not
    assigned as additive local flow to every downstream watershed.
    """

    name = "gaged_upstream"

    def __init__(
        self,
        ctx: RunContext,
    ):
        self.ctx = ctx
        self._source_failures: list[dict] = []

    def fetch(
        self,
        source: str,
        gage_id: str,
        start,
        end,
        *,
        special=None,
        meta: dict | None = None,
    ) -> pd.Series:
        """
        Retrieve one upstream-gage time series.
        """
        logger = self.ctx.component_logger(
            self.name
        )

        source_normalized = (
            str(source)
            .strip()
            .lower()
        )

        logger.debug(
            "Fetching source=%s gage=%s "
            "for %s through %s",
            source_normalized,
            gage_id,
            pd.Timestamp(start).date(),
            pd.Timestamp(end).date(),
        )

        if source_normalized == "usgs":
            return fetch_usgs_daily_afday(
                str(gage_id),
                start,
                end,
            )

        if source_normalized == "lnra":
            raise NotImplementedError(
                "LNRA upstream retrieval is not "
                "implemented in this component."
            )

        if source_normalized == "ibwc":
            raise NotImplementedError(
                "IBWC upstream retrieval is not "
                "implemented in this component."
            )

        raise NotImplementedError(
            "Unsupported upstream source: "
            f"{source_normalized}"
        )

    def build(
        self,
        start,
        end,
    ) -> pd.DataFrame:
        logger = self.ctx.component_logger(
            self.name
        )

        self._source_failures = []

        upstream_config = (
            self.ctx.registry
            .load_upstream_gages()
        )

        rows: list[pd.DataFrame] = []

        logger.info(
            "Processing %s upstream estuary definitions",
            len(upstream_config),
        )

        for estuary, components in (
            upstream_config.items()
        ):
            logger.debug(
                "Processing estuary=%s "
                "component_count=%s",
                estuary,
                len(components),
            )

            for definition in components:
                component_id = str(
                    definition["component_id"]
                )

                source = str(
                    definition["source"]
                )

                gage_id = str(
                    definition["gage_id"]
                )

                label = definition.get(
                    "label"
                )

                special = definition.get(
                    "special"
                )

                try:
                    series = self.fetch(
                        source=source,
                        gage_id=gage_id,
                        start=start,
                        end=end,
                        special=special,
                        meta=definition,
                    )

                except Exception as exc:
                    logger.error(
                        "Source fetch failed for "
                        "component_id=%s gage=%s: %s",
                        component_id,
                        gage_id,
                        exc,
                    )

                    self._source_failures.append(
                        {
                            "estuary": estuary,
                            "component_id": (
                                component_id
                            ),
                            "gage_id": gage_id,
                            "source": source,
                            "reason": str(exc),
                            "exception_type": (
                                type(exc).__name__
                            ),
                        }
                    )

                    continue

                if (
                    series is None
                    or series.empty
                ):
                    logger.warning(
                        "No source data returned for "
                        "component_id=%s gage=%s",
                        component_id,
                        gage_id,
                    )

                    self._source_failures.append(
                        {
                            "estuary": estuary,
                            "component_id": (
                                component_id
                            ),
                            "gage_id": gage_id,
                            "source": source,
                            "reason": (
                                "no_data_returned"
                            ),
                            "exception_type": None,
                        }
                    )

                    continue

                frame = series.reset_index()

                if len(frame.columns) != 2:
                    raise ValueError(
                        "Upstream source series must "
                        "reset to exactly two columns. "
                        f"gage_id={gage_id}; "
                        f"columns={frame.columns.tolist()}"
                    )

                frame.columns = [
                    "date",
                    "value_afday",
                ]

                frame["date"] = pd.to_datetime(
                    frame["date"],
                    errors="coerce",
                ).dt.normalize()

                frame["value_afday"] = (
                    pd.to_numeric(
                        frame["value_afday"],
                        errors="coerce",
                    )
                )

                frame = frame.dropna(
                    subset=[
                        "date",
                        "value_afday",
                    ]
                ).copy()

                frame["id"] = component_id
                frame["id_type"] = "system"
                frame["estuary"] = estuary
                frame["component"] = self.name

                frame["source"] = (
                    source.strip().lower()
                )

                frame[
                    "flow_role"
                ] = "system_counted"

                frame[
                    "count_in_basin_sum"
                ] = 1

                frame["data_as_of"] = (
                    pd.Timestamp(end)
                    .normalize()
                )

                frame["note"] = (
                    label
                    if label
                    else special
                )

                # Internal QA/QC fields. The mixin removes
                # underscore-prefixed fields before append.
                frame["_source_gage"] = gage_id

                frame[
                    "_source_component_id"
                ] = component_id

                frame[
                    "_source_expected_start"
                ] = (
                    pd.Timestamp(start)
                    .normalize()
                )

                frame[
                    "_source_expected_end"
                ] = (
                    pd.Timestamp(end)
                    .normalize()
                )

                rows.append(frame)

                logger.debug(
                    "Retrieved component_id=%s "
                    "gage=%s rows=%s "
                    "first_date=%s last_date=%s",
                    component_id,
                    gage_id,
                    len(frame),
                    (
                        frame["date"].min()
                        if not frame.empty
                        else None
                    ),
                    (
                        frame["date"].max()
                        if not frame.empty
                        else None
                    ),
                )

        if not rows:
            return pd.DataFrame(
                columns=(
                    REQUIRED_OUT_COLS
                    + INTERNAL_QA_COLS
                )
            )

        output = pd.concat(
            rows,
            ignore_index=True,
            sort=False,
        )

        logger.info(
            "Built %s upstream rows for "
            "%s system components",
            len(output),
            output["id"].nunique(
                dropna=True
            ),
        )

        return output[
            REQUIRED_OUT_COLS
            + INTERNAL_QA_COLS
        ]

    def validate(
        self,
        df: pd.DataFrame,
        *,
        result,
        start,
        end,
    ) -> None:
        manager = self.ctx.run_manager

        validate_canonical_component(
            df,
            component_result=result,
            run_manager=manager,
            component=self.name,
            start=start,
            end=end,
            coverage_group_cols=[
                "id",
                "_source_gage",
            ],
            duplicate_key_cols=[
                "date",
                "id",
                "component",
                "source",
                "flow_role",
            ],
        )

        source_failures = pd.DataFrame(
            self._source_failures
        )

        source_failure_path = (
            manager.write_exception_csv(
                component=self.name,
                name="source_failures",
                df=source_failures,
            )
        )

        result.add_check(
            CheckResult(
                check_id=(
                    "upstream_source_retrieval"
                ),
                status=(
                    "PASS"
                    if source_failures.empty
                    else "FAIL"
                ),
                message=(
                    "All upstream sources "
                    "returned data."
                    if source_failures.empty
                    else (
                        f"{len(source_failures)} "
                        "upstream sources failed "
                        "or returned no data."
                    )
                ),
                expected={
                    "source_failures": 0,
                },
                actual={
                    "source_failures": len(
                        source_failures
                    ),
                },
                blocking=True,
                details=(
                    {
                        "exception_file": (
                            source_failure_path
                        )
                    }
                    if source_failure_path
                    else {}
                ),
            )
        )

        if df.empty:
            return

        upstream_config = (
            self.ctx.registry
            .load_upstream_gages()
        )

        expected_components: set[str] = set()
        expected_gages: set[str] = set()

        for definitions in (
            upstream_config.values()
        ):
            for definition in definitions:
                expected_components.add(
                    str(
                        definition[
                            "component_id"
                        ]
                    )
                )

                expected_gages.add(
                    str(
                        definition[
                            "gage_id"
                        ]
                    )
                )

        actual_components = set(
            df["id"]
            .dropna()
            .astype(str)
        )

        actual_gages = set(
            df["_source_gage"]
            .dropna()
            .astype(str)
        )

        missing_components = sorted(
            expected_components
            - actual_components
        )

        unexpected_components = sorted(
            actual_components
            - expected_components
        )

        missing_gages = sorted(
            expected_gages
            - actual_gages
        )

        component_set_valid = (
            not missing_components
            and not unexpected_components
            and not missing_gages
        )

        result.add_check(
            CheckResult(
                check_id=(
                    "expected_upstream_components"
                ),
                status=(
                    "PASS"
                    if component_set_valid
                    else "FAIL"
                ),
                message=(
                    "Upstream output matches "
                    "the registry definitions."
                    if component_set_valid
                    else (
                        "Upstream output does not "
                        "match the registry "
                        "definitions."
                    )
                ),
                expected={
                    "component_count": len(
                        expected_components
                    ),
                    "gage_count": len(
                        expected_gages
                    ),
                },
                actual={
                    "component_count": len(
                        actual_components
                    ),
                    "gage_count": len(
                        actual_gages
                    ),
                    "missing_components": (
                        missing_components
                    ),
                    "unexpected_components": (
                        unexpected_components
                    ),
                    "missing_gages": (
                        missing_gages
                    ),
                },
                blocking=True,
            )
        )

        invalid_id_type = df.loc[
            ~df["id_type"].eq("system")
        ].copy()

        invalid_id_path = (
            manager.write_exception_csv(
                component=self.name,
                name="invalid_id_type",
                df=invalid_id_type,
            )
        )

        result.add_check(
            CheckResult(
                check_id="upstream_id_type",
                status=(
                    "PASS"
                    if invalid_id_type.empty
                    else "FAIL"
                ),
                message=(
                    "All upstream records use "
                    "id_type=system."
                    if invalid_id_type.empty
                    else (
                        "Upstream output contains "
                        "invalid id_type values."
                    )
                ),
                expected={
                    "id_type": "system",
                },
                actual={
                    "invalid_rows": len(
                        invalid_id_type
                    ),
                },
                blocking=True,
                details=(
                    {
                        "exception_file": (
                            invalid_id_path
                        )
                    }
                    if invalid_id_path
                    else {}
                ),
            )
        )

        invalid_flow_role = df.loc[
            ~df["flow_role"].eq(
                "system_counted"
            )
        ].copy()

        invalid_role_path = (
            manager.write_exception_csv(
                component=self.name,
                name="invalid_flow_role",
                df=invalid_flow_role,
            )
        )

        result.add_check(
            CheckResult(
                check_id=(
                    "upstream_flow_role"
                ),
                status=(
                    "PASS"
                    if invalid_flow_role.empty
                    else "FAIL"
                ),
                message=(
                    "All upstream records use "
                    "flow_role=system_counted."
                    if invalid_flow_role.empty
                    else (
                        "Upstream output contains "
                        "invalid flow roles."
                    )
                ),
                expected={
                    "flow_role": (
                        "system_counted"
                    ),
                },
                actual={
                    "invalid_rows": len(
                        invalid_flow_role
                    ),
                },
                blocking=True,
                details=(
                    {
                        "exception_file": (
                            invalid_role_path
                        )
                    }
                    if invalid_role_path
                    else {}
                ),
            )
        )

        expected_dates = pd.date_range(
            pd.Timestamp(start).normalize(),
            pd.Timestamp(end).normalize(),
            freq="D",
        )

        coverage_rows: list[dict] = []

        for (
            source_gage,
            output_id,
        ), group in df.groupby(
            [
                "_source_gage",
                "id",
            ],
            dropna=False,
        ):
            actual_dates = pd.DatetimeIndex(
                pd.to_datetime(
                    group["date"],
                    errors="coerce",
                )
                .dropna()
                .dt.normalize()
                .unique()
            )

            missing_dates = (
                expected_dates.difference(
                    actual_dates
                )
            )

            values = pd.to_numeric(
                group["value_afday"],
                errors="coerce",
            )

            coverage_rows.append(
                {
                    "source_gage": (
                        source_gage
                    ),
                    "id": output_id,
                    "expected_start": (
                        expected_dates.min()
                    ),
                    "expected_end": (
                        expected_dates.max()
                    ),
                    "actual_start": (
                        actual_dates.min()
                        if len(actual_dates)
                        else pd.NaT
                    ),
                    "actual_end": (
                        actual_dates.max()
                        if len(actual_dates)
                        else pd.NaT
                    ),
                    "expected_dates": len(
                        expected_dates
                    ),
                    "actual_dates": len(
                        actual_dates
                    ),
                    "missing_dates": len(
                        missing_dates
                    ),
                    "zero_value_dates": int(
                        values.eq(0).sum()
                    ),
                    "complete": (
                        len(missing_dates) == 0
                    ),
                }
            )

        coverage = pd.DataFrame(
            coverage_rows
        )

        incomplete = coverage.loc[
            ~coverage["complete"]
        ].copy()

        coverage_path = (
            manager.write_exception_csv(
                component=self.name,
                name=(
                    "incomplete_gage_coverage"
                ),
                df=incomplete,
            )
        )

        result.add_check(
            CheckResult(
                check_id=(
                    "upstream_gage_coverage"
                ),
                status=(
                    "PASS"
                    if incomplete.empty
                    else "FAIL"
                ),
                message=(
                    "All upstream gages cover "
                    "the requested period."
                    if incomplete.empty
                    else (
                        f"{len(incomplete)} "
                        "upstream gages have "
                        "incomplete date coverage."
                    )
                ),
                expected={
                    "incomplete_gages": 0,
                },
                actual={
                    "incomplete_gages": len(
                        incomplete
                    ),
                    "gages_checked": len(
                        coverage
                    ),
                },
                blocking=True,
                details=(
                    {
                        "exception_file": (
                            coverage_path
                        )
                    }
                    if coverage_path
                    else {}
                ),
            )
        )

        result.metrics.update(
            {
                "expected_components": len(
                    expected_components
                ),
                "actual_components": len(
                    actual_components
                ),
                "expected_gages": len(
                    expected_gages
                ),
                "actual_gages": len(
                    actual_gages
                ),
                "complete_gages": int(
                    coverage[
                        "complete"
                    ].sum()
                ),
                "incomplete_gages": int(
                    (
                        ~coverage[
                            "complete"
                        ]
                    ).sum()
                ),
                "zero_value_dates": int(
                    coverage[
                        "zero_value_dates"
                    ].sum()
                ),
            }
        )
