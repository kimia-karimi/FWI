from pathlib import Path
from tx_fwi.storage import Storage
from tx_fwi.components.base import RunContext
from tx_fwi.components.gaged_usgs import USGSGaged
from tx_fwi.components.diversion import Diversion

ROOT = Path(r"T:\CoastalScience\Data\Hydrology\fwi_master\data")
WS_REGISTRY = str(Path(r"T:\CoastalScience\Data\Hydrology\fwi_master\ws_registry.csv"))

def main():
    storage = Storage(ROOT)
    ctx = RunContext(storage=storage, ws_registry_path=WS_REGISTRY)

    # USGS (frequent)
    n1 = USGSGaged(ctx).run()
    print(f"USGS rows written: {n1}")

    # Diversion (infrequent)
    n2 = Diversion(ctx).run()
    print(f"Diversion rows written: {n2}")

if __name__ == "__main__":
    main()
