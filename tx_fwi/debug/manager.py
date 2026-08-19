from pathlib import Path
import pandas as pd


class DebugManager:

    def __init__(
        self,
        component: str,
        enabled: bool = True,
        outdir: str = "debug_output",
    ):

        self.component = component
        self.enabled = enabled

        self.outdir = (
            Path(outdir)
            / component
        )

        self.outdir.mkdir(
            parents=True,
            exist_ok=True,
        )

    # --------------------------------------------------
    # Generic csv export
    # --------------------------------------------------

    def export(
        self,
        name: str,
        df: pd.DataFrame,
    ):

        if not self.enabled:
            return

        path = (
            self.outdir
            / f"{name}.csv"
        )

        df.to_csv(
            path,
            index=False,
        )

        print(
            f"[{self.component}] "
            f"wrote {path}"
        )

    # --------------------------------------------------
    # Duplicate detection
    # --------------------------------------------------

    def duplicates(
        self,
        df,
        subset,
        name="duplicates",
    ):

        dup = (
            df[
                df.duplicated(
                    subset=subset,
                    keep=False,
                )
            ]
            .copy()
        )

        self.export(
            name,
            dup,
        )

        print(
            f"[{self.component}] "
            f"{len(dup)} duplicate rows"
        )

        return dup

    # --------------------------------------------------
    # Assignment check
    #
    # coastal-only coverage
    # --------------------------------------------------

    def assignment(
        self,
        source_df,
        assigned_df,
        id_col,
        assignment_col="WS_ID",
        name="assignment",
    ):

        expected = set(
            source_df[id_col]
            .dropna()
            .astype(str)
        )

        assigned = set(
            assigned_df[
                assigned_df[assignment_col].notna()
            ][id_col]
            .astype(str)
        )

        missing = expected - assigned

        missing_df = (
            source_df[
                source_df[id_col]
                .astype(str)
                .isin(missing)
            ]
            .copy()
        )

        self.export(
            f"{name}_missing",
            missing_df,
        )

        coverage = (
            len(assigned)
            / len(expected)
            * 100
            if expected
            else 0
        )

        print(
            f"[{self.component}] "
            f"Assignment coverage="
            f"{coverage:.1f}%"
        )

        return {
            "expected": len(expected),
            "assigned": len(assigned),
            "missing": len(missing),
            "coverage_pct": coverage,
        }

    # --------------------------------------------------
    # Temporal coverage
    #
    # useful for gages
    # --------------------------------------------------

    def temporal_coverage(
        self,
        df,
        date_col,
        id_col,
        start,
        end,
    ):

        start = pd.Timestamp(start)
        end = pd.Timestamp(end)

        expected_days = (
            end - start
        ).days + 1

        result = []

        for gid, grp in df.groupby(id_col):

            actual = (
                pd.to_datetime(
                    grp[date_col]
                )
                .dt.normalize()
                .nunique()
            )

            result.append(
                {
                    id_col: gid,
                    "expected_days": expected_days,
                    "actual_days": actual,
                    "missing_days":
                        expected_days - actual,
                    "coverage_pct":
                        round(
                            actual
                            / expected_days
                            * 100,
                            2,
                        ),
                }
            )

        out = pd.DataFrame(result)

        self.export(
            "temporal_coverage",
            out,
        )

        return out

    # --------------------------------------------------
    # Mass balance
    #
    # before vs after aggregation
    # --------------------------------------------------

    def mass_balance(
        self,
        before_df,
        after_df,
        value_col,
        name="mass_balance",
    ):

        before = (
            before_df[value_col]
            .sum()
        )

        after = (
            after_df[value_col]
            .sum()
        )

        report = pd.DataFrame(
            [
                {
                    "before": before,
                    "after": after,
                    "difference":
                        after - before,
                }
            ]
        )

        self.export(
            name,
            report,
        )

        return report

    # --------------------------------------------------
    # watershed monthly summary
    #
    # reusable across
    # diversion
    # return
    # gaged
    # ungaged
    # --------------------------------------------------

    def watershed_monthly_summary(
        self,
        df,
        source_id_col,
        flow_col,
        flow_role,
        year_col="year",
        month_col="month",
        ws_col="WS_ID",
        estuary_col="Estuary",
        name="watershed_monthly_summary",
    ):

        summary = (
            df.groupby(
                [
                    year_col,
                    month_col,
                    estuary_col,
                    ws_col,
                    source_id_col,
                ],
                dropna=False,
            )[flow_col]
            .sum()
            .reset_index()
        )

        summary[
            "flow_role"
        ] = flow_role

        self.export(
            name,
            summary,
        )

        return summary

    # --------------------------------------------------
    # Component completeness
    #
    # more useful than row counts
    # --------------------------------------------------

    def completeness(
        self,
        observed,
        expected,
        name="completeness",
    ):

        pct = (
            observed
            / expected
            * 100
            if expected
            else 0
        )

        report = pd.DataFrame(
            [
                {
                    "expected":
                        expected,
                    "observed":
                        observed,
                    "coverage_pct":
                        pct,
                }
            ]
        )

        self.export(
            name,
            report,
        )

        return report
