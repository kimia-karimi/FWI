# tx_fwi/components/gaged_upstream.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.sources.usgs import fetch_usgs_daily_afday
# from tx_fwi.sources.lnra import load_lake_texana_daily_afday  # optional hook
from tx_fwi.components.base import RunContext
from tx_fwi.components.qaqc_mixin import QAComponentMixin
from tx_fwi.qaqc.component_checks import validate_canonical_component

from tx_fwi.qaqc.models import CheckResult

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


class UpstreamGagedComponent(QAComponentMixin):
    """
    Builds estuary-level upstream gaged inflows (system components).

    These are NOT tied to watershed polygons.
    They represent total upstream inflow (e.g., Brazos, Guadalupe, etc.).
    """

    name = "gaged_upstream"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    # ------------------------------------------------------------------
    # FETCH LOGIC (shared USGS / LNRA / IBWC in future)
    # ------------------------------------------------------------------
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

        source = str(source).lower()

        # ✅ USGS (primary path)
        if source == "usgs":
            print(f"[Upstream] Fetching USGS {gage_id}")
            logger = self.ctx.component_logger(self.name)

            logger.debug("Fetching source=%s gage=%s " "for %s through %s",source,gage_id,pd.Timestamp(start).date(),pd.Timestamp(end).date(),)
            return fetch_usgs_daily_afday(gage_id, start, end)

        # 🔧 LNRA / Lake Texana (future hook)
        #if source == "lnra":
            #raise NotImplementedError(
                #"LNRA/Lake Texana requires a file path. "
                #"Add 'path' to upstream_gages.json."
            #)

        # 🔧 IBWC (optional future extension)
        if source == "ibwc":
            raise NotImplementedError("IBWC upstream not yet implemented.")

        raise NotImplementedError(f"Unsupported upstream source: {source}")

    # ------------------------------------------------------------------
    # BUILD DATAFRAME
    # ------------------------------------------------------------------
    def build(self, start, end) -> pd.DataFrame:
        self._source_failures = []
        print(self.ctx.registry.upstream_json)
        upstream_cfg = self.ctx.registry.load_upstream_gages()
        print(list(upstream_cfg.keys()))

        rows = []

        for estuary, components in upstream_cfg.items():

            print(f"\n[Upstream] Estuary: {estuary}")

            for comp in components:

                component_id = comp["component_id"]
                source = comp["source"]
                gage_id = comp["gage_id"]
                label = comp.get("label")
                special = comp.get("special")

                print(
                    f"  → Component: {component_id} | "
                    f"{source}:{gage_id}"
                )

                # ------------------------------------------------------------------
                # Fetch timeseries
                # ------------------------------------------------------------------
                try:
                    s = self.fetch(
                        source=source,
                        gage_id=gage_id,
                        start=start,
                        end=end,
                        special=special,
                        meta=comp,
                    )
                except Exception as e:
                    logger.error(
                        "Source fetch failed for "
                        "component_id=%s gage=%s: %s",
                        component_id,
                        gage_id, e,)
                    self._source_failures.append(
                        {
                            "component_id": component_id, "gage_id": gage_id,"source": source,"reason": str(exc), "exception_type": ( type(exc).__name__),
                        }
                    )
                    continue

                if s is None or s.empty:
                    print("  !! No data returned")
                    continue

                # ------------------------------------------------------------------
                # Convert to dataframe
                # ------------------------------------------------------------------
                df = s.reset_index()
                df.columns = ["date", "value_afday"]

                df["id"] = component_id
                df["id_type"] = "system"
                df["estuary"] = estuary
                df["component"] = self.name
                df["source"] = str(source).lower()
                df["flow_role"] = "system_counted"
                df["count_in_basin_sum"] = 1
                df["data_as_of"] = pd.Timestamp(end)
                df["note"] = label if label else special
                df["_source_gage"] = str(gage_id)
                df["_source_component_id"] = str(component_id)
                df["_source_expected_start"] = (pd.Timestamp(start).normalize())
                df["_source_expected_end"] = (pd.Timestamp(end).normalize())

                rows.append(df)

        if not rows:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        out = pd.concat(rows, ignore_index=True)

        return out[REQUIRED_OUT_COLS+ [ "_source_gage","_source_component_id","_source_expected_start", "_source_expected_end", ]]
                
    # ------------------------------------------------------------------
    # RUN (with watermark logic)
    # ------------------------------------------------------------------
    def validate(
    self,
    df,
    *,
    result,
    start,
    end,
    ) -> None:
    validate_canonical_component(
        df,
        component_result=result,
        run_manager=(
            self.ctx.run_manager
        ),
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

    if df.empty:
        return

    # Distinguish actual missing dates from legitimate zero flows.
    expected_dates = pd.date_range(
        pd.Timestamp(start).normalize(),
        pd.Timestamp(end).normalize(),
        freq="D",
    )

    coverage_rows = []

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

    exception_path = (
        self.ctx.run_manager
        .write_exception_csv(
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
                        exception_path
                    )
                }
                if exception_path
                else {}
            ),
        )
    )

    result.metrics.update(
        {
            "gages_checked": len(
                coverage
            ),
            "complete_gages": int(
                coverage["complete"].sum()
            ),
            "incomplete_gages": int(
                (~coverage["complete"]).sum()
            ),
            "zero_value_dates": int(
                coverage[
                    "zero_value_dates"
                ].sum()
            ),
        }
    )    
