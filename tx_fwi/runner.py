# tx_fwi/runner.py
from __future__ import annotations

from pathlib import_fwi.storage import Storagefrom pathlib import Path
from tx_fwi.registry import Registry
from tx_fwi.components.base import RunContext
from tx_fwi.components.gaged_local import LocalGagedComponent
from tx_fwi.components.gaged_upstream import UpstreamGagedComponent


DEFAULT_ROOT = Path(r"\\fileserver\CoastalScience\Data\Hydrology\fwi_master")
DEFAULT_WATERSHED_SHP = DEFAULT_ROOT / "coastal_watersheds_registry.shp"
DEFAULT_UPSTREAM_JSON = DEFAULT_ROOT / "upstream_gages.json"


def build_context(
    root: Path = DEFAULT_ROOT,
    watershed_shp: Path = DEFAULT_WATERSHED_SHP,
    upstream_json: Path = DEFAULT_UPSTREAM_JSON,
) -> RunContext:
    storage = Storage(root=root)
    registry = Registry(
        watershed_shp=watershed_shp,
        upstream_gages_json=upstream_json,
    )
    return RunContext(storage=storage, registry=registry)


def run_all(start: str | None = None, end: str | None = None) -> None:
    ctx = build_context()

    start_dt = pd.to_datetime(start).normalize() if start else None
    end_dt = pd.to_datetime(end).normalize() if end else None

    n_local = LocalGagedComponent(ctx).run(start=start_dt, end=end_dt)
    print(f"Local gaged rows written: {n_local:,}")

    n_upstream = UpstreamGagedComponent(ctx).run(start=start_dt, end=end_dt)
    print(f"Upstream gaged rows written: {n_upstream:,}")


def main():
    parser = argparse.ArgumentParser(description="Run FWI update pipeline.")
    parser.add_argument("--start", default=None, help="Optional start date, e.g. 2024-01-01")
    parser.add_argument("--end", default=None, help="Optional end date, e.g. 2024-01-31")

    args = parser.parse_args()

    run_all(start=args.start, end=args.end)


if __name__ == "__main__":
    main()
import argparse
import pandas as pd

