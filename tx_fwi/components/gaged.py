# tx_fwi/components/gaged.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.components.qaqc_mixin import QAComponentMixin
from tx_fwi.qaqc.component_checks import (
    validate_canonical_component,
)
from tx_fwi.qaqc.models import CheckResult
from tx_fwi.sources.base import (
    registry as source_registry,
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
    "_source_kind",
    "_source_special",
    "_source_expected_start",
    "_source_expected_end",
]


def _normalize_key(value):
    if value is None or pd.isna(value):
        return None

    return str(value).strip().lower()


class LocalGagedComponent(
    QAComponentMixin,
):
    name = "gaged"

    def __init__(
        self,
        ctx: RunContext,
    ):
        self.ctx = ctx
        self._source_failures: list[dict] = []

    def fetch(
        self,
        source,
        gage_id,
        start,
        end,
        *,
        special=None,
        meta=None,
    ):
        """
        Retrieve one local-gage time series through the source registry.
        """
        logger = self.ctx.component_logger(
            self.name
        )

        source_normalized = (
            _normalize_key(source)
        )

        special_normalized = (
            _normalize_key(special)
        )

        try:
            handler = source_registry.get(
                source_normalized,
                special_normalized,
            )
        except KeyError as exc:
            raise NotImplementedError(
                "No handler is registered for "
                f"source={source_normalized}, "
                f"special={special_normalized}."
            ) from exc

        logger.debug(
            "Fetching source=%s special=%s "
            "gage=%s for %s through %s",
            source_normalized,
            special_normalized,
            gage_id,
            pd.Timestamp(start).date(),
            pd.Timestamp(end).date(),
        )

        return handler(
            start=start,
            end=end,
            site_id=(
                str(gage_id)
                if gage_id is not None
                and not pd.isna(gage_id)
                else None
            ),
            meta=meta,
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

        watersheds = (
            self.ctx.registry
            .load_watersheds()
            .copy()
        )

        required_registry_cols = [
            "WS_ID",
            "Estuary",
            "HAS_GAGED",
            "SPECIAL",
            "G_SOURCE",
            "GAGE_ID",
        ]

        missing_registry_cols = [
            col
            for col in required_registry_cols
            if col not in watersheds.columns
        ]

        if missing_registry_cols:
            raise KeyError(
                "Watershed registry is missing "
                "local-gage columns: "
                + ", ".join(
                    missing_registry_cols
                )
            )

        active = watersheds.loc[
            watersheds["HAS_GAGED"].eq(1)
            | watersheds["SPECIAL"].notna()
        ].copy()

        special_groups: dict[str, str] = {}

        for special_name, group in (
            watersheds
            .dropna(subset=["SPECIAL"])
            .groupby("SPECIAL")
        ):
            primary = group.loc[
                group["HAS_GAGED"].eq(1)
            ]

            if len(primary) != 1:
                raise ValueError(
                    f"{special_name} must have "
                    "exactly one HAS_GAGED=1 "
                    "watershed."
                )

            special_groups[
                str(special_name)
            ] = str(
                primary.iloc[0]["WS_ID"]
            )

        rows: list[pd.DataFrame] = []

        logger.info(
            "Processing %s local-gage "
            "registry records",
            len(active),
        )

        for _, registry_row in (
            active.iterrows()
        ):
            ws_id = str(
                registry_row["WS_ID"]
            )

            estuary = registry_row.get(
                "Estuary"
            )

            source = registry_row.get(
                "G_SOURCE"
            )

            gage_id = registry_row.get(
                "GAGE_ID"
            )

            special = registry_row.get(
                "SPECIAL"
            )

            # Rows without a configured source cannot be
            # retrieved. Record them explicitly when they
            # are otherwise active.
            if pd.isna(source):
                self._source_failures.append(
                    {
                        "ws_id": ws_id,
                        "estuary": estuary,
                        "gage_id": (
                            None
                            if pd.isna(gage_id)
                            else str(gage_id)
                        ),
                        "source": None,
                        "special": (
                            None
                            if pd.isna(special)
                            else str(special)
                        ),
                        "reason": (
                            "missing_source_configuration"
                        ),
                        "exception_type": None,
                    }
                )

                continue

            if (
                pd.isna(gage_id)
                and pd.isna(special)
            ):
                self._source_failures.append(
                    {
                        "ws_id": ws_id,
                        "estuary": estuary,
                        "gage_id": None,
                        "source": str(source),
                        "special": None,
                        "reason": (
                            "missing_gage_id"
                        ),
                        "exception_type": None,
                    }
                )

                continue

            try:
                series = self.fetch(
                    source=source,
                    gage_id=gage_id,
                    start=start,
                    end=end,
                    special=special,
                    meta=registry_row.to_dict(),
                )

            except Exception as exc:
                logger.error(
                    "Source fetch failed for "
                    "ws_id=%s gage=%s: %s",
                    ws_id,
                    gage_id,
                    exc,
                )

                self._source_failures.append(
                    {
                        "ws_id": ws_id,
                        "estuary": estuary,
                        "gage_id": (
                            None
                            if pd.isna(gage_id)
                            else str(gage_id)
                        ),
                        "source": str(source),
                        "special": (
                            None
                            if pd.isna(special)
                            else str(special)
                        ),
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
                    "ws_id=%s gage=%s",
                    ws_id,
                    gage_id,
                )

                self._source_failures.append(
                    {
                        "ws_id": ws_id,
                        "estuary": estuary,
                        "gage_id": (
                            None
                            if pd.isna(gage_id)
                            else str(gage_id)
                        ),
                        "source": str(source),
                        "special": (
                            None
                            if pd.isna(special)
                            else str(special)
                        ),
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
                    "Local source series must "
                    "reset to exactly two columns. "
                    f"ws_id={ws_id}; "
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

            frame["id"] = ws_id
            frame["id_type"] = "watershed"
            frame["estuary"] = estuary
            frame["component"] = self.name

            frame["source"] = (
                str(source)
                .strip()
                .lower()
            )

            frame["flow_role"] = (
                "adjusted"
                if pd.notna(special)
                else "direct"
            )

            if pd.notna(special):
                primary_ws = special_groups.get(
                    str(special)
                )

                frame[
                    "count_in_basin_sum"
                ] = int(
                    ws_id == primary_ws
                )
            else:
                frame[
                    "count_in_basin_sum"
                ] = 1

            frame["data_as_of"] = (
                pd.Timestamp(end)
                .normalize()
            )

            frame["note"] = (
                str(special)
                if pd.notna(special)
                else None
            )

            frame["_source_gage"] = (
                str(gage_id)
                if pd.notna(gage_id)
                else pd.NA
            )

            # The local output component ID is the
            # watershed ID. There is no separate
            # component_id variable in this loop.
            frame[
                "_source_component_id"
            ] = ws_id

            frame["_source_kind"] = (
                str(source)
                .strip()
                .lower()
            )

            frame["_source_special"] = (
                str(special)
                if pd.notna(special)
                else pd.NA
            )

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
                "Retrieved ws_id=%s gage=%s "
                "rows=%s first_date=%s "
                "last_date=%s",
                ws_id,
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
            "Built %s local-gaged rows "
            "for %s output watersheds",
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
                    "local_source_retrieval"
                ),
                status=(
                    "PASS"
                    if source_failures.empty
                    else "FAIL"
                ),
                message=(
                    "All configured local-gage "
                    "sources returned data."
                    if source_failures.empty
                    else (
                        f"{len(source_failures)} "
                        "local-gage sources failed, "
                        "returned no data, or were "
                        "not fully configured."
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
                    "local_gage_coverage"
                ),
                status=(
                    "PASS"
                    if incomplete.empty
                    else "FAIL"
                ),
                message=(
                    "All local gages cover the "
                    "requested period."
                    if incomplete.empty
                    else (
                        f"{len(incomplete)} local "
                        "gages have incomplete "
                        "date coverage."
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

        invalid_id_type = df.loc[
            ~df["id_type"].eq(
                "watershed"
            )
        ].copy()

        result.add_check(
            CheckResult(
                check_id=(
                    "local_gage_id_type"
                ),
                status=(
                    "PASS"
                    if invalid_id_type.empty
                    else "FAIL"
                ),
                message=(
                    "All local-gage records use "
                    "id_type=watershed."
                    if invalid_id_type.empty
                    else (
                        "Local-gage output contains "
                        "invalid id_type values."
                    )
                ),
                expected={
                    "id_type": "watershed",
                },
                actual={
                    "invalid_rows": len(
                        invalid_id_type
                    ),
                },
                blocking=True,
            )
        )

        valid_roles = {
            "direct",
            "adjusted",
        }

        invalid_roles = df.loc[
            ~df["flow_role"].isin(
                valid_roles
            )
        ].copy()

        result.add_check(
            CheckResult(
                check_id=(
                    "local_gage_flow_role"
                ),
                status=(
                    "PASS"
                    if invalid_roles.empty
                    else "FAIL"
                ),
                message=(
                    "All local-gage flow roles "
                    "are valid."
                    if invalid_roles.empty
                    else (
                        "Local-gage output contains "
                        "invalid flow roles."
                    )
                ),
                expected={
                    "allowed_flow_roles": (
                        sorted(valid_roles)
                    ),
                },
                actual={
                    "invalid_rows": len(
                        invalid_roles
                    ),
                },
                blocking=True,
            )
        )

        result.metrics.update(
            {
                "gages_checked": len(
                    coverage
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
                "source_failures": len(
                    source_failures
                ),
            }
        )
