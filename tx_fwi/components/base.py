# tx_fwi/components/base.py
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from ..storage import Storage

@dataclass
class RunContext:
    storage: Storage
    ws_registry_path: str  # path to CSV/Parquet mapping ws_id/estuary/site ids
    end_date: str | None = None  # optional cap; otherwise component decides

class Component:
    name: str  # e.g., "gaged_usgs"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    def update(self) -> pd.DataFrame:
        """Return DAILY rows in canonical schema. Must be implemented."""
        raise NotImplementedError

    def run(self) -> int:
        df = self.update()
        if df is None or df.empty:
            return 0
        self.ctx.storage.write_partitioned(df)
        # Update watermark if the component chooses to
        return len(df)
