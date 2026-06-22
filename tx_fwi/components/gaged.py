# tx_fwi/components/gaged_local.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.sources.usgs import fetch_usgs_daily_afday
from tx_fwi.sources.ibwc import fetch_ibwc_daily_rounded_afday


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


class LocalGagedComponent:
    name = "gaged"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    def fetch(self, source: str, gage_id: str, start, end, special=None) -> pd.Series:
        source = str(source).lower()

        if source == "usgs":
            # Special cases can be routed here later:
            # colorado_adjusted
            # lake_houston
            return fetch_usgs_daily_afday(gage_id, start, end)

        if source == "ibwc":
            return fetch_ibwc_daily_rounded_afday(gage_id, start, end)

        raise NotImplementedError(f"Unsupported local gage source: {source}")

    def build(self, start, end) -> pd.DataFrame:
        registry = self.ctx.registry.load_watersheds()
        reg = registry[registry["HAS_GAGED"] == 1].copy()

        rows = []

        for _, r in reg.iterrows():
            ws_id = str(r["WS_ID"])
            estuary = r.get("Estuary")
            source = r.get("G_SOURCE")
            gage_id = r.get("GAGE_ID")
            special = r.get("SPECIAL_T")

            if pd.isna(source) or pd.isna(gage_id):
                continue

            special_val = None if pd.isna(special) else str(special)

            s = self.fetch(
                source=str(source),
                gage_id=str(gage_id),
                start=start,
                end=end,
                special=special_val,
            )

            if s.empty:
                continue

            df = s.reset_index()
            df.columns = ["date", "value_afday"]

            df["id"] = ws_id
            df["id_type"] = "watershed"
            df["estuary"] = estuary
            df["component"] = self.name
            df["source"] = str(source).lower()
            df["flow_role"] = "adjusted" if special_val else "direct"
            df["count_in_basin_sum"] = 1
            df["note"] = special_val

            rows.append(df)

        if not rows:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        return pd.concat(rows, ignore_index=True)[REQUIRED_OUT_COLS]

    def run(self, start=None, end=None) -> int:
        if start is None:
            wm = self.ctx.storage.get_watermark(self.name, default="2015-01-01")
            start = wm + pd.Timedelta(days=1) if wm is not None else pd.Timestamp("2015-01-01")

        if end is None:
            end = pd.Timestamp.utcnow().normalize()

        df = self.build(start=start, end=end)
        n = self.ctx.storage.append(df)

        if not df.empty:
            self.ctx.storage.set_watermark(self.name, df["date"].max())

        return n