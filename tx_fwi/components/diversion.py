# tx_fwi/components/diversion.py
import pandas as pd
import geopandas as gpd
import requests
from .base import Component
from ..transforms.temporal import expand_monthly_to_daily

class Diversion(Component):
    name = "diversion"

    def update(self) -> pd.DataFrame:
        # NOTE: This keeps your current approach:
        # - fetch water rights (layer 13) + points (layer 3)
        # - merge on WR_ID
        # - spatial join with coastal watershed shapefile to assign WS_ID
        # - aggregate monthly fields JAN_DIV..DEC_DIV and expand to daily [7](https://twdb-my.sharepoint.com/personal/alex_barth_twdb_texas_gov/Documents/Recordings/Estuary%20Science%20Exchange%20(Spring%202026)%20Estimating%20Total%20Suspended%20Solids%20in%20Texas%20Estuarine%20Waters%20Using%20Hyperspectral%20Imaging%20and%20In-situ%20Measurements-20260501_120324-Meeting%20Recording.mp4?web=1)

        # You’ll likely put these in config.py later.
        rights_url = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer/13/query"
        points_url = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer/3/query"
        watersheds_path = r"T:\CoastalScience\Data\Hydrology\fwi_master\watersheds\watersheds_registry.shp"  
        # ---- fetch rights ----
        rights = _fetch_all_features(rights_url)
        df_rights = pd.DataFrame([f["attributes"] for f in rights])

        # ---- fetch points ----
        pts = _fetch_all_features(points_url)
        df_points = pd.DataFrame([f["attributes"] for f in pts])

        # normalize and join
        df_points["LAT_DD"] = pd.to_numeric(df_points["LAT_DD"], errors="coerce").round(4)
        df_points["LONG_DD"] = pd.to_numeric(df_points["LONG_DD"], errors="coerce").round(4)

        wr_coord = df_rights.merge(df_points[["WR_ID", "LAT_DD", "LONG_DD"]], on="WR_ID", how="left")

        # aggregate same WR_ID/YEAR 
        wr_merged = (wr_coord
            .dropna(subset=["WR_ID"])
            .groupby(["WR_ID", "YEAR", "LAT_DD", "LONG_DD"], as_index=False)
            .sum(numeric_only=True)
        )

        # spatial join to WS_ID
        watersheds = gpd.read_file(watersheds_path)
        gdf_points = gpd.GeoDataFrame(
            wr_merged,
            geometry=gpd.points_from_xy(wr_merged.LONG_DD, wr_merged.LAT_DD),
            crs="EPSG:4269"
        )
        watersheds = watersheds.to_crs(gdf_points.crs)

        joined = gpd.sjoin(gdf_points, watersheds, how="inner", predicate="INTERSECTS")

        # monthly columns 
        monthly_cols = ["JAN_DIV","FEB_DIV","MAR_DIV","APR_DIV","MAY_DIV","JUN_DIV",
                        "JUL_DIV","AUG_DIV","SEPT_DIV","OCT_DIV","NOV_DIV","DEC_DIV"]

        wsd_wr = (joined
            .drop(columns=["geometry", "LAT_DD", "LONG_DD"], errors="ignore")
            .groupby(["WS_ID", "YEAR"], as_index=False)[monthly_cols]
            .sum(numeric_only=True)
        )

        # reshape to long
        df_long = wsd_wr.melt(
            id_vars=["WS_ID", "YEAR"],
            value_vars=monthly_cols,
            var_name="month",
            value_name="monthly_value"
        )

        month_map = {"JAN_DIV":1,"FEB_DIV":2,"MAR_DIV":3,"APR_DIV":4,"MAY_DIV":5,"JUN_DIV":6,
                     "JUL_DIV":7,"AUG_DIV":8,"SEPT_DIV":9,"OCT_DIV":10,"NOV_DIV":11,"DEC_DIV":12}
        df_long["month"] = df_long["month"].map(month_map)

        # expand monthly->daily by days-in-month 
        daily = expand_monthly_to_daily(
            df_long.rename(columns={"WS_ID":"WS_ID", "YEAR":"YEAR"}),
            year_col="YEAR", month_col="month", value_col="monthly_value", ws_col="WS_ID"
        )

        # attach estuary from registry (single source of truth)
        reg = pd.read_csv(self.ctx.ws_registry_path, dtype={"ws_id": str})
        daily = daily.merge(reg[["ws_id","estuary"]], on="ws_id", how="left")

        daily["component"] = self.name
        daily["source"] = "TCEQ"

        # watermark (diversion updates infrequently; still track last date written)
        if not daily.empty:
            self.ctx.storage.set_watermark(self.name, daily["date"].max())

        return daily[["date","ws_id","estuary","component","value_afday","source"]]


def _fetch_all_features(url: str, where: str = "1=1", out_fields: str = "*", batch_size: int = 2000):
    # the script already checks expected record count and paginates; this mirrors that approach.
    count = requests.get(url, params={"where": where, "returnCountOnly": "true", "f": "json"}).json()["count"]
    feats = []
    offset = 0
    while True:
        params = {"where": where, "outFields": out_fields, "f": "json",
                  "resultOffset": offset, "resultRecordCount": batch_size}
        payload = requests.get(url, params=params).json()
        batch = payload.get("features", [])
        feats.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
        if len(feats) >= count:
            break
    return feats
