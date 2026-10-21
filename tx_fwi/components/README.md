# Components

Component modules orchestrate one freshwater-inflow or freshwater-balance process. A component combines source data, registry metadata, and shared transforms, then returns records in the canonical daily schema.

## Expected interface

```python
class ExampleComponent:
    name = "example"

    def build(self, start, end):
        """Return a canonical DataFrame without writing final storage."""

    def run(self, start=None, end=None):
        """Resolve the run period, append output, and update watermarks."""
```

## Main responsibilities

- Select and validate records needed by the component.
- Apply component-specific hydrologic logic.
- Call shared unit, temporal, and spatial transforms.
- Produce the canonical schema.
- Write concise operational logs and intentional QA/QC diagnostics.

## Major components

- Local and adjusted gaged flow
- Upstream/system gaged flow
- Diversions
- EPA DMR return flow
- TxRR or HEC-HMS2D ungaged/model flow
- Precipitation and evaporation

## User interaction

Review component-specific unresolved records, approved overrides, missing periods, and methodology changes. Do not place API credentials, download logic, or registry definitions in component modules.
