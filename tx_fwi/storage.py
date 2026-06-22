# tx_fwi/storage.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import pandas as pd

REQUIRED_COLS = [
    "date", "ws_id", "estuary", "component", "value_afday", "source"
]

@dataclass
class Storage:
    root: Path  # e.g., Path(r"T:\CoastalScience\Data\Hydrology\fwi_master\data")

    @property
    def processed_root(self) -> Path:
        return self.root / "processed" / "fwi_timeseries"

    @property
    def state_path(self) -> Path:
        return self.root / "state" / "watermarks.json"

    def load_watermarks(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    def save_watermarks(self, wm: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(wm, indent=2), encoding="utf-8")

    def get_watermark(self, component: str, default: str | None = None) -> pd.Timestamp | None:
        wm = self.load_watermarks()
        v = wm.get(component, default)
        return pd.to_datetime(v) if v else None

    def set_watermark(self, component: str, dt: pd.Timestamp) -> None:
        wm = self.load_watermarks()
        wm[component] = pd.to_datetime(dt).strftime("%Y-%m-%d")
        self.save_watermarks(wm)

    def write_partitioned(self, df: pd.DataFrame) -> None:
        # Validate schema
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Normalize types
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        out["year"] = out["date"].dt.year.astype(int)

        # Partition write by (year, component)
        for (year, component), g in out.groupby(["year", "component"]):
            part_dir = self.processed_root / f"year={year}" / f"component={component}"
            part_dir.mkdir(parents=True, exist_ok=True)

            # Write a new part file each run (simple + safe for append-only)
            part_file = part_dir / f"part-{pd.Timestamp.utcnow().strftime('%Y%m%dT%H%M%SZ')}.parquet"
            g.drop(columns=["year"]).to_parquet(part_file, index=False)
