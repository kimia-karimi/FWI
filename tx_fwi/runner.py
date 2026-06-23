# tx_fwi/runner.py
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from tx_fwi.storage import Storage
from tx_fwi.registry import Registry
from tx_fwi.components.base import RunContext

# Components
from tx_fwi.components.gaged import LocalGagedComponent
#from tx_fwi.components.gaged_upstream import UpstreamGagedComponent


# ------------------------------------------------------------------
# DEFAULT PATHS (UNC-friendly)
# ------------------------------------------------------------------
DEFAULT_ROOT = Path(r"T:\CoastalScience\Data\Hydrology\fwi_master")

DEFAULT_WATERSHED_SHP = (
    DEFAULT_ROOT / "watersheds" / "watersheds_registry.shp"
)

DEFAULT_UPSTREAM_JSON = (
    DEFAULT_ROOT / "upstream_gages.json"
)
print(Path(DEFAULT_WATERSHED_SHP).exists())

# ------------------------------------------------------------------
# CONTEXT BUILDER
# ------------------------------------------------------------------
def build_context(
    root: Path = DEFAULT_ROOT,
    watershed_shp: Path = DEFAULT_WATERSHED_SHP,
    upstream_json: Path = DEFAULT_UPSTREAM_JSON,
) -> RunContext:
    """
    Initialize storage + registry and wrap in RunContext
    """
    storage = Storage(root=root)

    registry = Registry(
        watershed_shp=watershed_shp,
        upstream_gages_json=upstream_json,
    )

    return RunContext(
        storage=storage,
        registry=registry,
    )


# ------------------------------------------------------------------
# MAIN PIPELINE
# ------------------------------------------------------------------
def run_all(start: str | None = None, end: str | None = None):
    """
    Run full update pipeline.

    Parameters
    ----------
    start : optional string date
    end : optional string date
    """

    print("Initializing context...")
    ctx = build_context()

    start_dt = pd.to_datetime(start).normalize() if start else None
    end_dt = pd.to_datetime(end).normalize() if end else None

    print(f"Start: {start_dt}")
    print(f"End: {end_dt}")

    # --------------------------------------------------------------
    # Component 1: Local gaged (watershed-level)
    # --------------------------------------------------------------
    print("\nRunning local gaged component...")

    comp_local = LocalGagedComponent(ctx)
    n_local = comp_local.run(start=start_dt, end=end_dt)

    print(f"Local gaged rows written: {n_local:,}")

    # --------------------------------------------------------------
    # Component 2: Upstream gaged (system-level)
    # --------------------------------------------------------------
    print("\nRunning upstream gaged component...")

    comp_upstream = UpstreamGagedComponent(ctx)
    n_upstream = comp_upstream.run(start=start_dt, end=end_dt)

    print(f"Upstream gaged rows written: {n_upstream:,}")

    print("\nPipeline complete.")


# ------------------------------------------------------------------
# CLI ENTRYPOINT
# ------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Run TX FWI update pipeline"
    )

    parser.add_argument(
        "--start",
        help="Start date (YYYY-MM-DD)",
        default=None,
    )

    parser.add_argument(
        "--end",
        help="End date (YYYY-MM-DD)",
        default=None,
    )

    args = parser.parse_args()

    run_all(start=args.start, end=args.end)


# ------------------------------------------------------------------
if __name__ == "__main__":
    main()