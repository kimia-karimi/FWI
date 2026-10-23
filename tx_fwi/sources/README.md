# Sources

Source modules retrieve and parse external data. They should return pandas or GeoPandas objects and avoid applying freshwater-inflow business rules.

## Responsibilities

- Construct requests and source-specific queries.
- Handle authentication, certificates, pagination, archives, and file parsing.
- Normalize source response structure enough for component use.
- Validate required fields and incomplete downloads.
- Return DataFrames, GeoDataFrames, or Series without writing final products.

## Primary source families

- USGS NWIS
- IBWC/Aquarius exports
- EPA DMR
- TCEQ water rights and wastewater outfalls
- WDFT precipitation and evaporation files
- TxRR or HEC-HMS2D model output
- Lake Houston, Lake Texana/LNRA, and Colorado special sources

## User interaction

Update source URLs, credentials, approved file locations, and parsers when provider formats change. Confirm that new precipitation and model files cover the intended period before processing.
