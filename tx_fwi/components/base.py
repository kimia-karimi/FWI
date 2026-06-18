# base.py
class RunContext:
    def __init__(self, storage, registry, upstream):
        self.storage = storage
        self.registry = registry
        self.upstream = upstream
