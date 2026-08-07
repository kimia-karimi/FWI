# tx_fwi/components/gaged_local.py
from __future__ import annotations

import pandas as pd

from tx_fwi.components.base import RunContext

from tx_fwi.sources.base import registry


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


def _normalize_key(x):
    if x is None or pd.isna(x):
        return None
    return str(x).strip().lower()

class LocalGagedComponent:
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
        reg = registry[registry["HAS_GAGED"] == 1].copy()

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
            df["count_in_basin_sum"] = 1
            df["note"] = special if pd.notna(special) else None

            rows.append(df)
            print(df.head())

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
