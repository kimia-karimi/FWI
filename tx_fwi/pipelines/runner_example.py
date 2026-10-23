# tx_fwi/pipelines/runner_example.py
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.components.gaged import LocalGagedComponent
from tx_fwi.components.gaged_upstream import UpstreamGagedComponent
from tx_fwi.qaqc.manager import RunManager
from tx_fwi.registry.registry import Registry
from tx_fwi.storage import Storage


def git_version() -> str | None:
    try:
        return subprocess.check_output(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            text=True,
        ).strip()
    except Exception:
        return None


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Run the freshwater-inflow pipeline "
            "with QA/QC reporting."
        )
    )

    parser.add_argument(
        "--start",
        required=True,
    )

    parser.add_argument(
        "--end",
        required=True,
    )

    parser.add_argument(
        "--log-root",
        default="logs",
    )
    parser.add_argument(
        "--storage-root",
        default=r"T:\CoastalScience\Data\Hydrology\fwi_master",
        help=(
            "Root directory containing the canonical timeseries "
            "and component watermarks."
        ),
        )
    parser.add_argument(
        "--watershed-shp",
        required=True,
        help=(
            "Path to the watershed shapefile used by the "
            "freshwater-inflow registry."
        ),
    )  
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    if end < start:
        raise ValueError(
            "--end cannot be earlier than --start."
        )

    log_root = Path(args.log_root).resolve()

    log_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Use the same constructors as the existing production runner.
    #storage = Storage()
    storage_root = Path(
        args.storage_root
    ).resolve()

    storage_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    storage = Storage(
        root=storage_root,
    )
    watershed_shp = Path(args.watershed_shp).resolve()

    if not watershed_shp.exists():
        raise FileNotFoundError(
            "Watershed shapefile was not found: "
            f"{watershed_shp}"
        )

    if watershed_shp.suffix.lower() != ".shp":
        raise ValueError(
            "--watershed-shp must point to a .shp file. "
            f"Received: {watershed_shp}"
        )


    registry = Registry(watershed_shp=watershed_shp)

    run_manager = RunManager(
        log_root=log_root,
        requested_start=start,
        requested_end=end,
        repository_version=git_version(),
        environment=None,
    )

    ctx = RunContext(
        storage=storage,
        registry=registry,
        config=None,
        logger=run_manager.logger,
        run_manager=run_manager,
    )

    components = [
        LocalGagedComponent(ctx),
        UpstreamGagedComponent(ctx),
    ]

    exit_code = 0

    run_manager.logger.info(
        "Starting FWI run %s through %s",
        start.date(),
        end.date(),
    )

    try:
        for component in components:
            run_manager.logger.info(
                "Starting component=%s",
                component.name,
            )

            component.run(
                start=start,
                end=end,
            )

        manifest = run_manager.finalize()

        if manifest.status == "FAIL":
            exit_code = 1

    except Exception:
        run_manager.logger.exception(
            "Pipeline failed with an "
            "unhandled exception."
        )

        exit_code = 1

    finally:
        manifest = run_manager.finalize()

        print()
        print(
            "Pipeline status:",
            manifest.status,
        )
        print(
            "Run directory:",
            run_manager.run_dir.resolve(),
        )
        print(
            "Run log:",
            (
                run_manager.run_dir
                / "run.log"
            ).resolve(),
        )
        print(
            "Run summary:",
            (
                run_manager.run_dir
                / "run_summary.csv"
            ).resolve(),
        )
        print(
            "Run manifest:",
            (
                run_manager.run_dir
                / "run_manifest.json"
            ).resolve(),
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
