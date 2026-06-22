#This loads shapefile and upstream gage config.
# tx_fwi/registry.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import geopandas as gpd


@dataclass
class Registry:
    watershed_shp: Path
    upstream_gages_json: Path

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
