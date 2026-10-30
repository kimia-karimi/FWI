from __future__ import annotations
from dataclasses import dataclass

from tx_fwi.storage import Storage
from tx_fwi.registry.registry import Registry
from typing import Any


@dataclass
class RunContext:
    storage: Any
    registry: Any
    config: Any = None
    logger: logging.Logger | None = None
    run_manager: Any = None

    def component_logger(
        self,
        component: str,
    )-> logging.Logger:
        if self.logger is not None:
            return self.logger.getChild(
            component
            )
        return logging.getLogger(
            f"tx_fwi.{component_name}"
        )


