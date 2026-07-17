#!/usr/bin/env python
# coding: utf-8

# In[ ]:
"""
Convert hourly processed rainfall rasters to daily watershed PCP records.

Outputs flat PCP files:

    <pcp_output_root>/05008.pcp
    <pcp_output_root>/05009.pcp
    ...

Example:
    python pcp_conversion.py ^
      --start 2026-07-01 ^
      --end 2026-07-31 ^
      --processed-tif-dir "Outputs/Processed TIF" ^
      --pcp-output-root "T:/TXRR_PCP" ^
      --append
"""


from __future__ import annotations

import argparse
import calendar
import re
from pathlib import Path
import geopandas as gpd
#import matplotlib.pyplot as plt
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
TWDB_watersheds_filepath = "T:/CoastalScience/Projects/Contracts/Active Contracts/2301792723_UTA_improved_precipitation_data/Deliverables/GageCorrector/pcp_conversion/TWDB_watersheds/TWDB_watersheds.shp"
gdf = gpd.read_file(TWDB_watersheds_filepath)
#ws_ids = list(gdf["WS_ID"])
gdf["WS_ID"] = gdf["WS_ID"].astype(str).str.zfill(5)

pcp_output_root = "PCP_Files"
append_mode = False


# Lookup dict: WS_ID -> estuary
ws_to_estuary = dict(zip(gdf["WS_ID"], gdf["Estuary"]))
#List of watershed IDs used downstream
ws_ids = gdf["WS_ID"].tolist()

radar_folder = "T:/CoastalScience/Projects/Contracts/Active Contracts/2301792723_UTA_improved_precipitation_data/Deliverables/GageCorrector/Outputs/Processed TIF"

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

def extract_record_date(line: str) -> str | None:
    """
    Extract a date key from a PCP line.

    Supports either:
      YYYY,MM,DD,...
      YYYYMMDD embedded anywhere
    """
    s = line.strip()
    if not s:
        return None

    # Case 1: YYYY,MM,DD,...
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 3:
        if parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
            y = int(parts[0])
            m = int(parts[1])
            d = int(parts[2])
            if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}{m:02d}{d:02d}"

    # Case 2: embedded YYYYMMDD
    match = re.search(r"\b(19\d{2}|20\d{2})(0[1-9]|1[0-2])([0-3]\d)\b", s)
    if match:
        return "".join(match.groups())

    return None
def parse_date(value: str, is_end: bool = False) -> datetime:
    value = str(value).strip()

    if re.fullmatch(r"\d{8}", value):
        dt = datetime.strptime(value, "%Y%m%d")
        return dt.replace(hour=23) if is_end else dt

    if re.fullmatch(r"\d{12}", value):
        return datetime.strptime(value, "%Y%m%d%H%M")

    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(hour=23) if is_end else dt
    except ValueError:
        pass

    raise ValueError(
        f"Invalid date format: {value}. Use YYYY-MM-DD, YYYYMMDD, or YYYYMMDDHHMM."
    )


def validate_month_block_range(start_date: datetime, end_date: datetime):
    """
    Existing PCP writer creates complete monthly blocks.
    Therefore start must be day 1 and end must be the last day of a month.
    """
    if start_date.day != 1:
        raise ValueError(
            f"--start must be the first day of a month. Got {start_date.date()}."
        )

    last_day = calendar.monthrange(end_date.year, end_date.month)[1]

    if end_date.day != last_day:
        raise ValueError(
            f"--end must be the last day of a month. Got {end_date.date()}."
        )


def load_watersheds(watersheds_path: str):
    """
    Minimal reload helper so --watersheds can work.
    """
    global TWDB_watersheds_filepath
    global gdf
    global ws_to_estuary
    global ws_ids

    TWDB_watersheds_filepath = watersheds_path

    gdf = gpd.read_file(TWDB_watersheds_filepath)
    gdf["WS_ID"] = gdf["WS_ID"].astype(str).str.zfill(5)

    if "Estuary" in gdf.columns:
        ws_to_estuary = dict(zip(gdf["WS_ID"], gdf["Estuary"]))
    else:
        ws_to_estuary = {}

    ws_ids = gdf["WS_ID"].tolist()


def month_key_from_pcp_header(line: str) -> str | None:
    """
    Parse monthly header generated by your existing write_pcp_header():

        "1" + WS_ID.rjust(8) + YYYY + MM.rjust(2) + days

    Returns YYYYMM if the line is a PCP monthly header.
    """
    if not line:
        return None

    if not line.startswith("1"):
        return None

    if len(line) < 15:
        return None

    year_txt = line[9:13].strip()
    month_txt = line[13:15].strip()

    if year_txt.isdigit() and month_txt.isdigit():
        year = int(year_txt)
        month = int(month_txt)

        if 1900 <= year <= 2100 and 1 <= month <= 12:
            return f"{year:04d}{month:02d}"

    return None


def existing_pcp_months(path: Path) -> set"""
    Find existing monthly PCP blocks in an existing PCP file.
    """
    if not path.exists():
        return set()

    months = set()

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            key = month_key_from_pcp_header(line.rstrip("\n"))
            if key:
                months.add(key)

    return months


def month_key_from_val_dict(val_dict: dict) -> str | None:
    """
    Determine YYYYMM from the first daily key in the block.
    """
    if not val_dict:
        return None

    first_key = sorted(val_dict.keys())[0]
    dt = datetime.strptime(str(first_key), "%Y%m%d")
    return dt.strftime("%Y%m")



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
        WS_ID = str(key).zfill(5)
        month_key = month_key_from_val_dic*(val_dict)
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

        
        
        #estuary = ws_to_estuary.get(WS_ID, "Unknown_Estuary")

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
            values = daily_aggregates[ws_id].get(date, [])
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
# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Aggregate processed hourly TIFs to flat TXRR PCP files.")

    parser.add_argument("--start", required=True, help="PCP start date. YYYY-MM-DD, YYYYMMDD, or YYYYMMDDHHMM.")
    parser.add_argument("--end", required=True, help="PCP end date. YYYY-MM-DD, YYYYMMDD, or YYYYMMDDHHMM.")
    parser.add_argument("--processed-tif-dir", default="Outputs/Processed TIF", help="Folder with Processed*.tif files.")
    parser.add_argument("--pcp-output-root", default="PCP_Files", help="Flat folder for output .pcp files.")
    parser.add_argument("--watersheds", default=TWDB_watersheds_filepath, help="TWDB watershed shapefile path.")
    parser.add_argument("--append", action="store_true", help="Append new monthly blocks and skip duplicates.")

    return parser.parse_args()


def main() -> None:
    global radar_folder, pcp_output_root, append_mode

    args = parse_args()

    start_date = parse_date(args.start, is_end=False)
    end_date = parse_date(args.end, is_end=True)

    if end_date < start_date:
        raise ValueError(f"--end must be after --start. Got {start_date} to {end_date}")

    validate_month_block_range(start_date, end_date)

    radar_folder = args.processed_tif_dir
    pcp_output_root = args.pcp_output_root
    append_mode = bool(args.append)

    load_watersheds(args.watersheds)
    os.makedirs(pcp_output_root, exist_ok=True)
    if not append_mode:
        # True overwrite mode: clear only the PCPs we will write.
        for ws_id in ws_ids:
            p = Path(pcp_output_root) / f"{str(ws_id).zfill(5)}.pcp"
            if p.exists():
                p.unlink()

    files_dict = generate_iterable_dictionary(start_date, end_date)
    aggregate_daily_vals(files_dict)



if __name__ == "__main__":
    main()






