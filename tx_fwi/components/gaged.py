# tx_fwi/components/gaged_local.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.sources.usgs import fetch_usgs_daily_afday
from tx_fwi.sources.ibwc import fetch_ibwc_daily_rounded_afday
from tx_fwi.sources.lnra import load_lake_texana_afday
from tx_fwi.sources.colorado import colorado_adjusted_afday


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

    # ----------------------------------------------------------
    # ✅ CENTRALIZED FETCH LOGIC
    # ----------------------------------------------------------
    def fetch(self, source, gage_id, start, end, *, special=None):
        source = str(source).lower()
        special = None if pd.isna(special) else str(special).lower()

        # ==========================================================
        # ✅ SPECIAL CASES FIRST (highest priority)
        # ==========================================================

        #  Colorado adjusted flow
        if special == "colorado_adjusted":
            print(f"[Local] Colorado adjusted {gage_id}")

            return colorado_adjusted_afday(start, end)

        #  Lake Houston special
        if special == "lake_houston":
            print(f"[Local] Lake Houston {gage_id}")

            # If no custom math, just return USGS
            return fetch_usgs_daily_afday(gage_id, start, end)

        #  Lake Texana (LNRA file)
        if special == "lake_texana":
            print("[Local] Lake Texana (LNRA file)")

            return load_lake_texana_afday(start=start, end=end)

        # ==========================================================
        # ✅ NORMAL GAGES (fallback)
        # ==========================================================

        # ✅ USGS
        if source == "usgs":
            print(f"[Local] USGS {gage_id}")
            return fetch_usgs_daily_afday(gage_id, start, end)

        # ✅ IBWC
        if source == "ibwc":
            print(f"[Local] IBWC {gage_id}")
            return fetch_ibwc_daily_rounded_afday(gage_id, start, end)

        raise NotImplementedError(f"Unsupported local gage source: {source}")

    # ----------------------------------------------------------
    def build(self, start, end) -> pd.DataFrame:

        registry = self.ctx.registry.load_watersheds()
        reg = registry[registry["HAS_GAGED"] == 1].copy()

        rows = []

        for _, r in reg.iterrows():

            ws_id = str(r["WS_ID"])
            estuary = r.get("ESTUARY")
            source = r.get("G_SOURCE")
            gage_id = r.get("GAGE_ID")
            special = r.get("SPECIAL_T")

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
            df["flow_role"] = "adjusted" if special else "direct"
            df["count_in_basin_sum"] = 1
            df["note"] = special

            rows.append(df)

        if not rows:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        return pd.concat(rows, ignore_index=True)[REQUIRED_OUT_COLS]

    # ----------------------------------------------------------
    def run(self, start=None, end=None) -> int:

        if start is None:
            wm = self.ctx.storage.get_watermark(self.name, default="2015-01-01")
            start = wm + pd.Timedelta(days=1) if wm is not None else pd.Timestamp("2015-01-01")

        if end is None:
            end = pd.Timestamp.utcnow().normalize()

        print(f"\n[Local] Running {start} → {end}")

        df = self.build(start=start, end=end)

        if df.empty:
            print("[Local] No rows written")
            return 0

        n = self.ctx.storage.append(df)

        self.ctx.storage.set_watermark(self.name, df["date"].max())

        return n