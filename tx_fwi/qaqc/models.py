# tx_fwi/qaqc/models.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass
class CheckResult:
    check_id: str
    status: CheckStatus
    message: str
    expected: Any = None
    actual: Any = None
    blocking: bool = True
    details: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComponentResult:
    component: str
    status: CheckStatus
    requested_start: str
    requested_end: str
    source_start: str | None = None
    source_end: str | None = None
    output_start: str | None = None
    output_end: str | None = None
    expected_dates: int | None = None
    output_rows: int = 0
    output_ids: int = 0
    prepared_for_append: int = 0
    appended_rows: int = 0
    prior_watermark: str | None = None
    resulting_watermark: str | None = None
    append_status: str = "not_attempted"
    checks: list[CheckResult] = field(
        default_factory=list
    )
    metrics: dict[str, Any] = field(
        default_factory=dict
    )

    def add_check(
        self,
        result: CheckResult,
    ) -> None:
        self.checks.append(result)

    def finalize(self) -> None:
        blocking_failures = any(
            check.status == "FAIL"
            and check.blocking
            for check in self.checks
        )
        warnings = any(
            check.status == "WARN"
            for check in self.checks
        )

        if blocking_failures:
            self.status = "FAIL"
        elif warnings:
            self.status = "WARN"
        else:
            self.status = "PASS"

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["checks"] = [
            check.to_dict()
            for check in self.checks
        ]
        return output


@dataclass
class RunManifest:
    run_id: str
    started_at: str
    requested_start: str
    requested_end: str
    repository_version: str | None = None
    environment: str | None = None
    status: CheckStatus = "PASS"
    completed_at: str | None = None
    components: list[ComponentResult] = field(
        default_factory=list
    )

    def finalize(self) -> None:
        if any(
            component.status == "FAIL"
            for component in self.components
        ):
            self.status = "FAIL"
        elif any(
            component.status == "WARN"
            for component in self.components
        ):
            self.status = "WARN"
        else:
            self.status = "PASS"

        self.completed_at = (
            datetime.now().astimezone().isoformat()
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

from dataclasses import dataclass


@dataclass
class AppendResult:
    status: str
    prepared_rows: int
    appended_rows: int
    prior_watermark: str | None
    resulting_watermark: str | None
    historical_hash_before: str | None
    historical_hash_after: str | None
    message: str
