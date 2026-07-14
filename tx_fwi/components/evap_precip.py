from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext

from tx_fwi.sources.wdft_watercycle import (
    fetch_watercycle,
)

from tx_fwi.transforms.units import (
    inches_to_af,
)

from tx_fwi.transforms.temporal import (
    expand_monthly_to_daily,
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
    "note",
]

class EvapPrecipComponent:

    name = "evap_precip"

    def __init__(
        self,
        ctx: RunContext,
    ):
        self.ctx = ctx


    # --------------------------------------------------
    def build_precip(self, start, end):

        wc = fetch_watercycle(start, end)

        if wc.empty:
            return pd.DataFrame()

        watersheds = self.ctx.registry.load_watersheds()

        required = [
            "WS_ID",
            "Estuary",
            "QUAD",
            "AREA_ACRES",
        ]

        watersheds = watersheds[required].copy()

        df = watersheds.merge(
            wc,
            on="QUAD",
            how="inner",
        )

        df["monthly_af"] = inches_to_af(
            df["precip_inches"],
            df["AREA_ACRES"],
        )

        df["year"] = pd.to_datetime(
            df["period"]
        ).dt.year

        df["month"] = pd.to_datetime(
            df["period"]
        ).dt.month

        daily = expand_monthly_to_daily(
            df.rename(
                columns={
                    "WS_ID": "ws_id",
                    "Estuary": "estuary",
                    "monthly_af": "value",
                }
            ),
            year_col="year",
            month_col="month",
            value_col="value",
            id_col="ws_id",
            estuary_col="estuary",
            value_is_monthly_total=True,
        )

        daily["id"] = daily["ws_id"]
        daily["id_type"] = "watershed"

        daily["component"] = "precipitation"

        daily["source"] = "wdft"

        daily["flow_role"] = "addition"

        daily["count_in_basin_sum"] = 1

        daily["note"] = None

        return daily[
            REQUIRED_OUT_COLS
        ]

    # --------------------------------------------------
    def build_evap(self, start, end):

        wc = fetch_watercycle(start, end)

        if wc.empty:
            return pd.DataFrame()

        watersheds = self.ctx.registry.load_watersheds()

        watersheds = watersheds[
            [
                "WS_ID",
                "ESTUARY",
                "QUAD",
                "AREA_ACRES",
            ]
        ].copy()

        df = watersheds.merge(
            wc,
            on="QUAD",
            how="inner",
        )

        df["monthly_af"] = inches_to_af(
            df["evap_inches"],
            df["AREA_ACRES"],
        )

        df["year"] = pd.to_datetime(
            df["period"]
        ).dt.year

        df["month"] = pd.to_datetime(
            df["period"]
        ).dt.month

        daily = expand_monthly_to_daily(
            df.rename(
                columns={
                    "WS_ID": "ws_id",
                    "ESTUARY": "estuary",
                    "monthly_af": "value",
                }
            ),
            year_col="year",
            month_col="month",
            value_col="value",
            id_col="ws_id",
            estuary_col="estuary",
            value_is_monthly_total=True,
        )

        daily["id"] = daily["ws_id"]
        daily["id_type"] = "watershed"

        daily["component"] = "evaporation"

        daily["source"] = "wdft"

        daily["flow_role"] = "loss"

        daily["count_in_basin_sum"] = 1

        daily["note"] = None

        return daily[
            REQUIRED_OUT_COLS
        ]
    def _build_component(
        self,
        watercycle,
        metric_col,
        component_name,
        flow_role,
    ):

        subwatersheds = (
            self.ctx.registry
            .load_subwatersheds()
        )

        df = subwatersheds.merge(
            watercycle,
            on="quad",
            how="inner",
        )

        #
        # Convert inches over area
        #
        df["monthly_af"] = inches_to_af(
            df[metric_col],
            df["area_acres"],
        )

        #
        # Aggregate all quads
        # belonging to same watershed
        #
        monthly = (
            df.groupby(
                [
                    "WS_ID",
                    "Estuary",
                    "year",
                    "month",
                ],
                as_index=False,
            )["monthly_af"]
            .sum()
        )

        daily = expand_monthly_to_daily(
            monthly.rename(
                columns={
                    "WS_ID": "ws_id",
                    "Estuary": "estuary",
                    "monthly_af": "value",
                }
            ),
            year_col="year",
            month_col="month",
            value_col="value",
            id_col="ws_id",
            estuary_col="estuary",
            value_is_monthly_total=True,
        )

        daily["id"] = daily["ws_id"]

        daily["id_type"] = "watershed"

        daily["component"] = component_name

        daily["source"] = "wdft"

        daily["flow_role"] = flow_role

        daily["count_in_basin_sum"] = 1

        daily["note"] = None

        return daily[
            REQUIRED_OUT_COLS
        ]

    # --------------------------------------------------
    def build(
        self,
        start,
        end,
    ):

        wc = fetch_watercycle(
            start=start,
            end=end,
        )

        if wc.empty:
            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        precip = self._build_component(
            wc,
            metric_col="precip_inches",
            component_name="precipitation",
            flow_role="addition",
        )

        evap = self._build_component(
            wc,
            metric_col="evap_inches",
            component_name="evaporation",
            flow_role="loss",
        )

        return pd.concat(
            [precip, evap],
            ignore_index=True,
        )

    # --------------------------------------------------
    def run(
        self,
        start=None,
        end=None,
    ):

        if start is None:

            wm = (
                self.ctx.storage
                .get_watermark(
                    self.name,
                    default="2015-01-01",
                )
            )

            start = (
                wm + pd.Timedelta(days=1)
                if wm is not None
                else pd.Timestamp("2015-01-01")
            )

        if end is None:
            end = (
                pd.Timestamp.utcnow()
                .normalize()
            )

        print(
            f"[EvapPrecip] "
            f"Running {start} → {end}"
        )

        df = self.build(
            start=start,
            end=end,
        )

        if df.empty:
            print(
                "[EvapPrecip] "
                "No rows written"
            )
            return 0

        n = self.ctx.storage.append(df)

        self.ctx.storage.set_watermark(
            self.name,
            df["date"].max(),
        )

        return n
