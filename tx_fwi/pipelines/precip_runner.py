#!/usr/bin/env python

from __future__ import annotations

import argparse
import calendar
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


PYTHON_EXE = r"C:\Users\KKarimi\.conda\envs\mrms2\python.exe"

PROJECT_ROOT = Path(r"C:\Users\KKarimi\Documents\GageCorrector")

RAINFALL_PROCESSOR = PROJECT_ROOT / "rainfall_processor.py"
PCP_CONVERSION = PROJECT_ROOT / "pcp_conversion\pcp_conversion_ref.py"

SYNOPTIC_TOKEN = "1432cbdbed5647d1a5822907d98ffbee"

BOUNDARY_FILE = PROJECT_ROOT / r"Inputs\Boundary Shapefiles\AOI.shp"

# Keep this consistent with your corrected rainfall_processor buffer logic.
BUFFER_DISTANCE = "0.05"

PROCESSED_TIF_DIR = PROJECT_ROOT / r"Outputs\Processed TIF"

# Flat shared PCP folder.
PCP_SHARED_DIR = PROJECT_ROOT /(r"PCP_Files")

LOG_DIR = PCP_SHARED_DIR / "_monthly_logs"

PRE_BUFFER_DAYS = 8
POST_BUFFER_DAYS = 7


def previous_month_from_run_date(run_date: datetime) -> tuple[int, int]:
    first_this_month = datetime(run_date.year, run_date.month, 1)
    prev_month_end = first_this_month - timedelta(days=1)
    return prev_month_end.year, prev_month_end.month


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 0)
    return start, end


def yyyymmddhhmm(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M")


def run_command(cmd: list[str], cwd: Path, log_file: Path) -> None:
    print("Running:", " ".join(cmd))

    with log_file.open("a", encoding="utf-8") as log:
        log.write("\n\n========== COMMAND ==========\n")
        log.write(" ".join(cmd) + "\n")
        log.flush()

        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with return code {proc.returncode}: {' '.join(cmd)}")


def write_manifest(
    target_year: int,
    target_month: int,
    month_start: datetime,
    month_end: datetime,
    download_start: datetime,
    download_end: datetime,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    pcp_files = sorted(p.name for p in PCP_SHARED_DIR.glob("*.pcp"))

    manifest = {
        "target_year": target_year,
        "target_month": target_month,
        "pcp_start": month_start.strftime("%Y-%m-%d"),
        "pcp_end": month_end.strftime("%Y-%m-%d"),
        "download_start": download_start.strftime("%Y-%m-%dT%H:%M:%S"),
        "download_end": download_end.strftime("%Y-%m-%dT%H:%M:%S"),
        "pcp_folder": str(PCP_SHARED_DIR),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": "complete",
    }

    manifest_path = LOG_DIR / f"manifest_{target_year}_{target_month:02d}.json"

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def write_flag(target_year: int, target_month: int) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    flag = LOG_DIR / f"transfer_complete_{target_year}_{target_month:02d}.flag"

    flag.write_text(
        f"complete\n"
        f"target_year={target_year}\n"
        f"target_month={target_month}\n"
        f"created_at={datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--run-date", help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--skip-rainfall", action="store_true")
    parser.add_argument("--skip-pcp", action="store_true")
    args = parser.parse_args()

    run_date = (
        datetime.strptime(args.run_date, "%Y-%m-%d")
        if args.run_date
        else datetime.today()
    )

    if args.year and args.month:
        target_year, target_month = args.year, args.month
    else:
        target_year, target_month = previous_month_from_run_date(run_date)

    month_start, month_end = month_bounds(target_year, target_month)

    download_start = month_start - timedelta(days=PRE_BUFFER_DAYS)
    download_end = month_end + timedelta(days=POST_BUFFER_DAYS)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOG_DIR / f"monthly_windows_package_{target_year}_{target_month:02d}.log"

    print(f"Target month: {target_year}-{target_month:02d}")
    print(f"Download/process range: {download_start} to {download_end}")
    print(f"PCP append range: {month_start.date()} to {month_end.date()}")
    print(f"Flat PCP folder: {PCP_SHARED_DIR}")

    if not args.skip_rainfall:
        cmd = [
            PYTHON_EXE,
            str(RAINFALL_PROCESSOR),
            "-r",
            "-d",
            "-f",
            "-p",
            "-t",
            SYNOPTIC_TOKEN,
            "-s",
            yyyymmddhhmm(download_start),
            "-e",
            yyyymmddhhmm(download_end),
            "-b",
            str(BOUNDARY_FILE),
            "-g",
            str(BUFFER_DISTANCE),
        ]

        run_command(cmd, cwd=PROJECT_ROOT, log_file=log_file)

    if not args.skip_pcp:
        cmd = [
            PYTHON_EXE,
            str(PCP_CONVERSION),
            "--start",
            month_start.strftime("%Y-%m-%d"),
            "--end",
            month_end.strftime("%Y-%m-%d"),
            "--processed-tif-dir",
            str(PROCESSED_TIF_DIR),
            "--pcp-output-root",
            str(PCP_SHARED_DIR),
            "--append",
            "--flat-output",
        ]

        run_command(cmd, cwd=PROJECT_ROOT, log_file=log_file)

    write_manifest(
        target_year=target_year,
        target_month=target_month,
        month_start=month_start,
        month_end=month_end,
        download_start=download_start,
        download_end=download_end,
    )

    write_flag(target_year, target_month)

    print("Monthly Windows PCP workflow complete.")
    print(f"PCP files are in: {PCP_SHARED_DIR}")
    print(f"Logs are in: {LOG_DIR}")


if __name__ == "__main__":
    main()
