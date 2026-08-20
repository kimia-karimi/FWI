# tx_fwi/components/diversion.py
from __future__ import annotations
import pandas as pd
import geopandas as gpd
from tx_fwi.sources.tceq_wr import TCEQWaterRightsSource
from tx_fwi.transforms.temporal import expand_monthly_to_daily
from tx_fwi.components.base import RunContext
from tx_fwi.sources.base import registry
from tx_fwi.transforms.units import mgd_to_afday
import certifi
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

MONTH_MAP = {"JAN_DIV":1,"FEB_DIV":2,"MAR_DIV":3,"APR_DIV":4,"MAY_DIV":5,"JUN_DIV":6,
                     "JUL_DIV":7,"AUG_DIV":8,"SEPT_DIV":9,"OCT_DIV":10,"NOV_DIV":11,"DEC_DIV":12}
# monthly columns 
MONTHLY_COLS = list(MONTH_MAP.keys())

class DiversionComponent:
    name = "diversion"
    def __init__(self, ctx):
        self.ctx = ctx
        self.rights_source = TCEQWaterRightsSource(ctx=ctx)


    def build(self, start, end):
        # NOTE: This keeps the current approach:
        # - fetch water rights (layer 13) + points (layer 3)
        # - aggregate water use by WR_ID/YEAR
        # - fetch diversion points (layer 3)
        # - reduce points to ONE coordinate per WR_ID
        # - spatial join with watershed registry to assign WS_ID
        # - aggregate monthly fields JAN_DIV..DEC_DIV and expand to daily 

        
        # ---- fetch rights ----
        source_data = self.rights_source.fetch(start=start, end=end)
        rights = source_data["rights"]
        pts = source_data["points"]
        df_rights = pd.DataFrame([f["attributes"] for f in rights if "attributes" in f])
        df_rights["YEAR"] = pd.to_numeric(df_rights["YEAR"],errors="coerce")
        #filter between dates
        start_year = pd.Timestamp(start).year
        end_year = pd.Timestamp(end).year

        df_rights = df_rights[df_rights["YEAR"].between(start_year, end_year)].copy()

        # aggregate same WR_ID/YEAR 
        for c in MONTHLY_COLS:
            df_rights[c] = pd.to_numeric(df_rights[c], errors="coerce").fillna(0.0)
        wr_merged = (
            df_rights
            .dropna(subset=["WR_ID", "YEAR"])
            .groupby(["WR_ID", "YEAR"], as_index=False)[MONTHLY_COLS]
            .sum(numeric_only=True)
        )
        
        # ---- fetch points ----
        df_points = pd.DataFrame([f["attributes"] for f in pts if "attributes" in f])
        if wr_merged.empty or df_points.empty or df_points.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        # ---- attach coordinates ----
        #left merging to keep all the IDS. point records can include diversion points and on-channel reservoir locations
        df_points["TYPE"] = df_points["TYPE"].astype(str).str.strip()
        # Keep diversion points only for diversion component.
        points_div = df_points[df_points["TYPE"].str.lower().eq("diversion point")].copy()
        
        # normalize and join
        points_div["LAT_DD"] = pd.to_numeric(points_div["LAT_DD"], errors="coerce").round(4)
        points_div["LONG_DD"] = pd.to_numeric(points_div["LONG_DD"], errors="coerce").round(4)
        points_div = points_div.dropna(subset=["WR_ID", "LAT_DD", "LONG_DD"]).copy()
        # Remove exact duplicate coordinates for the same WR_ID.
        points_div = points_div.drop_duplicates(subset=["WR_ID", "LAT_DD", "LONG_DD"] ).copy()
        # Debug: how many diversion-point coordinates per WR_ID?
        point_counts = (points_div .groupby("WR_ID") .size() .reset_index(name="n_points") .sort_values("n_points", ascending=False))

        #print("\n[Diversion debug] diversion-point coordinates per WR_ID")
        #print(point_counts["n_points"].describe())
        #print(point_counts.head(20))
        # Choose ONE representative diversion point per WR_ID
        if "OBJECTID" in points_div.columns:
            points_div = points_div.sort_values(["WR_ID", "OBJECTID"])
        else:
            points_div = points_div.sort_values(["WR_ID", "LAT_DD", "LONG_DD"])

        points_one = (
            points_div
            .drop_duplicates(subset=["WR_ID"], keep="first")
            [["WR_ID", "LAT_DD", "LONG_DD"]]
            .copy()
        )

        print("\n[Diversion debug] one point per WR_ID")
        print("rows:", len(points_one))
        print("unique WR_ID:", points_one["WR_ID"].nunique())


        # aggregate same WR_ID/YEAR 
        #wr_merged = (wr_coord
            #.dropna(subset=["WR_ID"])
           # .groupby(["WR_ID", "YEAR", "LAT_DD", "LONG_DD"], as_index=False)
            #.sum(numeric_only=True)
        #)
        # Merge aggregated rights to one coordinate per WR_ID
        wr_coord = wr_merged.merge(points_one[["WR_ID", "LAT_DD", "LONG_DD"]], on="WR_ID", how="left")
        # spatial join to watershed registry
        watersheds = (self.ctx.registry.load_watersheds()[["WS_ID", "Estuary", "geometry"]])

        gdf_points = gpd.GeoDataFrame(
            wr_coord,
            geometry=gpd.points_from_xy(wr_coord["LONG_DD"], wr_coord["LAT_DD"],
            crs="EPSG:4269")
        )
        watersheds = watersheds.to_crs(gdf_points.crs)

        joined = gpd.sjoin(gdf_points, watersheds, how="left", predicate="intersects")
        joined = joined.dropna(subset=["WS_ID"]).copy()
        for c in MONTHLY_COLS:
            joined[c] = pd.to_numeric(joined[c], errors="coerce").fillna(0.0)

    
        for estuary, grp in joined.groupby("Estuary"):
            annual = (
                grp[MONTHLY_COLS]
                .sum(axis=1)
                .sum()
            )
            
            print("unique watersheds:", grp["WS_ID"].nunique())
            print("rows:", len(grp))


            ws_totals = (
                grp.assign(
                    annual_diversion=grp[MONTHLY_COLS].sum(axis=1)
                )
                [["WS_ID", "annual_diversion"]]
                .sort_values(
                    "annual_diversion",ascending=False)
            )

            print(ws_totals.head(10))
        #aggregate by watershed
        

        wsd_wr = (joined
            .drop(columns=["geometry", "LAT_DD", "LONG_DD"], errors="ignore")
            .groupby(["WS_ID", "Estuary", "YEAR"], as_index=False)
            .sum(numeric_only=True)
        )
        #debug
        grain_check = (
            wsd_wr
            .groupby(["WS_ID", "Estuary", "YEAR"])
            .size()
            .reset_index(name="n")
            .query("n > 1")
        )

        print("\n[Diversion debug] duplicate rows after WS_ID/Estuary/YEAR aggregation")
        print("duplicate grain rows:", len(grain_check))
        print(grain_check.head(20))
        debug = (joined[["WR_ID", "YEAR", "WS_ID", "Estuary", *MONTHLY_COLS, ]])
        debug.to_csv( "diversion_watershed_assignment.csv", index=False)

        # reshape to long
        df_long = wsd_wr.melt(
            id_vars=["WS_ID", "Estuary", "YEAR"],
            value_vars=MONTHLY_COLS,
            var_name="month_name",
            value_name="diversion"
        )
        df_long["month"] = df_long["month_name"].map(MONTH_MAP)

      


        monthly_long = (
            df_long[
            ["YEAR", "month", "Estuary", "WS_ID", "diversion"]
            ]
            .sort_values(
                ["Estuary", "WS_ID", "YEAR", "month"]
            )
        )

        monthly_long.to_csv(
            "diversion_monthly_by_watershed_long.csv",
           index=False
        )
        #debug_ws = "08010"
        #debug_year = 2024

        #tmp = wsd_wr.copy()

        #tmp["WS_ID"] = tmp["WS_ID"].astype(str).str.strip().str.zfill(5)
        #tmp["YEAR"] = pd.to_numeric(tmp["YEAR"], errors="coerce").astype("Int64")

        #check = tmp[(tmp["WS_ID"] == debug_ws) & (tmp["YEAR"] == debug_year)].copy()
        #print(f"\n[Diversion debug] Monthly diversion for WS_ID={debug_ws}, YEAR={debug_year}")

        month_lookup = {
            "JAN_DIV": "Jan",
            "FEB_DIV": "Feb",
            "MAR_DIV": "Mar",
            "APR_DIV": "Apr",
            "MAY_DIV": "May",
            "JUN_DIV": "Jun",
            "JUL_DIV": "Jul",
            "AUG_DIV": "Aug",
            "SEPT_DIV": "Sep",
            "OCT_DIV": "Oct",
            "NOV_DIV": "Nov",
            "DEC_DIV": "Dec",
        }

        #check_long = (check.melt(
            #id_vars=["WS_ID", "Estuary", "YEAR"],
            #value_vars=MONTHLY_COLS,
           # var_name="month_col",
            #value_name="diversion_acft"
        #)
                     #)

        #check_long["Month"] = check_long["month_col"].map(month_lookup)

        #check_long = check_long[
       # ["WS_ID", "Estuary", "YEAR", "Month", "diversion_acft"]]

        #print(check_long.to_string(index=False))

        #print("\nAnnual total:", check_long["diversion_acft"].sum()) 

        

        # expand monthly->daily by days-in-month 
        daily = expand_monthly_to_daily(
            df_long,
            year_col="YEAR", month_col="month", value_col="diversion", id_col="WS_ID", estuary_col="Estuary", value_is_monthly_total=True)
        
        if daily.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)


        # attach estuary from registry (single source of truth)
        #reg = pd.read_csv(self.ctx.ws_registry_path, dtype={"ws_id": str})
       # daily = daily.merge(reg[["ws_id","estuary"]], on="ws_id", how="left")

        daily["date"] = pd.to_datetime(daily["date"])

        daily["id"] = daily["WS_ID"]
        daily["id_type"] = "watershed"
        daily["estuary"] = daily["Estuary"]

        daily["component"] = self.name
        daily["source"] = "TCEQ"

        daily["value_afday"] = (daily["value"])

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


