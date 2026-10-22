from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tx_fwi.qaqc.models import (
    CheckResult,
    ComponentResult,
    RunManifest,
)


class RunManager:
    """
    Collect component results, write one run log, one component
    summary, and one machine-readable manifest.
    """

    def __init__(
        self,
        *,
        log_root,
        requested_start,
        requested_end,
        repository_version=None,
        environment=None,
    ):
        started = (
            datetime.now()
            .astimezone()
        )

        self.run_id = started.strftime(
            "%Y%m%dT%H%M%S%z"
        )

        self.run_dir = (
            Path(log_root)
            / self.run_id
        )

        self.run_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        self.logger = self._configure_logger()

        self.manifest = RunManifest(
            run_id=self.run_id,
            started_at=started.isoformat(),
            requested_start=str(
                pd.Timestamp(
                    requested_start
                ).date()
            ),
            requested_end=str(
                pd.Timestamp(
                    requested_end
                ).date()
            ),
            repository_version=(
                repository_version
            ),
            environment=environment,
        )

    def _configure_logger(
        self,
    ) -> logging.Logger:
        logger = logging.getLogger(
            f"tx_fwi.{self.run_id}"
        )

        logger.setLevel(logging.INFO)
        logger.propagate = False

        formatter = logging.Formatter(
            fmt=(
                "%(asctime)s | "
                "%(levelname)s | "
                "%(name)s | "
                "%(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.FileHandler(
            self.run_dir / "run.log",
            encoding="utf-8",
        )

        file_handler.setFormatter(
            formatter
        )

        console_handler = (
            logging.StreamHandler()
        )

        console_handler.setFormatter(
            logging.Formatter(
                "%(levelname)s | %(message)s"
            )
        )

        logger.addHandler(file_handler)
        logger.addHandler(
            console_handler
        )

        return logger

    def new_component_result(
        self,
        component,
        requested_start,
        requested_end,
        prior_watermark=None,
    ) -> ComponentResult:
        return ComponentResult(
            component=component,
            status="PASS",
            requested_start=str(
                pd.Timestamp(
                    requested_start
                ).date()
            ),
            requested_end=str(
                pd.Timestamp(
                    requested_end
                ).date()
            ),
            prior_watermark=(
                str(
                    pd.Timestamp(
                        prior_watermark
                    ).date()
                )
                if prior_watermark
                is not None
                else None
            ),
        )

    def add_component(
        self,
        result: ComponentResult,
    ) -> None:
        result.finalize()

        self.manifest.components.append(
            result
        )

        self.logger.info(
            "%s completed with status=%s; "
            "output_rows=%s; appended_rows=%s; "
            "append_status=%s",
            result.component,
            result.status,
            f"{result.output_rows:,}",
            f"{result.appended_rows:,}",
            result.append_status,
        )

    def write_exception_csv(
        self,
        *,
        component,
        name,
        df,
    ) -> str | None:
        if df is None or df.empty:
            return None

        component_dir = (
            self.run_dir
            / component
        )

        component_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        path = (
            component_dir
            / f"{name}.csv"
        )

        df.to_csv(
            path,
            index=False,
        )

        return str(
            path.relative_to(
                self.run_dir
            )
        )

    def finalize(
        self,
    ) -> RunManifest:
        self.manifest.finalize()

        self._write_summary()
        self._write_manifest()

        self.logger.info(
            "Run completed with status=%s",
            self.manifest.status,
        )

        return self.manifest

    def _write_summary(
        self,
    ) -> None:
        rows = []

        for component in (
            self.manifest.components
        ):
            blocking_failures = [
                check.check_id
                for check
                in component.checks
                if check.status == "FAIL"
                and check.blocking
            ]

            warnings = [
                check.check_id
                for check
                in component.checks
                if check.status == "WARN"
            ]

            rows.append(
                {
                    "run_id": self.run_id,
                    "component": (
                        component.component
                    ),
                    "status": (
                        component.status
                    ),
                    "requested_start": (
                        component
                        .requested_start
                    ),
                    "requested_end": (
                        component
                        .requested_end
                    ),
                    "source_start": (
                        component.source_start
                    ),
                    "source_end": (
                        component.source_end
                    ),
                    "output_start": (
                        component.output_start
                    ),
                    "output_end": (
                        component.output_end
                    ),
                    "expected_dates": (
                        component
                        .expected_dates
                    ),
                    "output_rows": (
                        component.output_rows
                    ),
                    "output_ids": (
                        component.output_ids
                    ),
                    "prepared_for_append": (
                        component
                        .prepared_for_append
                    ),
                    "appended_rows": (
                        component.appended_rows
                    ),
                    "append_status": (
                        component.append_status
                    ),
                    "prior_watermark": (
                        component
                        .prior_watermark
                    ),
                    "resulting_watermark": (
                        component
                        .resulting_watermark
                    ),
                    "blocking_failures": (
                        ";".join(
                            blocking_failures
                        )
                    ),
                    "warnings": (
                        ";".join(warnings)
                    ),
                }
            )

        pd.DataFrame(rows).to_csv(
            self.run_dir
            / "run_summary.csv",
            index=False,
        )

    def _write_manifest(
        self,
    ) -> None:
        path = (
            self.run_dir
            / "run_manifest.json"
        )

        with path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                self.manifest.to_dict(),
                file,
                indent=2,
                default=str,
            )
