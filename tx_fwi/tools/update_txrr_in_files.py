#!/usr/bin/env python
"""
update_txrr_in_files.py

Linux-side updater for TXRR watershed .in files.

Updates:
1. Line 1 PCP path to the global flat PCP folder.
2. Line 4:
     WSID,start_year,num_years,...
3. Line 7:
     0,start_year,start_month,end_year,end_month,...

The end year/month are taken from manifest by default.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--txrr-root", required=True)
    parser.add_argument("--global-pcp-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--start-month", type=int, default=1)
    return parser.parse_args()


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_ws_id_from_line4(line: str) -> str | None:
    parts = [p.strip() for p in line.split(",")]
    if not parts:
        return None

    raw = parts[0]

    if not raw.isdigit():
        return None

    return raw.zfill(5)


def infer_ws_id_from_existing_pcp_path(line: str) -> str | None:
    name = Path(line.strip()).name
    match = re.match(r"(\d+)\.pcp$", name, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).zfill(5)


def update_one_in_file(
    in_path: Path,
    global_pcp_dir: Path,
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> bool:
    lines = in_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    if len(lines) < 7:
        print(f"SKIP malformed .in file: {in_path}")
        return False

    ws_id = infer_ws_id_from_line4(lines[3])

    if ws_id is None:
        ws_id = infer_ws_id_from_existing_pcp_path(lines[0])

    if ws_id is None:
        print(f"SKIP could not infer watershed id: {in_path}")
        return False

    pcp_path = global_pcp_dir / f"{ws_id}.pcp"

    if not pcp_path.exists():
        print(f"WARNING PCP missing for {ws_id}: {pcp_path}")
        print(f"        File still updated, but TXRR may fail if PCP is required.")

    # Line 1: flat global PCP location
    lines[0] = str(pcp_path)

    # Line 4: WSID,start_year,num_years,...
    line4 = [p.strip() for p in lines[3].split(",")]

    if len(line4) < 3:
        print(f"SKIP malformed line 4 in: {in_path}")
        return False

    num_years = end_year - start_year + 1

    # Preserve original WSID formatting style in line 4.
    # If it was 5008, keep 5008. If 05008, keep 05008.
    line4[1] = str(start_year)
    line4[2] = str(num_years)

    lines[3] = ",".join(line4)

    # Line 7: 0,start_year,start_month,end_year,end_month,...
    control = [p.strip() for p in lines[6].split(",")]

    if len(control) < 5:
        print(f"SKIP malformed control line in: {in_path}")
        return False

    control[1] = str(start_year)
    control[2] = str(start_month)
    control[3] = str(end_year)
    control[4] = str(end_month)

    lines[6] = ",".join(control)

    backup = in_path.with_suffix(in_path.suffix + ".bak")
    if not backup.exists():
        backup.write_text("\n".join(lines) + "\n", encoding="utf-8")

    in_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"UPDATED {in_path} | ws_id={ws_id} | "
        f"end={end_year}-{end_month:02d} | num_years={num_years}"
    )

    return True


def main():
    args = parse_args()

    txrr_root = Path(args.txrr_root)
    global_pcp_dir = Path(args.global_pcp_dir)

    manifest = load_manifest(Path(args.manifest))

    end_year = int(manifest["target_year"])
    end_month = int(manifest["target_month"])

   

    if not global_pcp_dir.exists():
        raise FileNotFoundError(f"Global PCP dir not found: {global_pcp_dir}")

    in_files = sorted(txrr_root.rglob("*.in"))

    # Avoid editing backups.
    in_files = [p for p in in_files if not p.name.endswith(".bak")]

    if not in_files:
        raise RuntimeError(f"No .in files found under {txrr_root}")

    print(f"Found {len(in_files)} .in files under {txrr_root}")
    print(f"Using global PCP dir: {global_pcp_dir}")
    print(f"Simulation end: {end_year}-{end_month:02d}")

    updated = 0

    for in_file in in_files:
        if update_one_in_file(
            in_path=in_file,
            global_pcp_dir=global_pcp_dir,
            start_year=args.start_year,
            start_month=args.start_month,
            end_year=end_year,
            end_month=end_month,
        ):
            updated += 1

    print(f"Updated {updated}/{len(in_files)} .in files")


if __name__ == "__main__":
    main()
