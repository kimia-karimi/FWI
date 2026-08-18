# Modular Freshwater Inflow Automation (FWI)

## Overview

The Modular Freshwater Inflow Automation (FWI) platform generates freshwater inflow datasets for Texas estuaries using a component-based architecture.

The system retrieves source data from multiple providers, standardizes records into a common schema, assigns flows to watersheds, performs QA/QC checks, and produces daily inflow datasets that can be used for reporting, dashboards, Snowflake integration, and other downstream products.

The platform is designed to be:

- Modular
- Reproducible
- Auditable
- Registry-driven
- Incremental
- Easy to maintain

---

# Repository Structure

```text
tx_fwi/
│
├── components/
│   ├── base.py
│   ├── gaged.py
│   ├── gaged_upstream.py
│   ├── diversion.py
│   ├── returns.py
│   ├── ungaged.py
│   └── evap_precip.py
│
├── sources/
│   ├── base.py
│   ├── usgs.py
│   ├── epa_dmr.py
│   ├── tceq_wr.py
│   ├── colorado.py
│   ├── lnra.py
│   ├── ibwc.py
│   ├── models.py
│   └── usgs_utils.py
│
├── transforms/
│   ├── spatial.py
│   ├── temporal.py
│   ├── pcp_conversion.py
│   └── units.py
│
├── registry/
│   ├── watersheds
│   ├── registry.py
│   ├── upstream network definitions
│   └── metadata registries
│
├── pipelines/
│   ├── runner.py
│   ├── run_update.py
│   ├── wdft_watercycle.py
│   ├── calculate_monthly_bay_flow.py
│   └── precip_runner.py
│
├── tools/
│   ├── update_txrr_in_files.py
│   └── timeseries_generator.py
│
├── config/
│   └── config.py
├── storage.py
│
└── data/
    ├── watersheds/
    └── supporting reference data
```

---

# Component Architecture

A component represents a freshwater inflow process.

Examples:

| Component | Purpose |
|------------|------------|
| gaged | USGS streamflow, IBWC streamflow, Special gages (Lake Texana, Lake Houston, Colorado River), Upstream streamflow |
| diversion | Water right diversions |
| returns | Wastewater return flows |
| ungaged | model-generated ungaged flows |
| evap_precip | Bay precipitation and evaporation |

All components produce the same output schema.

```text
date
id
id_type
estuary
component
source
value_afday
flow_role
count_in_basin_sum
note
```

---

# Source Architecture

Sources retrieve raw data from external systems.

Examples:

| Source | Provider |
|----------|----------|
| usgs.py | USGS NWIS |
| tceq_wr.py | TCEQ Water Rights |
| epa_dmr.py | EPA DMR |
| wdft_watercycle.py | WDFT |
| models.py | Hydrologic models |
| lnra.py | Lake Texana |
| lake_houston.py | Lake Houston |
| colorado.py | Colorado adjustment |

Sources should only retrieve records.

Business logic belongs in the component.

---

# Environment Setup

## Create Environment

```bash
conda env create -f environment.yml
```

or

```bash
conda env update -f environment.yml
```

Activate:

```bash
conda activate fwi
```

---

# Required Python Packages

Typical dependencies include:

```text
pandas
geopandas
numpy
shapely
pyproj
requests
fiona
pyarrow
openpyxl
certifi
```

The authoritative dependency list is maintained in:

```text
environment.yml
```

---

# Configuration

Review configuration settings before running:

```text
tx_fwi/config/config.py
```

Verify:

- Local paths
- Network paths
- Registry locations
- Output locations
- Data directories

---

# Running the Workflow

Run all enabled components:

```bash
python -m tx_fwi.runner
```

Run a specific date range:

```bash
python -m tx_fwi.runner \
    --start 2024-01-01 \
    --end 2024-12-31
```

Incremental processing uses component watermarks when supported.

---

# Data Flow

```text
Source
   ↓

Component
   ↓

Transforms
   ↓

Watershed Attribution
   ↓

QA/QC
   ↓

Daily Component Dataset
   ↓

Monthly Aggregations
   ↓

Products
```

---

# Registry-Driven Design

Relationship definitions are stored in registry files rather than hardcoded throughout the codebase.

Examples:

- Watershed boundaries
- Upstream gage relationships
- Station mappings
- Estuary mappings
- Attribution rules

Updating registries is preferred over modifying component code whenever possible.

---

# Adding a New Component

1. Create a new component:

```text
tx_fwi/components/
```

2. Create or reuse a source adapter:

```text
tx_fwi/sources/
```

3. Convert outputs to the standard FWI schema.

4. Register the component within the runner.

5. Verify QA/QC results.

---

# QA/QC Outputs

The workflow generates debugging outputs where necessary, such as:

```text
join diagnostics
monthly summaries
watershed assignment checks
```

Review these outputs when validating new code or source updates.

---

# Common Maintenance Tasks

## Update Watershed Registry

Update metadata in:

```text
registry/
```

Re-run validation.

---

## Rebuild Historical Data

Use only when:

- Source corrections occur
- Attribution rules change
- Watershed definitions change
- Component logic changes

Full rebuilds should be documented and validated before replacing official outputs.

---
