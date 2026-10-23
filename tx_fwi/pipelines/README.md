# Pipelines

Pipeline modules coordinate component execution and downstream products.

## Responsibilities

- Parse run dates and command-line options.
- Build the shared run context.
- Execute enabled components in the approved order.
- Stop or report failures clearly.
- Trigger downstream monthly or bay-level products only after component success.
- Support scheduled and manual runs through the same entry points.

## Automatic workflow

A production scheduler should:

1. Start the pipeline with an explicit or watermark-derived period.
2. Capture logs and run metadata.
3. Run component QA/QC and conservation checks.
4. Generate downstream products only when required components succeed.
5. Preserve failed-run diagnostics without publishing partial results.

## User interaction

Operators review failed runs, source availability, unresolved spatial records, monthly completeness, and release status. Manual runs should use the same interfaces as scheduled runs.
