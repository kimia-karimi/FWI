# Registry

The registry is the controlled source of spatial and relationship metadata used across the pipeline.

## Managed relationships

- Watershed identifiers, geometries, estuaries, and inclusion flags
- Upstream/system gage definitions
- Station and special-gage mappings
- Quad-to-watershed crosswalks with `WS_ID`, quad, estuary, and `area_acres`
- Reviewed spatial or identifier overrides

## Rules

- Access registry data through registry methods rather than direct component file reads.
- Use one row per watershed/quad/area relationship for multi-quad watersheds.
- Validate polygon geometry, coordinate reference systems, overlaps, gaps, and duplicate IDs.
- Version and document approved registry changes.

## User interaction

Registry edits require review because changes can move flow between watersheds or estuaries. Record the editor, reason, effective date, affected identifiers, and validation result.
