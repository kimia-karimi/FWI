# tx_fwi/components/gaged_local.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext

from tx_fwi.sources.base import registry
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


def _normalize_key(x):
    if x is None or pd.isna(x):
        return None
    return str(x).strip().lower()

class LocalGagedComponent(QAComponentMixin):
    name = "gaged"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    # ----------------------------------------------------------
    # FETCH using plugin registry
    # ----------------------------------------------------------
    
    def fetch(self, source, gage_id, start, end, *, special, meta=None):

        source_norm = _normalize_key(source)
        special_norm = _normalize_key(special)

        try:
            handler = registry.get(source_norm, special_norm)
        except KeyError:
            raise NotImplementedError(
                f"No handler registered for source={source_norm}, special={special_norm}"
            )

        print(f"[Local] source={source_norm}, special={special_norm}, gage={gage_id}")
        #print(registry._handlers.keys())


        #  unified calling convention
        return handler(
            start=start,
            end=end,
            site_id=str(gage_id) if gage_id else None,
            meta=meta
        )


    # ----------------------------------------------------------
    def build(self, start, end) -> pd.DataFrame:

        registry = self.ctx.registry.load_watersheds()
        reg = registry[(registry["HAS_GAGED"] == 1)| (registry["SPECIAL"].notna())].copy()
        special_groups = {}

        for group, grp in registry.groupby("SPECIAL"):
            if pd.isna(group):
                continue

            primary = grp.loc[grp["HAS_GAGED"] == 1]

            if len(primary) != 1:
                raise ValueError(
                    f"{group} must have exactly one HAS_GAGED=1 watershed"
                )

            special_groups[group] = str(primary.iloc[0]["WS_ID"])

        rows = []

        for _, r in reg.iterrows():

            ws_id = str(r["WS_ID"])
            estuary = r.get("Estuary")
            source = r.get("G_SOURCE")
            gage_id = r.get("GAGE_ID")
            special = r.get("SPECIAL")
            meta=r 
            print(
                f"source={source}, "
                f"special={special}, "
                f"type={type(special)}, "
                f"bool={bool(special)}"
            )

            if pd.isna(source) or pd.isna(gage_id):
                continue

            try:
                s = self.fetch(
                    source=source,
                    gage_id=gage_id,
                    start=start,
                    end=end,
                    special=special,
                )
            except Exception as e:
                print(f"[Local] Failed for WS {ws_id}: {e}")
                continue

            if s is None or s.empty:
                continue

            df = s.reset_index()
            df.columns = ["date", "value_afday"]

            df["id"] = ws_id
            df["id_type"] = "watershed"
            df["estuary"] = estuary
            df["component"] = self.name
            df["source"] = str(source).lower()
            df["flow_role"] = "adjusted" if pd.notna(special) else "direct"
            

            if pd.notna(special) and pd.isna(gage_id):
                primary_ws = special_groups[special]
                df["count_in_basin_sum"] =int(ws_id == primary_ws)
            else:
                df["count_in_basin_sum"] = 1
            df["note"] = special if pd.notna(special) else None
            df["data_as_of"] = pd.Timestamp(end)
            df["_source_gage"] = str(gage_id)
            df["_source_component_id"] = str(component_id)
            df["_source_expected_start"] = (pd.Timestamp(start).normalize())
            df["_source_expected_end"] = (pd.Timestamp(end).normalize())

            rows.append(df)
            print(df.head())

        if not rows:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        return pd.concat(rows, ignore_index=True)[REQUIRED_OUT_COLS+ [ "_source_gage","_source_component_id","_source_expected_start", "_source_expected_end", ]
        ]

    # ----------------------------------------------------------
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
