# tx_fwi/pipelines/runner_example.py
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.components.diversion import DiversionComponent
from tx_fwi.components.gaged import LocalGagedComponent
from tx_fwi.components.gaged_upstream import UpstreamGagedComponent
from tx_fwi.components.returns import ReturnComponent
from tx_fwi.components.ungaged import UngagedComponent
from tx_fwi.qaqc.manager import RunManager

# Change these imports if your repository uses different
# configuration, storage, or registry constructors.
#from tx_fwi.config import Config
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
            "with QA/QC logging."
        )
    )

    parser.add_argument(
        "--start",
        required=True,
        help="Requested start date, such as 2015-01-01.",
    )

    parser.add_argument(
        "--end",
        required=True,
        help="Requested end date, such as 2015-02-28.",
    )

    parser.add_argument(
        "--log-root",
        default=None,
        help=(
            "Optional QA/QC log directory. "
            "Overrides config.LOG_ROOT."
        ),
    )

    return parser.parse_args()


def run_pipeline(
    *,
    start,
    end,
    config,
    storage,
    registry,
    log_root,
) -> int:
    manager = RunManager(
        log_root=log_root,
        requested_start=start,
        requested_end=end,
        repository_version=git_version(),
        environment=getattr(
            config,
            "ENVIRONMENT_NAME",
            None,
        ),
    )

    ctx = RunContext(
        storage=storage,
        registry=registry,
        config=config,
        logger=manager.logger,
        run_manager=manager,
    )

    manager.logger.info(
        "Starting FWI run %s through %s",
        pd.Timestamp(start).date(),
        pd.Timestamp(end).date(),
    )

    components = [
        LocalGagedComponent(ctx),
        UpstreamGagedComponent(ctx),
        UngagedComponent(ctx),
        DiversionComponent(ctx),
        ReturnComponent(ctx),
    ]

    exit_code = 0

    try:
        for component in components:
            manager.logger.info(
                "Starting component=%s",
                component.name,
            )

            component.run(
                start=start,
                end=end,
            )

        manifest = manager.finalize()

        if manifest.status == "FAIL":
            exit_code = 1

    except Exception:
        manager.logger.exception(
            "Pipeline failed with an unhandled error."
        )

        exit_code = 1

    finally:
        manifest = manager.finalize()

        print()
        print("Pipeline status:", manifest.status)
        print("Run directory:", manager.run_dir)
        print(
            "Summary:",
            manager.run_dir / "run_summary.csv",
        )
        print(
            "Manifest:",
            manager.run_dir / "run_manifest.json",
        )
        print(
            "Detailed log:",
            manager.run_dir / "run.log",
        )

    return exit_code


def main() -> int:
    args = parse_args()

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    if end < start:
        raise ValueError(
            "--end cannot be earlier than --start."
        )

    config = Config()

    log_root = Path(
        args.log_root
        or getattr(
            config,
            "LOG_ROOT",
            "logs",
        )
    )

    log_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    storage = Storage(
        config=config,
    )

    registry = Registry(
        config=config,
    )

    print("Requested start:", start)
    print("Requested end:", end)
    print(
        "QA/QC log root:",
        log_root.resolve(),
    )

    return run_pipeline(
        start=start,
        end=end,
        config=config,
        storage=storage,
        registry=registry,
        log_root=log_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
