from dataclasses import dataclass

from tx_fwi.storage import Storage
from tx_fwi.registry import Registry


@dataclass
class RunContext:
    storage: Storage
    registry: Registry
