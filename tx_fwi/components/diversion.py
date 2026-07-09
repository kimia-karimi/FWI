# tx_fwi/components/diversion.py
import pandas as pd
import geopandas as gpd
import requests
from tx_fwi.transforms.temporal import expand_monthly_to_daily
from __future__ import annotations
from tx_fwi.components.base import RunContext
from tx_fwi.sources.base import registry
from tx_fwi.transforms.units import mgd_to_afday

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
RIGHTS_URL = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer/13/query"
POINTS_URL = "https://gisweb.tceq.texas.gov/arcgis/rest/services/WaterRights/WaterRightsViewer/MapServer/3/query"
MONTH_MAP = {"JAN_DIV":1,"FEB_DIV":2,"MAR_DIV":3,"APR_DIV":4,"MAY_DIV":5,"JUN_DIV":6,
                     "JUL_DIV":7,"AUG_DIV":8,"SEPT_DIV":9,"OCT_DIV":10,"NOV_DIV":11,"DEC_DIV":12}
# monthly columns 
MONTHLY_COLS = list(MONTH_MAP.keys())

class DiversionflowComponent:
    name = "diversion"
    def __init__(self, ctx):
        self.ctx = ctx
    def _fetch_all_features(self, url: str, where: str = "1=1", out_fields: str = "*", batch_size: int = 2000):
        count = requests.get(url, params={"where": where, "returnCountOnly": "true", "f": "json"}, timeout=60).json()["count"]
        feats = []
        offset = 0
        while True:
            params = {"where": where, "outFields": out_fields, "f": "json",
                  "resultOffset": offset, "resultRecordCount": batch_size}
        payload = requests.get(url, params=params, timeout=60).json()
        batch = payload.get("features", [])
        feats.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size
        if len(feats) >= count:
            break
    return feats


    def build(self, start, end):
        # NOTE: This keeps your current approach:
        # - fetch water rights (layer 13) + points (layer 3)
        # - merge on WR_ID
        # - spatial join with watershed registry to assign WS_ID
        # - aggregate monthly fields JAN_DIV..DEC_DIV and expand to daily 

        
        # ---- fetch rights ----
        rights = self._fetch_all_features(RIGHTS_URL)
        df_rights = pd.DataFrame([f["attributes"] for f in rights if "attributes" in f])

        # ---- fetch points ----
        pts = self._fetch_all_features(POINTS_URL)
        df_points = pd.DataFrame([f["attributes"] for f in pts if "attributes" in f])
        if df_rights.empty or df_points.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)
        # ---- attach coordinates ----
        wr_coord = df_rights.merge(df_points[["WR_ID", "LAT_DD", "LONG_DD"]], on="WR_ID", how="left")
        
        # normalize and join
        #df_points["LAT_DD"] = pd.to_numeric(df_points["LAT_DD"], errors="coerce").round(4)
        #df_points["LONG_DD"] = pd.to_numeric(df_points["LONG_DD"], errors="coerce").round(4)

        # aggregate same WR_ID/YEAR 
        wr_merged = (wr_coord
            .dropna(subset=["WR_ID"])
            .groupby(["WR_ID", "YEAR", "LAT_DD", "LONG_DD"], as_index=False)
            .sum(numeric_only=True)
        )

        # spatial join to watershed registry
        watersheds = (self.ctx.registry.load_watersheds()[["WS_ID", "ESTUARY", "geometry"]])

        gdf_points = gpd.GeoDataFrame(
            wr_merged,
            geometry=gpd.points_from_xy(wr_merged["LONG_DD"], wr_merged["LAT_DD"],
            crs="EPSG:4269"
        )
        watersheds = watersheds.to_crs(gdf_points.crs)

        joined = gpd.sjoin(gdf_points, watersheds, how="inner", predicate="INTERSECTS")
        #aggregate by watershed
        

        wsd_wr = (joined
            .drop(columns=["geometry", "LAT_DD", "LONG_DD"], errors="ignore")
            .groupby(["WS_ID", "ESTUARY", "YEAR"], as_index=False)[monthly_cols]
            .sum(numeric_only=True)
        )

        # reshape to long
        df_long = wsd_wr.melt(
            id_vars=["WS_ID", "ESTUARY", "YEAR"],
            value_vars=monthly_cols,
            var_name="month_name",
            value_name="diversion"
        )

       
        df_long["month"] = df_long["month_name"].map(MONTH_MAP)

        # expand monthly->daily by days-in-month 
        daily = expand_monthly_to_daily(
            df_long,
            year_col="YEAR", month_col="month", value_col="diversion"
        )
        
        if daily.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)


        # attach estuary from registry (single source of truth)
        #reg = pd.read_csv(self.ctx.ws_registry_path, dtype={"ws_id": str})
       # daily = daily.merge(reg[["ws_id","estuary"]], on="ws_id", how="left")

        daily["date"] = pd.to_datetime(daily["date"])

        daily["id"] = daily["WS_ID"]
        daily["id_type"] = "watershed"
        daily["estuary"] = daily["ESTUARY"]

        daily["component"] = self.name
        daily["source"] = "TCEQ"

        daily["value_afday"] = mgd_to_afday(daily["value"])

        daily["flow_role"] = "removal"

        daily["count_in_basin_sum"] = 1

        daily["note"] = None

        return daily[REQUIRED_OUT_COLS]
    def run(self, start=None, end=None):
        if start is None:
            start = pd.Timestamp("2015-01-01")

        if end is None:
            end = pd.Timestamp.utcnow().normalize()

        print(f"[Diversion] Running {start} → {end}")

        df = self.build(start, end)

        if df.empty:
            print("[Diversion] No rows written")
            return 0

        n = self.ctx.storage.append(df)

        return n


