# tx_fwi/components/base.py
from __future__ import annotations


from typing import Callable, Dict, Tuple


SourceKey = Tuple[str | None, str | None]


SourceKey = Tuple[str | None, str | None]


class SourceRegistry:
    """
    Central registry for all time series sources.
    """

    def __init__(self):
        self._registry: Dict[SourceKey, Callable] = {}

    def register(self, source: str | None, special: str | None):
        def decorator(func: Callable):
            key = (source, special)
            self._registry[key] = func
            return func
        return decorator

    def get(self, source: str | None, special: str | None):
        key = (source, special)

        # priority order
        if key in self._registry:
            return self._registry[key]

        fallback = (source, None)
        if fallback in self._registry:
            return self._registry[fallback]

        raise KeyError(f"No handler for source={source}, special={special}")


# ✅ global singleton
registry = SourceRegistry()
``
