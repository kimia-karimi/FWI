# tx_fwi/components/returns.py
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from tx_fwi.components.base import RunContext
from tx_fwi.sources.epa_dmr import ReturnFlowSource
from tx_fwi.transforms.spatial import (
    assign_geometries_to_watersheds,
    fill_missing_geometry_from_group,
)
from tx_fwi.transforms.temporal import expand_monthly_to_daily
from tx_fwi.transforms.units import mgd_to_afday


REQUIRED_OUT_COLS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "source",
    "value_afday",
    "flow_role",
    "count_in_basin_sum",
    "data_as_of",
    "note",
]


WRITE_DEBUG_FILES = True


MONITORING_LOCATION_PRIORITY = {
    "1": 0,    # Effluent Gross
    "EG": 1,   # Effluent Gross
    "Y": 2,    # Effluent Gross Supplementary
    "EA": 3,   # Effluent Adjusted Value
    "ED": 4,   # Effluent with additives
    "E1": 5,   # Effluent Option 1
    "E2": 6,   # Effluent Option 2
    "E3": 7,   # Effluent Option 3
}


STATISTICAL_BASE_MAP = {
    # Direct monthly or approximately monthly averages
    "MK": {
        "rank": 0,
        "description": "Monthly Average",
        "monthly_compatible": True,
    },
    "3C": {
        "rank": 1,
        "description": "30 Day Average",
        "monthly_compatible": True,
    },
    "DB": {
        "rank": 2,
        "description": "Daily Average",
        "monthly_compatible": True,
    },
    "1H": {
        "rank": 3,
        "description": "1 Day Average",
        "monthly_compatible": False,
    },
    "DG": {
        "rank": 4,
        "description": "Discharge Per Day Average",
        "monthly_compatible": False,
    },
    "AF": {
        "rank": 5,
        "description": "Average",
        "monthly_compatible": False,
    },
    "AH": {
        "rank": 6,
        "description": "Average Value",
        "monthly_compatible": False,
    },
    "AE": {
        "rank": 7,
        "description": "Arithmetic Mean",
        "monthly_compatible": False,
    },
    "MC": {
        "rank": 8,
        "description": "Mean",
        "monthly_compatible": False,
    },
    "RB": {
        "rank": 9,
        "description": "Reported Average",
        "monthly_compatible": False,
    },
    "NA": {
        "rank": 10,
        "description": "Non-Specific Average",
        "monthly_compatible": False,
    },
    "A1": {
        "rank": 11,
        "description": "Average (Data Migration)",
        "monthly_compatible": False,
    },

    # Monthly or 30-day alternatives
    "3B": {
        "rank": 15,
        "description": "30 Day Arithmetic",
        "monthly_compatible": True,
    },
    "3F": {
        "rank": 16,
        "description": "30 Day Arithmetic Mean",
        "monthly_compatible": True,
    },
    "3A": {
        "rank": 17,
        "description": "30 Day Geometric Mean",
        "monthly_compatible": True,
    },
    "3D": {
        "rank": 18,
        "description": "30 Day Geometric",
        "monthly_compatible": True,
    },
    "3H": {
        "rank": 19,
        "description": "30 Day Average Geometric",
        "monthly_compatible": True,
    },
    "ML": {
        "rank": 20,
        "description": "Monthly Geometric",
        "monthly_compatible": True,
    },
    "MM": {
        "rank": 21,
        "description": "Monthly Geometric Mean",
        "monthly_compatible": True,
    },
    "M4": {
        "rank": 22,
        "description": "Monthly Average Geometric",
        "monthly_compatible": True,
    },
    "LB": {
        "rank": 23,
        "description": "Logarithmic Monthly Median",
        "monthly_compatible": False,
    },
    "M3": {
        "rank": 24,
        "description": "Monthly Median",
        "monthly_compatible": False,
    },

    # Short-period fallbacks
    "DP": {
        "rank": 30,
        "description": "14 Day Average",
        "monthly_compatible": False,
    },
    "7A": {
        "rank": 31,
        "description": "7 Day Average",
        "monthly_compatible": False,
    },
    "WA": {
        "rank": 32,
        "description": "Weekly Average",
        "monthly_compatible": False,
    },
    "HB": {
        "rank": 33,
        "description": "High Weekly Average",
        "monthly_compatible": False,
    },
    "HA": {
        "rank": 34,
        "description": "High 7 Day Average",
        "monthly_compatible": False,
    },
    "4A": {
        "rank": 35,
        "description": "4 Day Average",
        "monthly_compatible": False,
    },
    "5B": {
        "rank": 36,
        "description": "5 Day Average",
        "monthly_compatible": False,
    },
    "1C": {
        "rank": 37,
        "description": "12 Day Average",
        "monthly_compatible": False,
    },
    "9B": {
        "rank": 38,
        "description": "90 Day Average",
        "monthly_compatible": False,
    },
    "1F": {
        "rank": 39,
        "description": "120 Day Average",
        "monthly_compatible": False,
    },

    # Longer-period fallbacks
    "QA": {
        "rank": 50,
        "description": "Quarterly Average",
        "monthly_compatible": False,
    },
    "QR": {
        "rank": 51,
        "description": "Quarterly Rolling Average",
        "monthly_compatible": False,
    },
    "SC": {
        "rank": 52,
        "description": "Semi-Annual Average",
        "monthly_compatible": False,
    },
    "6D": {
        "rank": 53,
        "description": "6 Month Average",
        "monthly_compatible": False,
    },
    "AB": {
        "rank": 54,
        "description": "Annual Average",
        "monthly_compatible": False,
    },
    "1D": {
        "rank": 55,
        "description": "12 Month Average",
        "monthly_compatible": False,
    },
    "1M": {
        "rank": 56,
        "description": "Month 1 Average",
        "monthly_compatible": False,
    },
    "2M": {
        "rank": 57,
        "description": "Month 2 Average",
        "monthly_compatible": False,
    },
    "3M": {
        "rank": 58,
        "description": "Month 3 Average",
        "monthly_compatible": False,
    },
    "RA": {
        "rank": 59,
        "description": "Rolling Average",
        "monthly_compatible": False,
    },
    "RE": {
        "rank": 60,
        "description": "Individual 12 Month Rolling Average",
        "monthly_compatible": False,
    },
    "RF": {
        "rank": 61,
        "description": "Aggregate 12 Month Rolling Average",
        "monthly_compatible": False,
    },
    "5Y": {
        "rank": 62,
        "description": "5 Year Average",
        "monthly_compatible": False,
    },

    # Average-like fallback values
    "DA": {
        "rank": 70,
        "description": "Daily Geometric Average",
        "monthly_compatible": True,
    },
    "DM": {
        "rank": 71,
        "description": "Daily Geometric",
        "monthly_compatible": False,
    },
    "GA": {
        "rank": 72,
        "description": "Geometric Mean",
        "monthly_compatible": False,
    },
    "LA": {
        "rank": 73,
        "description": "Logarithmic Mean",
        "monthly_compatible": False,
    },
    "MD": {
        "rank": 74,
        "description": "Median",
        "monthly_compatible": False,
    },
    "DF": {
        "rank": 75,
        "description": "Daily Median",
        "monthly_compatible": False,
    },
    "SE": {
        "rank": 76,
        "description": "Single Reading",
        "monthly_compatible": False,
    },
    "VA": {
        "rank": 77,
        "description": "Value",
        "monthly_compatible": False,
    },
    "DN": {
        "rank": 78,
        "description": "Discharged",
        "monthly_compatible": False,
    },
}


STATISTICAL_TYPE_PRIORITY = {
    "AVG": 0,
    "MEAN": 1,
    "MEDIAN": 2,
}


def fiscal_year(dt):
    dt = pd.Timestamp(dt)

    return (
        dt.year + 1
        if dt.month >= 10
        else dt.year
    )


def _join_unique(values):
    """
    Return sorted unique non-null values as a semicolon-delimited string.
    """
    unique_values = sorted(
        {
            str(value).strip()
            for value in values
            if value is not None
            and not pd.isna(value)
            and str(value).strip()
        }
    )

    return (
        ";".join(unique_values)
        if unique_values
        else pd.NA
    )


class ReturnComponent:
    name = "return"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

        self.return_source = ReturnFlowSource(
            ctx=ctx
        )

    @staticmethod
    def _normalize_npdes(series):
        return (
            series
            .astype("string")
            .str.replace(
                r"\D+",
                "",
                regex=True,
            )
            .str.lstrip("0")
            .replace("", pd.NA)
        )

    @staticmethod
    def _normalize_outfall(series):
        normalized = (
            series
            .astype("string")
            .str.strip()
            .str.upper()
            .str.replace(
                r"\.0$",
                "",
                regex=True,
            )
        )

        return (
            normalized
            .str.replace(
                r"^0+(?=[0-9])",
                "",
                regex=True,
            )
            .replace("", pd.NA)
        )

    @staticmethod
    def _normalize_code(series):
        return (
            series
            .astype("string")
            .str.strip()
            .str.upper()
            .replace(
                {
                    "": pd.NA,
                    "NAN": pd.NA,
                    "NONE": pd.NA,
                    "<NA>": pd.NA,
                }
            )
        )

    def _build_feature_debug(
        self,
        feature_monthly,
    ):
        """
        Create one permit-period row with one MGD column per outfall.
        """
        debug = (
            feature_monthly
            .pivot_table(
                index=[
                    "EXTERNAL_PERMIT_NMBR",
                    "MONITORING_PERIOD_END_DATE",
                ],
                columns="PERM_FEATURE_NMBR",
                values="FLOW_MGD",
                aggfunc="sum",
                fill_value=0,
            )
            .reset_index()
        )

        debug.columns = [
            col
            if col in [
                "EXTERNAL_PERMIT_NMBR",
                "MONITORING_PERIOD_END_DATE",
            ]
            else f"feature_{col}_mgd"
            for col in debug.columns
        ]

        feature_cols = [
            col
            for col in debug.columns
            if col.startswith("feature_")
            and col.endswith("_mgd")
        ]

        debug["overall_flow_mgd"] = (
            debug[feature_cols].sum(axis=1)
        )

        debug["days_in_month"] = (
            pd.to_datetime(
                debug[
                    "MONITORING_PERIOD_END_DATE"
                ]
            )
            .dt.days_in_month
        )

        debug[
            "overall_flow_acft_month"
        ] = (
            mgd_to_afday(
                debug["overall_flow_mgd"]
            )
            * debug["days_in_month"]
        )

        return debug

    def _select_return_flow_candidates(
        self,
        dmr: pd.DataFrame,
        *,
        raise_on_conflicting_ties: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Select one representative flow per permit, outfall, and period.
        """
        if dmr.empty:
            return dmr.copy(), dmr.copy()

        work = dmr.copy().reset_index(
            drop=True
        )

        work["_candidate_id"] = range(
            len(work)
        )

        group_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "PERMIT_NUM",
            "PERM_FEATURE_NMBR",
            "MONITORING_PERIOD_END_DATE",
        ]

        required_cols = group_cols + [
            "DMR_VALUE_STANDARD_UNITS",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
        ]

        missing_cols = [
            col
            for col in required_cols
            if col not in work.columns
        ]

        if missing_cols:
            raise KeyError(
                "Cannot select representative DMR "
                "flow records. Missing columns: "
                + ", ".join(missing_cols)
            )

        work[
            "MONITORING_PERIOD_END_DATE"
        ] = pd.to_datetime(
            work[
                "MONITORING_PERIOD_END_DATE"
            ],
            errors="coerce",
        )

        work["FLOW_MGD"] = pd.to_numeric(
            work[
                "DMR_VALUE_STANDARD_UNITS"
            ],
            errors="coerce",
        )

        code_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
            "DMR_VALUE_QUALIFIER_CODE",
            "NODI_CODE",
            "PARAMETER_CODE",
        ]

        for col in code_cols:
            if col in work.columns:
                work[col] = (
                    self._normalize_code(
                        work[col]
                    )
                )

        work = work.dropna(
            subset=group_cols + ["FLOW_MGD"]
        ).copy()

        if work.empty:
            return work.copy(), work.copy()

        negative = work["FLOW_MGD"] < 0

        #if negative.any():
           # sample = work.loc[
               # negative,
                #group_cols + ["FLOW_MGD"],
            #].head(20)

            raise ValueError("Negative EPA DMR flow values require review:\n" + sample.to_string(index=False))

        work[
            "stat_base_description"
        ] = (
            work["STATISTICAL_BASE_CODE"]
            .map(
                lambda code: (
                    STATISTICAL_BASE_MAP
                    .get(code, {})
                    .get("description")
                )
            )
        )

        work["_stat_base_rank"] = (
            work["STATISTICAL_BASE_CODE"]
            .map(
                lambda code: (
                    STATISTICAL_BASE_MAP
                    .get(code, {})
                    .get("rank", 900)
                )
            )
            .astype(int)
        )

        work[
            "monthly_average_compatible"
        ] = (
            work["STATISTICAL_BASE_CODE"]
            .map(
                lambda code: (
                    STATISTICAL_BASE_MAP
                    .get(code, {})
                    .get(
                        "monthly_compatible",
                        False,
                    )
                )
            )
        )

        work["_monthly_compat_rank"] = (
            ~work[
                "monthly_average_compatible"
            ]
        ).astype(int)

        work["_stat_type_rank"] = (
            work[
                "STATISTICAL_BASE_TYPE_CODE"
            ]
            .map(STATISTICAL_TYPE_PRIORITY)
            .fillna(900)
            .astype(int)
        )

        work["_location_rank"] = (
            work["MONITORING_LOCATION_CODE"]
            .map(
                MONITORING_LOCATION_PRIORITY
            )
            .fillna(90)
            .astype(int)
        )

        work["known_stat_base"] = (
            work["STATISTICAL_BASE_CODE"]
            .isin(STATISTICAL_BASE_MAP)
        )

        work["_known_code_rank"] = (
            ~work["known_stat_base"]
        ).astype(int)

        work[
            "eligible_average_candidate"
        ] = (
            work[
                "STATISTICAL_BASE_TYPE_CODE"
            ]
            .isin(STATISTICAL_TYPE_PRIORITY)
        )

        eligible = work.loc[
            work[
                "eligible_average_candidate"
            ]
        ].copy()

        if eligible.empty:
            work[
                "selection_result"
            ] = "excluded_no_average_candidate"

            return eligible, work

        if (
            "DMR_VALUE_QUALIFIER_CODE"
            in eligible.columns
        ):
            qualifier = eligible[
                "DMR_VALUE_QUALIFIER_CODE"
            ]

            eligible[
                "_qualifier_rank"
            ] = 10

            eligible.loc[
                qualifier.isna()
                | qualifier.eq("="),
                "_qualifier_rank",
            ] = 0
        else:
            eligible[
                "_qualifier_rank"
            ] = 0

        if "NODI_CODE" in eligible.columns:
            eligible["_nodi_rank"] = (
                eligible["NODI_CODE"]
                .notna()
                .astype(int)
            )
        else:
            eligible["_nodi_rank"] = 0

        if (
            "VALUE_RECEIVED_DATE"
            in eligible.columns
        ):
            eligible["_received_date"] = (
                pd.to_datetime(
                    eligible[
                        "VALUE_RECEIVED_DATE"
                    ],
                    errors="coerce",
                )
            )
        else:
            eligible["_received_date"] = (
                pd.NaT
            )

        eligible["selection_basis"] = (
            "location="
            + eligible[
                "MONITORING_LOCATION_CODE"
            ].fillna("missing")
            + ";stat_base="
            + eligible[
                "STATISTICAL_BASE_CODE"
            ].fillna("missing")
            + ";stat_type="
            + eligible[
                "STATISTICAL_BASE_TYPE_CODE"
            ].fillna("missing")
            + ";description="
            + eligible[
                "stat_base_description"
            ].fillna(
                "unmapped average fallback"
            )
        )

        exact_candidate_cols = (
            group_cols
            + [
                "MONITORING_LOCATION_CODE",
                "STATISTICAL_BASE_CODE",
                "STATISTICAL_BASE_TYPE_CODE",
                "FLOW_MGD",
            ]
        )

        eligible[
            "_candidate_copy_count"
        ] = (
            eligible
            .groupby(
                exact_candidate_cols,
                dropna=False,
            )["FLOW_MGD"]
            .transform("size")
        )

        eligible = (
            eligible
            .drop_duplicates(
                subset=exact_candidate_cols,
                keep="first",
            )
            .copy()
        )

        substantive_rank_cols = [
            "_stat_type_rank",
            "_location_rank",
            "_monthly_compat_rank",
            "_known_code_rank",
            "_stat_base_rank",
            "_qualifier_rank",
            "_nodi_rank",
        ]

        eligible["_priority_tuple"] = list(
            zip(
                *(
                    eligible[col]
                    for col
                    in substantive_rank_cols
                )
            )
        )

        eligible[
            "_best_priority_tuple"
        ] = (
            eligible
            .groupby(
                group_cols,
                dropna=False,
            )["_priority_tuple"]
            .transform("min")
        )

        eligible[
            "_is_top_priority_candidate"
        ] = (
            eligible["_priority_tuple"]
            .eq(
                eligible[
                    "_best_priority_tuple"
                ]
            )
        )

        top_candidates = eligible.loc[
            eligible[
                "_is_top_priority_candidate"
            ]
        ].copy()

        top_summary = (
            top_candidates
            .groupby(
                group_cols,
                dropna=False,
                as_index=False,
            )
            .agg(
                n_top_candidates=(
                    "FLOW_MGD",
                    "size",
                ),
                n_top_flow_values=(
                    "FLOW_MGD",
                    "nunique",
                ),
                min_top_flow_mgd=(
                    "FLOW_MGD",
                    "min",
                ),
                max_top_flow_mgd=(
                    "FLOW_MGD",
                    "max",
                ),
            )
        )

        conflicting_groups = (
            top_summary.loc[
                top_summary[
                    "n_top_flow_values"
                ] > 1
            ]
            .copy()
        )

        if not conflicting_groups.empty:
            conflict_rows = (
                top_candidates
                .merge(
                    conflicting_groups[
                        group_cols
                    ],
                    on=group_cols,
                    how="inner",
                )
            )

            display_cols = group_cols + [
                "MONITORING_LOCATION_CODE",
                "STATISTICAL_BASE_CODE",
                "STATISTICAL_BASE_TYPE_CODE",
                "FLOW_MGD",
                "DMR_VALUE_ID",
                "stat_base_description",
            ]

            display_cols = [
                col
                for col in display_cols
                if col
                in conflict_rows.columns
            ]

            message = (
                "Conflicting top-ranked EPA DMR "
                "return-flow candidates were found. "
                "Equal-priority records have different "
                "flow values and were not summed:\n"
                + conflict_rows[
                    display_cols
                ]
                .head(50)
                .to_string(index=False)
            )

            if raise_on_conflicting_ties:
                raise ValueError(message)

            print(
                "[ReturnFlow] WARNING: "
                + message
            )

        sort_cols = (
            group_cols
            + substantive_rank_cols
            + ["_received_date"]
        )

        ascending = (
            [True] * len(group_cols)
            + [True]
            * len(substantive_rank_cols)
            + [False]
        )

        for optional_id in [
            "DMR_VALUE_ID",
            "DMR_FORM_VALUE_ID",
            "DMR_EVENT_ID",
            "LIMIT_VALUE_ID",
            "LIMIT_ID",
        ]:
            if optional_id in eligible.columns:
                sort_col = (
                    f"_sort_{optional_id}"
                )

                eligible[sort_col] = (
                    pd.to_numeric(
                        eligible[optional_id],
                        errors="coerce",
                    )
                )

                sort_cols.append(sort_col)
                ascending.append(False)

        eligible = eligible.sort_values(
            sort_cols,
            ascending=ascending,
            na_position="last",
            kind="stable",
        ).copy()

        selected = (
            eligible
            .drop_duplicates(
                subset=group_cols,
                keep="first",
            )
            .copy()
        )

        selected = selected.merge(
            top_summary,
            on=group_cols,
            how="left",
            validate="one_to_one",
        )

        selected[
            "selection_status"
        ] = "selected_unique"

        selected.loc[
            (
                selected[
                    "n_top_candidates"
                ] > 1
            )
            & (
                selected[
                    "n_top_flow_values"
                ] == 1
            ),
            "selection_status",
        ] = "selected_from_equal_value_tie"

        selected.loc[
            selected[
                "n_top_flow_values"
            ] > 1,
            "selection_status",
        ] = "selected_from_conflicting_tie"

        if selected.duplicated(
            group_cols
        ).any():
            raise AssertionError(
                "Selector returned more than "
                "one row per permit/outfall/period."
            )

        selected_candidate_ids = set(
            selected["_candidate_id"]
        )

        eligible_audit = eligible.copy()

        eligible_audit[
            "selection_result"
        ] = "not_selected"

        eligible_audit.loc[
            eligible_audit[
                "_candidate_id"
            ].isin(selected_candidate_ids),
            "selection_result",
        ] = "selected"

        eligible_audit.loc[
            eligible_audit[
                "_is_top_priority_candidate"
            ]
            & ~eligible_audit[
                "_candidate_id"
            ].isin(selected_candidate_ids),
            "selection_result",
        ] = "top_priority_tie_not_selected"

        audit = work.merge(
            eligible_audit[
                [
                    "_candidate_id",
                    "selection_result",
                ]
            ],
            on="_candidate_id",
            how="left",
            validate="one_to_one",
        )

        audit["selection_result"] = (
            audit["selection_result"]
            .fillna(
                "excluded_not_representative_average"
            )
        )

        return selected, audit

    def _prepare_canonical_outfall_geometry(
        self,
        outfalls: gpd.GeoDataFrame,
    ) -> gpd.GeoDataFrame:
        """
        Select one audited canonical geometry for each normalized
        NPDES permit and outfall key.
        """
        required_cols = [
            "NPDES_NUM",
            "OUTFALL",
            "geometry",
        ]

        missing_cols = [
            col
            for col in required_cols
            if col not in outfalls.columns
        ]

        if missing_cols:
            raise KeyError(
                "TCEQ outfall data are missing "
                "required columns: "
                + ", ".join(missing_cols)
            )

        outfalls = outfalls.copy()

        if not isinstance(
            outfalls,
            gpd.GeoDataFrame,
        ):
            outfalls = gpd.GeoDataFrame(
                outfalls,
                geometry="geometry",
                crs="EPSG:4326",
            )
        elif outfalls.crs is None:
            outfalls = outfalls.set_crs(
                "EPSG:4326"
            )

        # Preserve the original TCEQ permit ID before
        # PERMIT_NUM is reused as the normalized NPDES key.
        if "PERMIT_NUM" in outfalls.columns:
            outfalls[
                "TCEQ_PERMIT_NUM"
            ] = (
                outfalls["PERMIT_NUM"]
                .astype("string")
                .str.strip()
            )

        outfalls["PERMIT_NUM"] = (
            self._normalize_npdes(
                outfalls["NPDES_NUM"]
            )
        )

        outfalls[
            "PERM_FEATURE_NMBR"
        ] = self._normalize_outfall(
            outfalls["OUTFALL"]
        )

        print(
            "[ReturnFlow] TCEQ outfall rows "
            "before validation:",
            len(outfalls),
        )

        outfalls = outfalls.dropna(
            subset=[
                "PERMIT_NUM",
                "PERM_FEATURE_NMBR",
                "geometry",
            ]
        ).copy()

        print(
            "[ReturnFlow] TCEQ outfall rows "
            "with valid geometry:",
            len(outfalls),
        )

        if outfalls.empty:
            return gpd.GeoDataFrame(
                columns=[
                    "PERMIT_NUM",
                    "PERM_FEATURE_NMBR",
                    "geometry",
                ],
                geometry="geometry",
                crs=outfalls.crs,
            )

        geometry_key = [
            "PERMIT_NUM",
            "PERM_FEATURE_NMBR",
        ]

        metadata_cols = [
            "OBJECTID",
            "GIS_NUMBER",
            "TCEQ_PERMIT_NUM",
            "NPDES_NUM",
            "OUTFALL",
            "PERMITTEE",
            "STATUS",
            "DTYPE",
            "SEGMENT",
            "BASIN",
            "HORZ_DATE",
            "LAT_DD",
            "LONG_DD",
        ]

        metadata_cols = [
            col
            for col in metadata_cols
            if col in outfalls.columns
        ]

        candidates = outfalls[
            geometry_key
            + ["geometry"]
            + metadata_cols
        ].copy()

        candidates["_geometry_wkt"] = (
            candidates.geometry.to_wkt()
        )

        if "HORZ_DATE" in candidates.columns:
            candidates[
                "_horz_date_sort"
            ] = pd.to_numeric(
                candidates["HORZ_DATE"],
                errors="coerce",
            )
        else:
            candidates[
                "_horz_date_sort"
            ] = pd.NA

        if "OBJECTID" in candidates.columns:
            candidates[
                "_objectid_sort"
            ] = pd.to_numeric(
                candidates["OBJECTID"],
                errors="coerce",
            )
        else:
            candidates[
                "_objectid_sort"
            ] = pd.NA

        summary = (
            candidates
            .groupby(
                geometry_key,
                dropna=False,
                as_index=False,
            )
            .agg(
                n_geometry_candidates=(
                    "_geometry_wkt",
                    "nunique",
                ),
                candidate_coordinates=(
                    "_geometry_wkt",
                    _join_unique,
                ),
            )
        )

        summary_fields = {
            "GIS_NUMBER": (
                "candidate_GIS_NUMBERs"
            ),
            "TCEQ_PERMIT_NUM": (
                "candidate_TCEQ_PERMIT_NUMs"
            ),
            "DTYPE": "candidate_DTYPES",
            "SEGMENT": "candidate_SEGMENTs",
            "BASIN": "candidate_BASINs",
            "HORZ_DATE": (
                "candidate_HORZ_DATEs"
            ),
        }

        for source_col, summary_col in (
            summary_fields.items()
        ):
            if source_col not in candidates.columns:
                continue

            field_summary = (
                candidates
                .groupby(
                    geometry_key,
                    dropna=False,
                )[source_col]
                .agg(_join_unique)
                .rename(summary_col)
                .reset_index()
            )

            summary = summary.merge(
                field_summary,
                on=geometry_key,
                how="left",
                validate="one_to_one",
            )

        sort_cols = (
            geometry_key
            + [
                "_horz_date_sort",
                "_objectid_sort",
            ]
        )

        ascending = [
            True,
            True,
            False,
            False,
        ]

        if "GIS_NUMBER" in candidates.columns:
            sort_cols.append("GIS_NUMBER")
            ascending.append(True)

        canonical = (
            candidates
            .sort_values(
                sort_cols,
                ascending=ascending,
                na_position="last",
                kind="stable",
            )
            .drop_duplicates(
                subset=geometry_key,
                keep="first",
            )
            .merge(
                summary,
                on=geometry_key,
                how="left",
                validate="one_to_one",
            )
        )

        canonical = gpd.GeoDataFrame(
            canonical,
            geometry="geometry",
            crs=outfalls.crs,
        )

        canonical[
            "geometry_lookup_method"
        ] = "unique_exact_outfall_geometry"

        canonical.loc[
            canonical[
                "n_geometry_candidates"
            ].gt(1),
            "geometry_lookup_method",
        ] = (
            "ranked_multiple_exact_"
            "geometry_candidates"
        )

        if WRITE_DEBUG_FILES:
            canonical.loc[
                canonical[
                    "n_geometry_candidates"
                ].gt(1)
            ].to_csv(
                "return_tceq_multiple_"
                "geometry_candidates.csv",
                index=False,
            )

        return canonical

    @staticmethod
    def _unique_volume(
        df,
        record_cols,
    ):
        """
        Sum one monthly volume per source record from an audit table.
        """
        if (
            df.empty
            or "FLOW_ACFT_MONTH"
            not in df.columns
        ):
            return 0.0

        available_keys = [
            col
            for col in record_cols
            if col in df.columns
        ]

        if not available_keys:
            return float(
                df["FLOW_ACFT_MONTH"].sum()
            )

        unique_rows = df.drop_duplicates(
            subset=available_keys,
            keep="first",
        )

        return float(
            unique_rows[
                "FLOW_ACFT_MONTH"
            ].sum()
        )

    def build(
        self,
        start,
        end,
    ):
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)

        start_fy = fiscal_year(start_ts)
        end_fy = fiscal_year(end_ts)

        # External retrieval and source parsing stay in ReturnFlowSource.
        dmr = self.return_source.load_dmr(
            start_fy,
            end_fy,
        )

        if dmr.empty:
            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        dmr[
            "MONITORING_PERIOD_END_DATE"
        ] = pd.to_datetime(
            dmr[
                "MONITORING_PERIOD_END_DATE"
            ],
            errors="coerce",
        )

        dmr["PARAMETER_CODE"] = (
            self._normalize_code(
                dmr["PARAMETER_CODE"]
            )
        )

        dmr = dmr.loc[
            dmr[
                "MONITORING_PERIOD_END_DATE"
            ].between(
                start_ts,
                end_ts,
                inclusive="both",
            )
            & dmr[
                "PARAMETER_CODE"
            ].eq("50050")
        ].copy()

        dmr["FLOW_MGD"] = pd.to_numeric(
            dmr[
                "DMR_VALUE_STANDARD_UNITS"
            ],
            errors="coerce",
        )

        dmr = dmr.dropna(
            subset=[
                "MONITORING_PERIOD_END_DATE",
                "EXTERNAL_PERMIT_NMBR",
                "PERM_FEATURE_NMBR",
                "FLOW_MGD",
            ]
        )

        if dmr.empty:
            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        dmr["PERMIT_NUM"] = (
            self._normalize_npdes(
                dmr[
                    "EXTERNAL_PERMIT_NMBR"
                ]
            )
        )

        dmr["PERM_FEATURE_NMBR"] = (
            self._normalize_outfall(
                dmr[
                    "PERM_FEATURE_NMBR"
                ]
            )
        )

        dmr = dmr.dropna(
            subset=[
                "PERMIT_NUM",
                "PERM_FEATURE_NMBR",
            ]
        )

        selected_dmr, selection_audit = (
            self._select_return_flow_candidates(
                dmr,
                raise_on_conflicting_ties=True,
            )
        )

        if WRITE_DEBUG_FILES:
            selection_audit.to_csv(
                "return_dmr_selection_audit.csv",
                index=False,
            )

        if selected_dmr.empty:
            print(
                "[ReturnFlow] No representative "
                "average-flow candidates selected"
            )

            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        print(
            "[ReturnFlow] Candidate rows:",
            len(dmr),
        )

        print(
            "[ReturnFlow] Selected "
            "permit-outfall-period rows:",
            len(selected_dmr),
        )

        print(
            "[ReturnFlow] Selected bases:"
        )

        print(
            selected_dmr[
                "selection_basis"
            ]
            .value_counts(dropna=False)
            .to_string()
        )

        feature_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "PERMIT_NUM",
            "PERM_FEATURE_NMBR",
            "STATISTICAL_BASE_CODE",
            "MONITORING_PERIOD_END_DATE",
            "FLOW_MGD",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
            "stat_base_description",
            "selection_basis",
            "selection_status",
        ]

        feature_monthly = (
            selected_dmr[
                feature_cols
            ].copy()
        )

        record_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "PERMIT_NUM",
            "PERM_FEATURE_NMBR",
            "MONITORING_PERIOD_END_DATE",
        ]

        duplicate_selected = (
            feature_monthly
            .duplicated(
                subset=record_cols,
                keep=False,
            )
        )

        if duplicate_selected.any():
            raise AssertionError(
                "Selected DMR data contains duplicate "
                "permit/outfall/period rows:\n"
                + feature_monthly.loc[
                    duplicate_selected
                ]
                .head(50)
                .to_string(index=False)
            )

        feature_monthly[
            "days_in_month"
        ] = (
            feature_monthly[
                "MONITORING_PERIOD_END_DATE"
            ]
            .dt.days_in_month
        )

        feature_monthly[
            "FLOW_ACFT_MONTH"
        ] = (
            mgd_to_afday(
                feature_monthly[
                    "FLOW_MGD"
                ]
            )
            * feature_monthly[
                "days_in_month"
            ]
        )

        if WRITE_DEBUG_FILES:
            self._build_feature_debug(
                feature_monthly
            ).to_csv(
                "return_dmr_feature_flow_debug.csv",
                index=False,
            )

        # TCEQ outfall retrieval remains in ReturnFlowSource.
        outfalls = (
            self.return_source
            .load_perm_feature_nmbrs()
        )
        expected_outfalls = (
            self.return_source
            .last_outfall_expected_count
        )

        retrieved_outfalls = (
            self.return_source
            .last_outfall_retrieved_count
        )

        if (
            expected_outfalls is None
            or retrieved_outfalls is None
            or retrieved_outfalls != expected_outfalls
        ):
            raise RuntimeError(
                "Return-flow processing cannot continue "
                "because the TCEQ outfall retrieval was "
                "not verified as complete. "
                f"Expected={expected_outfalls}; "
                f"retrieved={retrieved_outfalls}."
            )

        canonical_geometry = (
            self._prepare_canonical_outfall_geometry(
                outfalls
            )
        )

        if canonical_geometry.empty:
            print(
                "[ReturnFlow] No valid TCEQ "
                "outfall geometry was available"
            )

            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        # Attach exact geometry or borrow a sibling-outfall
        # geometry from the same permit.
        (
            geometry_assigned,
            geometry_unresolved,
        ) = fill_missing_geometry_from_group(
            feature_monthly,
            canonical_geometry,
            group_col="PERMIT_NUM",
            feature_col="PERM_FEATURE_NMBR",
            geometry_col="geometry",
            donor_sort_cols=[
                "_horz_date_sort",
                "_objectid_sort",
                "PERM_FEATURE_NMBR",
            ],
            assignment_col=(
                "geometry_assignment_method"
            ),
            donor_feature_col=(
                "geometry_donor_outfall"
            ),
        )

        if WRITE_DEBUG_FILES:
            geometry_assigned.to_csv(
                "return_dmr_geometry_"
                "assignment_audit.csv",
                index=False,
            )

            geometry_unresolved.to_csv(
                "return_dmr_geometry_"
                "unresolved.csv",
                index=False,
            )

        if not geometry_unresolved.empty:
            print(
                "[ReturnFlow] Selected "
                "outfall-period rows without "
                "any permit geometry: "
                f"{len(geometry_unresolved)}"
            )

        if geometry_assigned.empty:
            print(
                "[ReturnFlow] No selected records "
                "have exact or permit-fallback "
                "geometry"
            )

            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        points = gpd.GeoDataFrame(
            geometry_assigned,
            geometry="geometry",
            crs=canonical_geometry.crs,
        )

        watersheds = (
            self.ctx.registry
            .load_watersheds()[
                [
                    "WS_ID",
                    "Estuary",
                    "HAS_UNGAGE",
                    "geometry",
                ]
            ]
            .copy()
        )

        (
            assigned,
            spatial_unresolved,
            spatial_audit,
        ) = assign_geometries_to_watersheds(
            points,
            watersheds,
            record_cols=record_cols,
            ws_col="WS_ID",
            estuary_col="Estuary",
            include_flag_col="HAS_UNGAGE",
            include_flag_value=1,
            predicate="intersects",
        )

        if WRITE_DEBUG_FILES:
            spatial_audit.to_csv(
                "return_dmr_spatial_"
                "assignment_audit.csv",
                index=False,
            )

            spatial_unresolved.to_csv(
                "return_dmr_spatial_"
                "unresolved.csv",
                index=False,
            )

        if not spatial_unresolved.empty:
            print(
                "[ReturnFlow] Spatially "
                "unresolved selected "
                "outfall-period rows: "
                f"{len(spatial_unresolved)}"
            )

        if assigned.empty:
            print(
                "[ReturnFlow] No selected outfalls "
                "were assigned to eligible "
                "ungaged watersheds"
            )

            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        spatial_duplicates = (
            assigned.duplicated(
                subset=record_cols,
                keep=False,
            )
        )

        if spatial_duplicates.any():
            display_cols = record_cols + [
                "WS_ID",
                "Estuary",
                "candidate_WS_IDs",
                "candidate_estuaries",
                "spatial_assignment_method",
            ]

            display_cols = [
                col
                for col in display_cols
                if col in assigned.columns
            ]

            raise AssertionError(
                "Spatial assignment produced more "
                "than one calculation row for a "
                "selected outfall-period:\n"
                + assigned.loc[
                    spatial_duplicates,
                    display_cols,
                ]
                .head(50)
                .to_string(index=False)
            )

        # Flow-volume conservation:
        #
        # selected = assigned + geometry unresolved
        #            + spatial unresolved
        selected_volume = float(
            feature_monthly[
                "FLOW_ACFT_MONTH"
            ].sum()
        )

        assigned_volume = (
            self._unique_volume(
                assigned,
                record_cols,
            )
        )

        geometry_unresolved_volume = (
            self._unique_volume(
                geometry_unresolved,
                record_cols,
            )
        )

        spatial_unresolved_volume = (
            self._unique_volume(
                spatial_unresolved,
                record_cols,
            )
        )

        accounted_volume = (
            assigned_volume
            + geometry_unresolved_volume
            + spatial_unresolved_volume
        )

        tolerance = max(
            1e-6,
            abs(selected_volume) * 1e-10,
        )

        if abs(
            selected_volume
            - accounted_volume
        ) > tolerance:
            raise AssertionError(
                "Return-flow volume was not "
                "conserved through geometry and "
                "spatial assignment. "
                f"Selected={selected_volume}, "
                f"assigned={assigned_volume}, "
                "geometry_unresolved="
                f"{geometry_unresolved_volume}, "
                "spatial_unresolved="
                f"{spatial_unresolved_volume}."
            )

        assigned[
            "MONITORING_PERIOD_END_DATE"
        ] = pd.to_datetime(
            assigned[
                "MONITORING_PERIOD_END_DATE"
            ],
            errors="coerce",
        )

        assigned["year"] = (
            assigned[
                "MONITORING_PERIOD_END_DATE"
            ].dt.year
        )

        assigned["month"] = (
            assigned[
                "MONITORING_PERIOD_END_DATE"
            ].dt.month
        )

        assigned = assigned.dropna(
            subset=[
                "WS_ID",
                "Estuary",
                "year",
                "month",
                "FLOW_ACFT_MONTH",
            ]
        )

        if WRITE_DEBUG_FILES:
            assigned.to_csv(
                "return_dmr_outfall_join_audit.csv",
                index=False,
            )

        monthly_ws = (
            assigned
            .groupby(
                [
                    "WS_ID",
                    "Estuary",
                    "year",
                    "month",
                ],
                as_index=False,
            )
            .agg(
                value_acft_month=(
                    "FLOW_ACFT_MONTH",
                    "sum",
                )
            )
        )

        daily = expand_monthly_to_daily(
            monthly_ws.rename(
                columns={
                    "value_acft_month": "value",
                    "WS_ID": "ws_id",
                    "Estuary": "estuary",
                }
            ),
            year_col="year",
            month_col="month",
            value_col="value",
            id_col="ws_id",
            estuary_col="estuary",
            value_is_monthly_total=True,
        )

        if daily.empty:
            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        daily["value_afday"] = daily["value"]
        daily["id"] = daily["ws_id"]
        daily["id_type"] = "watershed"
        daily["component"] = self.name
        daily["source"] = "epa_dmr"
        daily["flow_role"] = "return"
        daily["count_in_basin_sum"] = 1
        daily["data_as_of"] = end_ts
        daily["note"] = None

        return daily[REQUIRED_OUT_COLS]

    def run(
        self,
        start=None,
        end=None,
    ):
        if start is None:
            watermark = (
                self.ctx.storage
                .get_watermark(
                    self.name,
                    default="2015-01-01",
                )
            )

            start = (
                watermark
                + pd.Timedelta(days=1)
                if watermark is not None
                else pd.Timestamp(
                    "2015-01-01"
                )
            )

        if end is None:
            end = (
                pd.Timestamp.utcnow()
                .normalize()
            )

        print(
            f"[ReturnFlow] Running "
            f"{start} -> {end}"
        )

        df = self.build(
            start,
            end,
        )

        if df.empty:
            print(
                "[ReturnFlow] No rows written"
            )

            return 0

        n_rows = self.ctx.storage.append(
            df,
            run_start=start,
            run_end=end,
        )

        self.ctx.storage.set_watermark(
            self.name,
            df["date"].max(),
        )

        return n_rows


def build_return(monthly_df):
    df = expand_monthly_to_daily(
        monthly_df
    )

    df["component"] = "return"
    df["flow_role"] = "return"
    df["count_in_basin_sum"] = 1

    return df
