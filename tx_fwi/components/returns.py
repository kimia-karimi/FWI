# components/return.py
from __future__ import annotations
from tx_fwi.transforms.temporal import expand_monthly_to_daily
import pandas as pd
import geopandas as gpd
import requests
from tx_fwi.components.base import RunContext
from tx_fwi.sources.base import registry
from tx_fwi.transforms.units import mgd_to_afday
import io
from tx_fwi.sources.epa_dmr import ReturnFlowSource
import certifi
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



def fiscal_year(dt):
    dt = pd.Timestamp(dt)
    return dt.year + 1 if dt.month >= 10 else dt.year

class ReturnComponent:

    name = "return"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.return_source = ReturnFlowSource(ctx=ctx)
    @staticmethod
    def _normalize_npdes(series):
        return (
            series
            .astype(str)
            .str.replace(r"\D+", "", regex=True)
            .str.lstrip("0")
            .replace("", pd.NA)
        )

    @staticmethod
    def _normalize_outfall(series):
        return (
            series
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
           # .str.replace(r"\D+", "", regex=True)
            .str.lstrip("0")
            .replace("", pd.NA)
        )

    @staticmethod
    def _first_existing_col(df, candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    def _write_debug_csv(self, debug_df):
        
        debug_df.to_csv( "return_dmr_feature_flow_debug.csv", index=False)

        print(f"[ReturnFlow] Wrote debug CSV")

    def _build_feature_debug(self, feature_monthly):
        """
        One row per permit/month with one MGD column per feature/PERM_FEATURE_NMBR,
        plus total MGD and total acre-ft/month.
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
            c
            if c in [
                "EXTERNAL_PERMIT_NMBR",
                "MONITORING_PERIOD_END_DATE",
            ]
            else f"feature_{c}_mgd"
            for c in debug.columns
        ]

        feature_cols = [
            c
            for c in debug.columns
            if c.startswith("feature_")
            and c.endswith("_mgd")
        ]

        debug["overall_flow_mgd"] = debug[feature_cols].sum(axis=1)

        debug["days_in_month"] = (
            pd.to_datetime(debug["MONITORING_PERIOD_END_DATE"])
            .dt.days_in_month
        )
        #convert mgd to afd and multiply by the number of days in a month 
        debug["overall_flow_acft_month"] = mgd_to_afday(debug["overall_flow_mgd"]) * debug["days_in_month"]
            

        return debug
    def _select_return_flow_candidates(
        self,
        dmr: pd.DataFrame,
        *,
        raise_on_conflicting_ties: bool = True,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Select one representative flow value per permit/feature/period.

        Substantive priority:
        1. AVG statistical-base type.
        2. Monitoring location: 1, EG, Y, EA, ED, E1, E2, E3, then other.
        3. Statistical base: DB, MB, AB, QB, then other.
        4. Exact or unqualified values.
        5. Rows without a NODI code.

        Tie breakers:
        1. Most recent VALUE_RECEIVED_DATE.
        2. Highest available DMR/source identifier.

        Equal-priority candidates with different flow values raise an error by
        default. They are never summed automatically.
        """
        if dmr.empty:
            return dmr.copy(), dmr.copy()

        work = dmr.copy().reset_index(drop=True)
        work["_candidate_id"] = range(len(work))

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
        missing_cols = [col for col in required_cols if col not in work.columns]
        if missing_cols:
            raise KeyError(
                "Cannot select representative DMR flow records. Missing columns: "
                + ", ".join(missing_cols)
            )

        work["MONITORING_PERIOD_END_DATE"] = pd.to_datetime(
            work["MONITORING_PERIOD_END_DATE"],
            errors="coerce",
        )
        work["FLOW_MGD"] = pd.to_numeric(
            work["DMR_VALUE_STANDARD_UNITS"],
            errors="coerce",
        )

        code_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
            "DMR_VALUE_QUALIFIER_CODE",
            "LIMIT_VALUE_QUALIFIER_CODE",
            "NODI_CODE",
            "VALUE_TYPE_CODE",
            "PARAMETER_CODE",
        ]
        for col in code_cols:
            if col in work.columns:
                work[col] = self._normalize_code(work[col])

        work = work.dropna(subset=group_cols + ["FLOW_MGD"]).copy()
        if work.empty:
            return work.copy(), work.copy()

        negative = work["FLOW_MGD"] < 0
        if negative.any():
            sample = work.loc[
                negative,
                group_cols + ["FLOW_MGD"],
            ].head(20)
            raise ValueError(
                "Negative EPA DMR flow values require review:\n"
                + sample.to_string(index=False)
            )

        base_type_priority = {"AVG": 0}
        work["_base_type_rank"] = (
            work["STATISTICAL_BASE_TYPE_CODE"]
            .map(base_type_priority)
            .fillna(90)
            .astype(int)
        )

        location_priority = {
            "1": 0,
            "EG": 1,
            "Y": 2,
            "EA": 3,
            "ED": 4,
            "E1": 5,
            "E2": 6,
            "E3": 7,
        }
        work["_location_rank"] = (
            work["MONITORING_LOCATION_CODE"]
            .map(location_priority)
            .fillna(90)
            .astype(int)
        )

        stat_base_priority = {
            "DB": 0,
            "MB": 1,
            "AB": 2,
            "QB": 3,
        }
        work["_stat_base_rank"] = (
            work["STATISTICAL_BASE_CODE"]
            .map(stat_base_priority)
            .fillna(90)
            .astype(int)
        )

        if "DMR_VALUE_QUALIFIER_CODE" in work.columns:
            qualifier = work["DMR_VALUE_QUALIFIER_CODE"]
            work["_qualifier_rank"] = 10
            work.loc[qualifier.isna() | qualifier.eq("="), "_qualifier_rank"] = 0
        else:
            work["_qualifier_rank"] = 0

        if "NODI_CODE" in work.columns:
            work["_nodi_rank"] = work["NODI_CODE"].notna().astype(int)
        else:
            work["_nodi_rank"] = 0

        if "VALUE_RECEIVED_DATE" in work.columns:
            work["_received_date"] = pd.to_datetime(
                work["VALUE_RECEIVED_DATE"],
                errors="coerce",
            )
        else:
            work["_received_date"] = pd.NaT

        work["selection_basis"] = (
            "location="
            + work["MONITORING_LOCATION_CODE"].fillna("missing")
            + ";stat_base="
            + work["STATISTICAL_BASE_CODE"].fillna("missing")
            + ";stat_type="
            + work["STATISTICAL_BASE_TYPE_CODE"].fillna("missing")
        )

        work["selection_tier"] = (
            work["_base_type_rank"].astype(str)
            + "-"
            + work["_location_rank"].astype(str)
            + "-"
            + work["_stat_base_rank"].astype(str)
        )

        exact_candidate_cols = group_cols + [
            "PARAMETER_CODE",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
            "FLOW_MGD",
        ]
        exact_candidate_cols = [
            col for col in exact_candidate_cols if col in work.columns
        ]

        work["_candidate_copy_count"] = (
            work
            .groupby(exact_candidate_cols, dropna=False)["FLOW_MGD"]
            .transform("size")
        )
        work = work.drop_duplicates(
            subset=exact_candidate_cols,
            keep="first",
        ).copy()

        substantive_rank_cols = [
            "_base_type_rank",
            "_location_rank",
            "_stat_base_rank",
            "_qualifier_rank",
            "_nodi_rank",
        ]

        # Rank candidates lexicographically, not independently by each minimum.
        work["_priority_tuple"] = list(
            zip(*(work[col] for col in substantive_rank_cols))
        )
        work["_best_priority_tuple"] = (
            work
            .groupby(group_cols, dropna=False)["_priority_tuple"]
            .transform("min")
        )
        work["_is_top_priority_candidate"] = work["_priority_tuple"].eq(
            work["_best_priority_tuple"]
        )

        top_candidates = work[work["_is_top_priority_candidate"]].copy()
        top_summary = (
            top_candidates
            .groupby(group_cols, dropna=False, as_index=False)
            .agg(
                n_top_candidates=("FLOW_MGD", "size"),
                n_top_flow_values=("FLOW_MGD", "nunique"),
                min_top_flow_mgd=("FLOW_MGD", "min"),
                max_top_flow_mgd=("FLOW_MGD", "max"),
            )
        )

        conflicting_groups = top_summary[
            top_summary["n_top_flow_values"] > 1
        ].copy()
        if not conflicting_groups.empty:
            conflict_rows = top_candidates.merge(
                conflicting_groups[group_cols],
                on=group_cols,
                how="inner",
            )
            display_cols = group_cols + [
                "MONITORING_LOCATION_CODE",
                "STATISTICAL_BASE_CODE",
                "STATISTICAL_BASE_TYPE_CODE",
                "FLOW_MGD",
                "DMR_VALUE_QUALIFIER_CODE",
                "VALUE_RECEIVED_DATE",
                "DMR_VALUE_ID",
                "selection_basis",
            ]
            display_cols = [
                col for col in display_cols if col in conflict_rows.columns
            ]
            message = (
                "Conflicting top-ranked EPA DMR return-flow candidates were "
                "found. Equal-priority records have different flow values and "
                "were not summed:\n"
                + conflict_rows[display_cols].head(50).to_string(index=False)
            )
            if raise_on_conflicting_ties:
                raise ValueError(message)
            print("[ReturnFlow] WARNING: " + message)

        sort_cols = group_cols + substantive_rank_cols + ["_received_date"]
        ascending = [True] * len(group_cols) + [True] * len(
            substantive_rank_cols
        ) + [False]

        for optional_id in [
            "DMR_VALUE_ID",
            "DMR_FORM_VALUE_ID",
            "DMR_EVENT_ID",
            "LIMIT_VALUE_ID",
            "LIMIT_ID",
        ]:
            if optional_id in work.columns:
                sort_col = f"_sort_{optional_id}"
                work[sort_col] = pd.to_numeric(
                    work[optional_id],
                    errors="coerce",
                )
                sort_cols.append(sort_col)
                ascending.append(False)

        work = work.sort_values(
            sort_cols,
            ascending=ascending,
            na_position="last",
            kind="stable",
        ).copy()

        selected = work.drop_duplicates(
            subset=group_cols,
            keep="first",
        ).copy()
        selected = selected.merge(
            top_summary,
            on=group_cols,
            how="left",
            validate="one_to_one",
        )

        selected["selection_status"] = "selected_unique"
        selected.loc[
            (selected["n_top_candidates"] > 1)
            & (selected["n_top_flow_values"] == 1),
            "selection_status",
        ] = "selected_from_equal_value_tie"
        selected.loc[
            selected["n_top_flow_values"] > 1,
            "selection_status",
        ] = "selected_from_conflicting_tie"

        selected_counts = selected.groupby(
            group_cols,
            dropna=False,
        ).size()
        if (selected_counts != 1).any():
            raise AssertionError(
                "Candidate selection did not produce exactly one row per "
                "permit/feature/monitoring period."
            )

        selected_candidate_ids = set(selected["_candidate_id"])
        audit = work.copy()
        audit["selection_result"] = "not_selected"
        audit.loc[
            audit["_candidate_id"].isin(selected_candidate_ids),
            "selection_result",
        ] = "selected"
        audit.loc[
            audit["_is_top_priority_candidate"]
            & ~audit["_candidate_id"].isin(selected_candidate_ids),
            "selection_result",
        ] = "top_priority_tie_not_selected"

        cleanup_cols = [
            col
            for col in selected.columns
            if col.startswith("_best_")
            or col.startswith("_sort_")
            or col == "_priority_tuple"
        ]
        selected = selected.drop(columns=cleanup_cols, errors="ignore")

        return selected, audit
    # --------------------------------------------------
    # Main build
    # --------------------------------------------------
    def build(self, start, end):
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        start_fy = fiscal_year(start_ts)
        end_fy = fiscal_year(end_ts)
        source = ReturnFlowSource(ctx=self.ctx)

        dmr = source._load_dmr(start_fy, end_fy)

        #print("Loaded DMR rows:", len(dmr))

        if dmr.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        # --------------------------------------------------
        # Flow records only
        # --------------------------------------------------
        #print(dmr.columns)
        #dmr_st = dmr.loc[dmr['EXTERNAL_PERMIT_NMBR']=='TX0000027' , ['MONITORING_PERIOD_END_DATE','PERM_FEATURE_NMBR', 'DMR_VALUE_STANDARD_UNITS']].sort_values(by='MONITORING_PERIOD_END_DATE', ascending=True).head(10)
        #print("DMR for TX0000027 before dedup:", pd.DataFrame(dmr_st))
        dmr["MONITORING_PERIOD_END_DATE"] = pd.to_datetime(
            dmr["MONITORING_PERIOD_END_DATE"],
            errors="coerce",
        )
        #print(dmr["MONITORING_PERIOD_END_DATE"] .min(), dmr["MONITORING_PERIOD_END_DATE"] .max())
        dmr = dmr[
            (dmr["MONITORING_PERIOD_END_DATE"] >= start_ts)
            & (dmr["MONITORING_PERIOD_END_DATE"] <= end_ts)
            & (dmr["PARAMETER_CODE"].astype(str).str.strip() == "50050")
            #& (dmr["STATISTICAL_BASE_CODE"].astype(str) == "DB") #daily average code
        ].copy()

        #print("Flow rows after date and parameter filter:", len(dmr))
        dmr["FLOW_MGD"] = pd.to_numeric(
            dmr["DMR_VALUE_STANDARD_UNITS"],
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
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)
        # --------------------------------------------------
        # Permit, PERM_FEATURE_NMBR, and ID normalization
        # --------------------------------------------------
        
        dmr["PERMIT_NUM"] = self._normalize_npdes(
            dmr["EXTERNAL_PERMIT_NMBR"]
        )

        dmr["PERM_FEATURE_NMBR"] = self._normalize_outfall(
            dmr["PERM_FEATURE_NMBR"]
        )
        dmr = dmr.dropna(
            subset=[
                "PERMIT_NUM","PERM_FEATURE_NMBR",
            ]
        )
        selected_dmr, selection_audit = self._select_return_flow_candidates(
            dmr,
            raise_on_conflicting_ties=True,
        )

        if selected_dmr.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        print("[ReturnFlow] Candidate rows:", len(dmr))
        print(
            "[ReturnFlow] Selected permit-feature-period rows:",
            len(selected_dmr),
        )
        print("[ReturnFlow] Selected bases:")
        print(
            selected_dmr["selection_basis"]
            .value_counts(dropna=False)
            .to_string()
        )

        selection_audit.to_csv(
            "return_dmr_selection_audit.csv",
            index=False,
        )
        # --------------------------------------------------
        # Avoid inflating flow because one reported DMR value
        # can appear more than once due to limit rows.
        # --------------------------------------------------
        feature_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "PERMIT_NUM",
            "PERM_FEATURE_NMBR", #multiple feature/PERM_FEATURE_NMBR may be reported separately
            "STATISTICAL_BASE_CODE",
            "MONITORING_PERIOD_END_DATE",
            "PARAMETER_CODE",
            "DMR_VALUE_ID",
            "FLOW_MGD",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
            "selection_basis",
            "selection_status",
        ]

        feature_monthly = selected_dmr[feature_cols].copy()

        duplicate_selected = feature_monthly.duplicated(
            subset=[
                "EXTERNAL_PERMIT_NMBR",
                "PERMIT_NUM",
                "PERM_FEATURE_NMBR",
                "MONITORING_PERIOD_END_DATE",
            ],
            keep=False,
        )
        if duplicate_selected.any():
            raise AssertionError(
                "Selected DMR data contains duplicate permit/feature/period rows:\n"
                + feature_monthly.loc[duplicate_selected]
                .head(50)
                .to_string(index=False)
            )
        #dmr_st = dmr.loc[dmr['EXTERNAL_PERMIT_NMBR']=='TX0000027' , ['MONITORING_PERIOD_END_DATE','PERM_FEATURE_NMBR', 'DMR_VALUE_STANDARD_UNITS']].sort_values(by='MONITORING_PERIOD_END_DATE', ascending=True)
        #print("DMR for TX0000027:", pd.DataFrame(dmr_st))
        #print("DMR length after dedup:", len(dmr))
        # --------------------------------------------------
        # Monthly feature-level flow
        #
        #
        # keep each permit PERM_FEATURE_NMBR separate before spatial join.
        # --------------------------------------------------
        
        #print("Feature monthly rows:", len(feature_monthly))
        #print(feature_monthly[ ["PERMIT_NUM","PERM_FEATURE_NMBR"]].head(20))
        feature_monthly["days_in_month"] = (
            feature_monthly["MONITORING_PERIOD_END_DATE"]
            .dt.days_in_month
        )

        feature_monthly["FLOW_ACFT_MONTH"] = mgd_to_afday(feature_monthly["FLOW_MGD"])*feature_monthly["days_in_month"]
        # Load and normalize TCEQ outfall geometry.
        perm_feature_nmbrs = self.return_source.load_perm_feature_nmbrs()
        perm_feature_nmbrs["PERMIT_NUM"] = self._normalize_npdes(
            perm_feature_nmbrs["NPDES_NUM"]
        )
        perm_feature_nmbrs["PERM_FEATURE_NMBR"] = self._normalize_outfall(
            perm_feature_nmbrs["OUTFALL"]
        )

        print(
            "[ReturnFlow] PERM_FEATURE_NMBRs rows before dropna:",
            len(perm_feature_nmbrs),
        )
        perm_feature_nmbrs = perm_feature_nmbrs.dropna(
            subset=["PERMIT_NUM", "PERM_FEATURE_NMBR", "geometry"]
        )
        print(
            "[ReturnFlow] PERM_FEATURE_NMBRs rows after dropna:",
            len(perm_feature_nmbrs),
        )

        duplicated_geometry_keys = perm_feature_nmbrs.duplicated(
            subset=["PERMIT_NUM", "PERM_FEATURE_NMBR"],
            keep=False,
        )
        if duplicated_geometry_keys.any():
            geometry_conflicts = (
                perm_feature_nmbrs.loc[duplicated_geometry_keys]
                .groupby(["PERMIT_NUM", "PERM_FEATURE_NMBR"])
                .size()
            )
            print(
                "[ReturnFlow] Duplicate TCEQ permit-feature geometry keys "
                f"found for {len(geometry_conflicts)} keys; keeping one geometry "
                "per key."
            )

        perm_feature_nmbrs_feature = (
            perm_feature_nmbrs
            .drop_duplicates(
                subset=["PERMIT_NUM", "PERM_FEATURE_NMBR"],
                keep="first",
            )[
                ["PERMIT_NUM", "PERM_FEATURE_NMBR", "geometry"]
            ]
        )

        
        
        # --------------------------------------------------
        # Debug CSV:
        # permit, monitoring date, feature flow columns,
        # overall MGD, and overall acre-ft/month.
        # --------------------------------------------------
        #debug = self._build_feature_debug(feature_monthly)
        #self._write_debug_csv(debug)
        
        # --------------------------------------------------
        # Load PERM_FEATURE_NMBRs
        # --------------------------------------------------
        
        #resp = requests.get(
            #self.return_source.PERM_FEATURE_NMBR_URL,timeout=120,verify=certifi.where(),)

        #resp.raise_for_status()

        #geojson = resp.json()
        

        #PERM_FEATURE_NMBRs = gpd.GeoDataFrame.from_features( geojson["features"], crs="EPSG:4326",)
        #print("PERM_FEATURE_NMBRs rows:", len(PERM_FEATURE_NMBRs))
        #print(PERM_FEATURE_NMBRs.columns.tolist())
       # PERM_FEATURE_NMBRs["PERMIT_NUM"] = self._normalize_npdes(
            #PERM_FEATURE_NMBRs["NPDES_NUM"]
        #)
        #PERM_FEATURE_NMBRs["PERM_FEATURE_NMBR"] = self._normalize_outfall(PERM_FEATURE_NMBRs["OUTFALL"])
        #print("PERM_FEATURE_NMBRs rows before dropna:", len(PERM_FEATURE_NMBRs))
        #PERM_FEATURE_NMBRs = PERM_FEATURE_NMBRs.dropna(
            #subset=[
                #"PERMIT_NUM",
               # "PERM_FEATURE_NMBR",
               # "geometry",
            #]
        #)
        #print("PERM_FEATURE_NMBRs rows after dropna:", len(PERM_FEATURE_NMBRs))
        #print(PERM_FEATURE_NMBRs.columns.tolist())
        #print(PERM_FEATURE_NMBRs[ ["PERMIT_NUM","PERM_FEATURE_NMBR"]].head(20))
        # --------------------------------------------------
        # One geometry per permit + PERM_FEATURE_NMBR.
        #
        # This prevents the old issue:
        # joining permit total to all PERM_FEATURE_NMBRs and multiplying flow.
        # --------------------------------------------------
        #PERM_FEATURE_NMBRs_feature = (
            #PERM_FEATURE_NMBRs
            #.drop_duplicates(
               # subset=[
                   # "PERMIT_NUM",
                   # "PERM_FEATURE_NMBR",
               # ]
            #)
            #[
               # [
                   # "PERMIT_NUM",
                    #"PERM_FEATURE_NMBR",
                   # "geometry",
                #]
           # ]
      #  )
 

        # --------------------------------------------------
        # Join DMR permit + feature -> TCEQ permit + PERM_FEATURE_NMBR
        # --------------------------------------------------
        dmr_geo = feature_monthly.merge(
            PERM_FEATURE_NMBRs_feature,
            on=["PERMIT_NUM","PERM_FEATURE_NMBR"],
            how="left",
            indicator= True,
            validate="many_to_one"
        )
        
        # Optional join-quality debug

        join_debug_cols[
            [
                "EXTERNAL_PERMIT_NMBR",
                "PERMIT_NUM",
                "PERM_FEATURE_NMBR",
                "MONITORING_PERIOD_END_DATE",
                "STATISTICAL_BASE_CODE",
                "MONITORING_LOCATION_CODE",
                "STATISTICAL_BASE_TYPE_CODE",
                "selection_basis",
                "FLOW_MGD",
                "FLOW_ACFT_MONTH",
                "_merge",
            ]
        ].to_csv(
           "return_dmr_PERM_FEATURE_NMBR_join_debug.csv",
            index=False,
        )
        #print(dmr_geo["_merge"].value_counts(dropna=False))

        missing_geo = dmr_geo[dmr_geo["_merge"] == "left_only"].copy()

        if not missing_geo.empty:
            missing_geo[
                join_debug_cols[:-1]
            ].to_csv(
                "return_dmr_missing_PERM_FEATURE_NMBR_geometry.csv",
                index=False,
            )

            print(
                "[ReturnFlow] Missing PERM_FEATURE_NMBR geometry rows: "
                f"{len(missing_geo)}. "
                "See return_dmr_missing_PERM_FEATURE_NMBR_geometry.csv"
            )

        dmr_geo = dmr_geo[
            dmr_geo["_merge"] == "both"
        ].drop(columns=["_merge"]).dropna(subset=["geometry"]
        )

        if dmr_geo.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        dmr_geo = gpd.GeoDataFrame(
            dmr_geo,
            geometry="geometry",
            crs="EPSG:4326",
        )

        # --------------------------------------------------
        # Watershed registry
        # --------------------------------------------------
        watersheds = (
            self.ctx.registry
            .load_watersheds()
            [["WS_ID", "Estuary", "HAS_UNGAGE", "geometry"]]
        )

        watersheds = watersheds.to_crs(
            dmr_geo.crs
        )
        #calculate only for ungaged watersheds.
        dmr_geo = gpd.sjoin(
            dmr_geo,
            watersheds[watersheds["HAS_UNGAGE"] == 1],
            how="inner",
            predicate="intersects",
        )

        if dmr_geo.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        # --------------------------------------------------
        # Date fields
        # --------------------------------------------------
        dmr_geo["year"] = (
            dmr_geo["MONITORING_PERIOD_END_DATE"]
            .dt.year
        )

        dmr_geo["month"] = (
            dmr_geo["MONITORING_PERIOD_END_DATE"]
            .dt.month
        )

        # --------------------------------------------------
        # Aggregate by watershed/month
        # Use FLOW_ACFT_MONTH, not FLOW_MGD.
        # This is already total monthly volume.
        # --------------------------------------------------
        monthly_ws = (
            dmr_geo
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
                value_acft_month=("FLOW_ACFT_MONTH", "sum"),
                permits_included=(
                    "EXTERNAL_PERMIT_NMBR",
                    lambda x: ";".join(
                        sorted(x.astype(str).unique())
                    ),
                ),
                PERM_FEATURE_NMBRs_included=(
                    "PERM_FEATURE_NMBR",
                    lambda x: ";".join(
                        sorted(x.astype(str).unique())
                    ),
                ),
                n_permit_PERM_FEATURE_NMBRs=(
                    "PERM_FEATURE_NMBR",
                    "size",
                ),
            )
        )

        # --------------------------------------------------
        # Expand monthly -> daily
        # No extra MGD conversion here.
        # The conversion already happened once above.
        # --------------------------------------------------
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

        # --------------------------------------------------
        # Convert MGD -> AFD
        # --------------------------------------------------
        daily["value_afday"] = daily["value"]

        # --------------------------------------------------
        # Canonical schema
        # --------------------------------------------------
        daily["id"] = daily["ws_id"]

        daily["id_type"] = "watershed"

        daily["component"] = self.name

        daily["source"] = "epa_dmr"

        daily["flow_role"] = "return"

        daily["count_in_basin_sum"] = 1
        daily["data_as_of"] = pd.Timestamp(end)

        daily["note"] = None

        return daily[
            REQUIRED_OUT_COLS
        ]

    # --------------------------------------------------
    def run(self, start=None, end=None):

        if start is None:
            wm = self.ctx.storage.get_watermark(
                self.name,
                default="2015-01-01",
            )

            start = (
                wm + pd.Timedelta(days=1)
                if wm is not None
                else pd.Timestamp("2015-01-01")
            )

        if end is None:
            end = (
                pd.Timestamp.utcnow()
                .normalize()
            )

        print(
            f"[ReturnFlow] Running {start} → {end}"
        )

        df = self.build(start, end)

        if df.empty:
            print("[ReturnFlow] No rows written")
            return 0

        n = self.ctx.storage.append(df ,run_start=start, run_end= end)

        self.ctx.storage.set_watermark(
            self.name,
            df["date"].max(),
        )

        return n
        
def build_return(monthly_df):

    df = expand_monthly_to_daily(monthly_df)

    df["component"] = "return"
    df["flow_role"] = "direct"
    df["count_in_basin_sum"] = 1

    return df
