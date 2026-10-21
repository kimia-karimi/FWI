# Transforms

Transform modules contain reusable, source-independent processing logic.

## Scope

- `units.py`: conversions such as cfs, cubic metres per second, MGD, inches over acres, and daily acre-feet.
- `temporal.py`: monthly-to-daily expansion and date alignment.
- `spatial.py`: point construction, geometry fallback, watershed assignment, overlap handling, and unresolved spatial audits.
- Precipitation conversion helpers: quad-area weighting and inches-to-acre-feet calculations.

## Rules

- Keep transforms deterministic and side-effect free.
- Require explicit units and value semantics.
- Distinguish monthly totals from monthly average rates.
- Do not embed source URLs, registry paths, or component names.
- Add tests for boundary cases, missing values, duplicates, and conservation checks.

## User interaction

Changes to shared transforms can affect several components. Document the reason, affected periods, regression results, and whether a historical rebuild is required.
