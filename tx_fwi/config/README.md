# Configuration

Configuration defines environment-specific paths, credentials, enabled features, and operational settings.

## Review before running

- Local and network data paths
- Registry and watershed locations
- Output and storage locations
- Source credentials or tokens
- Certificate settings
- Enabled components
- Scheduler-related options


## Rules

- Do not commit secrets.
- Keep environment-specific values out of component code.
- Prefer environment variables or approved secret storage for credentials.
- Document defaults and required settings.

## User interaction

Operators update configuration (if needed) during deployment or source changes. Configuration changes should be reviewed before scheduled runs resume.
