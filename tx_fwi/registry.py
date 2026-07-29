#This loads shapefile, evap/precip quads, and upstream gage config.
# tx_fwi/registry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import geopandas as gpd
import pandas as pd


@dataclass
class Registry:
    def __init__(
        self,
        watershed_shp,
        upstream_json=None,
        subwatersheds_csv=None,
        quads_csv=None,
    ):
        self.watershed_shp = Path(watershed_shp)
        self.upstream_json = (
            Path(upstream_json)
            if upstream_json else None
        )

        self.quads_csv = (
            Path(quads_csv)
            if quads_csv else None
        )

    def load_quads(self):

        if self.quads_csv is None:
            raise ValueError(
                "quads_csv not configured"
            )

        df = pd.read_csv(
            self.quads_csv
        )

        df.columns = [
            c.strip()
            for c in df.columns
        ]

        df["WS_ID"] = (
            df["WS_ID"]
            .astype(str)
            .str.zfill(5)
        )

        df["quad"] = (
            df["quad"]
            .astype(str)
            .str.strip()
        )

        return df


    

    def load_watersheds(self) -> gpd.GeoDataFrame:
        if not self.watershed_shp.exists():
            raise FileNotFoundError(f"Watershed registry not found: {self.watershed_shp}")

        gdf = gpd.read_file(self.watershed_shp)

        if "WS_ID" not in gdf.columns:
            raise ValueError("Watershed registry must contain WS_ID.")

        gdf["WS_ID"] = (
            gdf["WS_ID"]
            .astype(str)
            .str.strip()
            .apply(lambda x: x.zfill(5) if x.isdigit() else x)
        )

        return gdf

    def load_upstream_gages(self) -> dict:
        if not self.upstream_gages_json.exists():
            raise FileNotFoundError(
                f"Upstream gage config not found: {self.upstream_gages_json}"
            )

        return json.loads(self.upstream_gages_json.read_text(encoding="utf-8"))
