# Tools

Tools are maintenance utilities that support controlled data preparation, migration, diagnostics, or product generation. Tools are not routine component implementations.

## Typical uses

- Prepare or update approved TxRR/model input files.
- Generate or inspect timeseries products.
- Compare historical and replacement outputs.
- Produce one-time migration or validation artifacts.

## Rules

- Keep tool inputs and outputs explicit.
- Avoid silently modifying source or production data.
- Provide a dry-run or preview mode when practical.
- Record model version, date range, replacement scope, and output location for TxRR updates.

## User interaction

A user should review the planned changes before a tool replaces historical data or modifies model inputs. Retain logs or comparison summaries for approved releases.
