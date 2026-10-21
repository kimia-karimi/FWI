# Modular Freshwater Inflow Automation

The `tx_fwi` package assembles daily freshwater-inflow and freshwater-balance components for Texas coastal estuaries. It separates external data retrieval, hydrologic processing, spatial attribution, registry metadata, and storage so each component can be updated and audited independently.

## Workflow coverage

| Component | Primary input | Output role |
|---|---|---|
| Local gaged flow | USGS, IBWC, Lake Houston, Lake Texana/LNRA, Colorado adjustments | `direct` or `adjusted` |
| Upstream/system gaged flow | Upstream USGS or IBWC definitions | `system_counted` |
| TxRR / model flow | TxRR or HEC-HMS2D output | `direct` |
| Diversions | TCEQ water-right records and diversion points | `removal` |
| Return flow | EPA DMR records and TCEQ wastewater outfalls | `return` |
| Precipitation | WDFT watercycle precipitation by quad | `addition` |
| Evaporation | WDFT watercycle gross evaporation by quad | `loss` |

## Processing model

```text
Sources -> Components -> Shared transforms -> Spatial attribution
        -> QA/QC -> Canonical daily timeseries -> Monthly/bay products
```

All component outputs use the canonical schema:

```text
date, id, id_type, estuary, component, source,
value_afday, flow_role, count_in_basin_sum, data_as_of, note
```

## Setup

```bash
conda env create -f environment.yml
conda activate fwi
```

To update an existing environment:

```bash
conda env update -f environment.yml
```

Review `tx_fwi/config/config.py` and registry inputs before running. Confirm source paths, output paths, credentials, watershed data, upstream-gage definitions, and quad crosswalks.

## Running the pipeline

Run enabled components:

```bash
python -m tx_fwi.pipelines.runner
```

Run a date range:

```bash
python -m tx_fwi.pipelines.runner --start 2024-01-01 --end 2024-12-31
```

Component watermarks support incremental processing where implemented. Historical rebuilds should be used when source corrections, watershed boundaries, attribution rules, or component methodology change.

## Automatic workflow and frequency

The production scheduler should call the pipeline entry point, capture logs, preserve QA/QC outputs, and fail the run when required sources or validation checks fail.

| Activity | Recommended trigger or frequency | User interaction |
|---|---|---|
| Incremental component update | Scheduled after source data are expected to be available | Review failures and unresolved records |
| Precipitation and evaporation update | After a new WDFT watercycle file is available | Verify the file period, quad coverage, and units |
| TxRR/model update | After approved model output is delivered | Confirm model version, simulation period, watershed identifiers, and replacement scope |
| Monthly/bay aggregation | After component updates complete successfully | Review monthly completeness and comparison reports |
| Historical rebuild | On approved methodology, registry, or source correction | Document the reason, date range, reviewer, and acceptance decision |

The exact schedule is deployment-specific. Record the configured schedule here once the production scheduler is finalized:

- **Precipitation/model ingestion:** `[Monthly scheduled task on sw-lnx-18947]`
- **Incremental pipeline:** `[Monthly scheduled task, except for diversion and return]`
- **Precipitation/evaporation ingestion:** `[Annual quad data from WDfT]`

## User interaction points

Human review is expected at controlled boundaries rather than inside routine transformations:

1. **Configuration:** maintain paths, credentials, enabled components, and output settings.
2. **Registry maintenance:** approve watershed, estuary, upstream-gage, station, and quad-crosswalk changes.
3. **Source intake:** confirm precipitation files and TxRR/model deliveries are complete and authoritative.
4. **Spatial exceptions:** review unmatched points, overlapping watershed candidates, cross-estuary conflicts, and approved overrides.
5. **QA/QC:** inspect missing periods, duplicate records, unit conversions, volume conservation, and changes from the previous approved run.
6. **Release:** approve historical replacements and publication of downstream products.

## Design rules

- External API, download, and parsing logic belongs in `tx_fwi/sources`.
- Component orchestration belongs in `tx_fwi/components`.
- Shared unit, temporal, and spatial logic belongs in `tx_fwi/transforms`.
- Watershed, estuary, upstream-gage, station, and quad relationships belong in the registry.
- Components should return the canonical schema and avoid hardcoded reference-data paths.
- Intermediate files should be limited to intentional QA/QC or approved exports.
- System/upstream gages are counted once at the system or estuary level, not repeated across downstream watersheds.

