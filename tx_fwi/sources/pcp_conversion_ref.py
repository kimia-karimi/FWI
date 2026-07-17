#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from rasterstats import zonal_stats
import rasterio
import os
from datetime import datetime, timedelta
import calendar
from zoneinfo import ZoneInfo

#PCP_ROOT = "pcp_by_estuary"
#os.makedirs(PCP_ROOT, exist_ok=True)


# --- Load watersheds once for the whole module ---
TWDB_watersheds_filepath = "pcp_conversion/TWDB_watersheds/TWDB_watersheds.shp"
gdf = gpd.read_file(TWDB_watersheds_filepath)
#ws_ids = list(gdf["WS_ID"])
gdf["WS_ID"] = gdf["WS_ID"].astype(str).str.zfill(5)


# Lookup dict: WS_ID -> estuary
ws_to_estuary = dict(zip(gdf["WS_ID"], gdf["Estuary"]))
#List of watershed IDs used downstream
ws_ids = gdf["WS_ID"].tolist()
def plot_watershed(ws_id):
    TWDB_watersheds_filepath = "pcp_conversion/TWDB_watersheds/TWDB_watersheds.shp"
    gdf = gpd.read_file(TWDB_watersheds_filepath)
    ws_ids = list(gdf["WS_ID"])
    if ws_id not in gdf["WS_ID"].values:
        print(f"Watershed ID {ws_id} not found in the dataset.")
        return
    highlight_gdf = gdf[gdf["WS_ID"] == ws_id]
    fig, ax = plt.subplots(figsize=(10, 8))
    gdf.plot(ax=ax, edgecolor="black", facecolor="lightblue", alpha=0.5)
    highlight_gdf.plot(ax=ax, edgecolor="red", facecolor="blue", alpha=0.5, linewidth=2, label=f"Watershed {ws_id}")
    ax.set_title("TWDB Watersheds with Highlighted Watershed", fontsize=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    plt.grid(True)
    plt.savefig(ws_id)
    # plt.legend()
    plt.show()

#plot_watershed(ws_id=5008)


# In[ ]:


radar_folder = "Outputs/Processed TIF"
files = os.listdir(radar_folder)

# UTC Logic
# def return_24hr_files(dt):
#     dt = dt + timedelta(hours=1)
#     filenames = []
#     for i in range(24):
#         past_dt = dt + timedelta(hours=i)
#         filenames.append("Processed" + past_dt.strftime("%Y%m%d%H%M") + ".tif")
#     return filenames

# Local Logic
def return_24hr_files(dt):
    tz_local = ZoneInfo("America/Chicago")
    tz_utc = ZoneInfo("UTC")
    dt_local = dt.replace(tzinfo=tz_local)
    filenames = []
    for i in range(24):
        hour_local = dt_local + timedelta(hours=i)
        hour_utc = hour_local.astimezone(tz_utc)
        filenames.append("Processed" + hour_utc.strftime("%Y%m%d%H%M") + ".tif")
    return filenames

def generate_iterable_dictionary(start_date, end_date):
    my_files = {}
    while start_date <= end_date:
        my_files[start_date.strftime("%Y%m%d")] = return_24hr_files(start_date)
        start_date += timedelta(hours=24)
    return my_files

# files_dict = generate_iterable_dictionary(datetime(2015, 1, 1), datetime(2024, 10, 31))
files_dict = generate_iterable_dictionary(datetime(2026, 3, 1), datetime(2026, 6, 30))

def write_pcp(my_dict):
    for key, val_dict in my_dict.items():
        def return_spaces(no_spaces):
            space_str = ""
            for i in range(no_spaces):
                space_str += " "
            return space_str

        def concat_str(*args):
            return "".join(str(arg) for arg in args)

        def get_days_in_month(dt_):
                year = dt_.year
                month = dt_.month
                return calendar.monthrange(year, month)[1]

        def write_pcp_header(WS_ID, dt):
            return concat_str("1", WS_ID.rjust(8, " "), dt.strftime("%Y"), str(int(dt.strftime("%m"))).rjust(2, " "), get_days_in_month(dt))

        monthly_precip = 0
        monthly_pcp_entry = ""
        for dt_int, daily_agg_val in val_dict.items():
            monthly_precip += daily_agg_val
            dt = datetime.strptime(str(dt_int), "%Y%m%d")
            if dt.day == 1:
                monthly_pcp_entry += write_pcp_header(key, dt)
            if dt.day == 8:
                monthly_pcp_entry += concat_str("\n", "2")
            if dt.day == 17:
                monthly_pcp_entry += concat_str("\n", "3")
            if dt.day == 26:
                monthly_pcp_entry += concat_str("\n", "4")
            monthly_pcp_entry += f"{daily_agg_val:8.2f}"
            if dt.day == get_days_in_month(dt):
                final_day = dt.day
                while final_day != 31:
                    monthly_pcp_entry += "-9999.00"
                    final_day += 1
                monthly_pcp_entry += concat_str(f"{monthly_precip:8.2f}", "\n")
                monthly_precip = 0

        
        WS_ID = str(key).zfill(5)
        estuary = ws_to_estuary.get(WS_ID, "Unknown_Estuary")

        # Create estuary folder
        #estuary_dir = os.path.join("PCP_Files", estuary, "PCP")
        #os.makedirs(estuary_dir, exist_ok=True)

        #with open(os.path.join(estuary_dir, f"{WS_ID}.pcp"), "a") as file:
            #file.write(monthly_pcp_entry)
        with open(os.path.join("PCP_Files",str(key) + ".pcp"), "a") as file:
            file.write(monthly_pcp_entry)
            

def aggregate_daily_vals(files_):
    daily_aggregates = {ws_id: {} for ws_id in ws_ids}
    for date, tif_files in files_.items():
        print(f"Processing date: {date}")
        for tif_file in tif_files:
            tif_path = os.path.join(radar_folder, tif_file)
            if not os.path.exists(tif_path):
                print(f"File not found: {tif_path}")
                continue

            with rasterio.open(tif_path) as src:
                affine = src.transform
                raster = src.read(1)
                stats = zonal_stats(gdf, raster, affine=affine, stats=["mean"], nodata=src.nodata)
                for i, ws_id in enumerate(ws_ids):
                    mean_val = stats[i]["mean"]
                    if mean_val is not None:
                        if date not in daily_aggregates[ws_id]:
                            daily_aggregates[ws_id][date] = []
                        daily_aggregates[ws_id][date].append(mean_val)
                    else:
                        print("Mean Val None")
        for ws_id in daily_aggregates:
            values = daily_aggregates[ws_id][date]
            # if ws_id == 12001:
            #     print(daily_aggregates[12001])
            #     print(round(float(np.sum(values)), 2))
            daily_aggregates[ws_id][date] = round(float(np.sum(values)), 2)

        def get_days_in_month(dt_):
            year = dt_.year
            month = dt_.month
            return calendar.monthrange(year, month)[1]

        if datetime.strptime(str(date), "%Y%m%d").day == get_days_in_month(datetime.strptime(str(date), "%Y%m%d")):
            write_pcp(daily_aggregates)
            daily_aggregates = {ws_id: {} for ws_id in ws_ids}
    return


# In[40]:


aggregate_daily_vals(files_dict)


# In[ ]:




