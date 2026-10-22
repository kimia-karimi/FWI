from __future__ import annotations

import pandas as pd

from tx_fwi.qaqc.component_checks import validate_canonical_component
from tx_fwi.qaqc.models import CheckResult


class QAComponentMixin:
    """Shared run orchestration for components that implement build() and validate()."""

    def validate(self, df, *, result, start, end) -> None:
        validate_canonical_component(
            df,
            component_result=result,
            run_manager=self.ctx.run_manager,
            component=self.name,
            start=start,
            end=end,
            coverage_group_cols=["id"],
        )

    def run(self, start=None, end=None):
        prior_watermark = self.ctx.storage.get_watermark(self.name, default=None)
        if start is None:
            start = (
                pd.Timestamp(prior_watermark) + pd.Timedelta(days=1)
                if prior_watermark is not None
                else pd.Timestamp("2015-01-01")
            )
        if end is None:
            end = pd.Timestamp.utcnow().normalize()

        manager = self.ctx.run_manager
        logger = manager.component_logger(self.name)
        result = manager.new_component_result(
            component=self.name,
            requested_start=start,
            requested_end=end,
            prior_watermark=prior_watermark,
        )

        try:
            logger.info("Building %s for %s through %s", self.name, pd.Timestamp(start).date(), pd.Timestamp(end).date())
            df = self.build(start, end)
            self.validate(df, result=result, start=start, end=end)
            result.finalize()

            if result.has_blocking_failures:
                result.append_status = "blocked_by_qaqc"
                logger.error("Append blocked by QA/QC failures.")
                return 0

            append_result = self.ctx.storage.append_checked(
                df,
                component=self.name,
                run_start=start,
                run_end=end,
                prior_watermark=prior_watermark,
            )
            result.prepared_for_append = append_result.prepared_rows
            result.appended_rows = append_result.appended_rows
            result.append_status = append_result.status
            result.resulting_watermark = append_result.resulting_watermark

            if append_result.status != "success":
                result.add_check(CheckResult(
                    check_id="storage_append",
                    status="FAIL",
                    message=append_result.message,
                    blocking=True,
                ))
            result.finalize()
            return result.appended_rows

        except Exception as exc:
            logger.exception("Component failed.")
            result.add_check(CheckResult(
                check_id="unhandled_component_error",
                status="FAIL",
                message=str(exc),
                blocking=True,
                details={"exception_type": type(exc).__name__},
            ))
            result.append_status = "failed"
            return 0
        finally:
            manager.add_component(result)
