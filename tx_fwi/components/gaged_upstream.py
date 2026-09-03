# tx_fwi/components/gaged_upstream.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.sources.usgs import fetch_usgs_daily_afday
# from tx_fwi.sources.lnra import load_lake_texana_daily_afday  # optional hook


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


class UpstreamGagedComponent:
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
                    print(f"  !! Failed fetch: {e}")
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
                df["note"] = label if label else special

                rows.append(df)

        if not rows:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        out = pd.concat(rows, ignore_index=True)

        return out[REQUIRED_OUT_COLS]

    # ------------------------------------------------------------------
    # RUN (with watermark logic)
    # ------------------------------------------------------------------
    def run(self, start=None, end=None) -> int:

        # Determine start
        if start is None:
            wm = self.ctx.storage.get_watermark(self.name, default="2015-01-01")

            if wm is not None:
                start = wm + pd.Timedelta(days=1)
            else:
                start = pd.Timestamp("2015-01-01")

        # Determine end
        if end is None:
            end = pd.Timestamp.utcnow().normalize()

        print(f"\n[Upstream] Running from {start} → {end}")

        df = self.build(start=start, end=end)

        if df.empty:
            print("[Upstream] No rows to write.")
            return 0

        n = self.ctx.storage.append(df)

        self.ctx.storage.set_watermark(self.name, df["date"].max())

        return n
