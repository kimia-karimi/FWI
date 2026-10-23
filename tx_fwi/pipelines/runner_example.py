from __future__ import annotations

import argparse
import subprocess

import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.qaqc.manager import RunManager


def git_version() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return None


def run_pipeline(*, start, end, config, storage, registry, components) -> int:
    manager = RunManager(
        log_root=config.LOG_ROOT,
        requested_start=start,
        requested_end=end,
        repository_version=git_version(),
        environment=getattr(config, "ENVIRONMENT_NAME", None),
    )
    ctx = RunContext(
        storage=storage,
        registry=registry,
        config=config,
        logger=manager.logger,
        run_manager=manager,
    )

    manager.logger.info("Starting FWI run %s through %s", pd.Timestamp(start).date(), pd.Timestamp(end).date())
    try:
        for component_class in components:
            component_class(ctx).run(start=start, end=end)
        manifest = manager.finalize()
        return 0 if manifest.status in {"PASS", "WARN"} else 1
    except Exception:
        manager.logger.exception("Pipeline failed.")
        return 1
    finally:
        manager.finalize()
