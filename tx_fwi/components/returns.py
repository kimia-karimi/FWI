# tx_fwi/components/returns.py
from __future__ import annotations
from pathlib import Path
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
    "date", "id", "id_type", "estuary", "component", "source",
    "value_afday", "flow_role", "count_in_basin_sum", "data_as_of", "note",
]

WRITE_DEBUG_FILES = True
COASTAL_REVIEW_DISTANCE_MILES = 10.0
DISTANCE_CRS = "EPSG:5070"
METERS_PER_MILE = 1609.344

MONITORING_LOCATION_PRIORITY = {
    "1": 0, "EG": 1, "Y": 2, "EA": 3,
    "ED": 4, "E1": 5, "E2": 6, "E3": 7,
}

# Existing ranking retained. Unknown codes receive rank 900.
STATISTICAL_BASE_MAP = {
    "MK": (0, "Monthly Average", True),
    "3C": (1, "30 Day Average", True),
    "DB": (2, "Daily Average", True),
    "1H": (3, "1 Day Average", False),
    "DG": (4, "Discharge Per Day Average", False),
    "AF": (5, "Average", False),
    "AH": (6, "Average Value", False),
    "AE": (7, "Arithmetic Mean", False),
    "MC": (8, "Mean", False),
    "RB": (9, "Reported Average", False),
    "NA": (10, "Non-Specific Average", False),
    "A1": (11, "Average (Data Migration)", False),
    "3B": (15, "30 Day Arithmetic", True),
    "3F": (16, "30 Day Arithmetic Mean", True),
    "3A": (17, "30 Day Geometric Mean", True),
    "3D": (18, "30 Day Geometric", True),
    "3H": (19, "30 Day Average Geometric", True),
    "ML": (20, "Monthly Geometric", True),
    "MM": (21, "Monthly Geometric Mean", True),
    "M4": (22, "Monthly Average Geometric", True),
    "LB": (23, "Logarithmic Monthly Median", False),
    "M3": (24, "Monthly Median", False),
    "DP": (30, "14 Day Average", False),
    "7A": (31, "7 Day Average", False),
    "WA": (32, "Weekly Average", False),
    "HB": (33, "High Weekly Average", False),
    "HA": (34, "High 7 Day Average", False),
    "4A": (35, "4 Day Average", False),
    "5B": (36, "5 Day Average", False),
    "1C": (37, "12 Day Average", False),
    "9B": (38, "90 Day Average", False),
    "1F": (39, "120 Day Average", False),
    "QA": (50, "Quarterly Average", False),
    "QR": (51, "Quarterly Rolling Average", False),
    "SC": (52, "Semi-Annual Average", False),
    "6D": (53, "6 Month Average", False),
    "AB": (54, "Annual Average", False),
    "1D": (55, "12 Month Average", False),
    "1M": (56, "Month 1 Average", False),
    "2M": (57, "Month 2 Average", False),
    "3M": (58, "Month 3 Average", False),
    "RA": (59, "Rolling Average", False),
    "RE": (60, "Individual 12 Month Rolling Average", False),
    "RF": (61, "Aggregate 12 Month Rolling Average", False),
    "5Y": (62, "5 Year Average", False),
    "DA": (70, "Daily Geometric Average", True),
    "DM": (71, "Daily Geometric", False),
    "GA": (72, "Geometric Mean", False),
    "LA": (73, "Logarithmic Mean", False),
    "MD": (74, "Median", False),
    "DF": (75, "Daily Median", False),
    "SE": (76, "Single Reading", False),
    "VA": (77, "Value", False),
    "DN": (78, "Discharged", False),
}

STATISTICAL_TYPE_PRIORITY = {"AVG": 0, "MEAN": 1, "MEDIAN": 2}


def fiscal_year(value):
    value = pd.Timestamp(value)
    return value.year + 1 if value.month >= 10 else value.year


def _join_unique(values):
    result = sorted({
        str(value).strip() for value in values
        if value is not None and not pd.isna(value) and str(value).strip()
    })
    return ";".join(result) if result else pd.NA


def _join_unique_numbers(values):
    result = sorted({float(value) for value in values if not pd.isna(value)})
    return ";".join(f"{value:.12g}" for value in result) if result else pd.NA


class ReturnComponent:
    name = "return"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.return_source = ReturnFlowSource(ctx=ctx)

    @staticmethod
    def _normalize_npdes(series):
        return (
            series.astype("string").str.replace(r"\D+", "", regex=True)
            .str.lstrip("0").replace("", pd.NA)
        )

    @staticmethod
    def _normalize_outfall(series):
        return (
            series.astype("string").str.strip().str.upper()
            .str.replace(r"\.0$", "", regex=True)
            .str.replace(r"^0+(?=[0-9])", "", regex=True)
            .replace("", pd.NA)
        )

    @staticmethod
    def _normalize_code(series):
        return (
            series.astype("string").str.strip().str.upper()
            .replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA, "<NA>": pd.NA})
        )

    @staticmethod
    def _limit_set_key(frame):
        parts = []
        for column in ["LIMIT_SET_ID", "LIMIT_SET_DESIGNATOR", "LIMIT_SET_SCHEDULE_ID"]:
            value = (
                frame[column].astype("string").str.strip().fillna("missing")
                if column in frame.columns
                else pd.Series("missing", index=frame.index, dtype="string")
            )
            parts.append(column + "=" + value)
        return parts[0] + "|" + parts[1] + "|" + parts[2]

    def _select_return_flow_candidates(self, dmr):
        """Rank candidates and average different values from distinct top limit sets."""
        if dmr.empty:
            return dmr.copy(), dmr.copy()

        work = dmr.copy().reset_index(drop=True)
        work["_candidate_id"] = range(len(work))
        groups = [
            "EXTERNAL_PERMIT_NMBR", "PERMIT_NUM", "PERM_FEATURE_NMBR",
            "MONITORING_PERIOD_END_DATE",
        ]
        required = groups + [
            "DMR_VALUE_STANDARD_UNITS", "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE", "STATISTICAL_BASE_TYPE_CODE",
        ]
        missing = [column for column in required if column not in work.columns]
        if missing:
            raise KeyError("Missing DMR selection fields: " + ", ".join(missing))

        work["MONITORING_PERIOD_END_DATE"] = pd.to_datetime(
            work["MONITORING_PERIOD_END_DATE"], errors="coerce"
        )
        work["FLOW_MGD"] = pd.to_numeric(work["DMR_VALUE_STANDARD_UNITS"], errors="coerce")
        for column in [
            "EXTERNAL_PERMIT_NMBR", "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE", "STATISTICAL_BASE_TYPE_CODE",
            "DMR_VALUE_QUALIFIER_CODE", "NODI_CODE", "PARAMETER_CODE",
            "LIMIT_SET_DESIGNATOR", "LIMIT_VALUE_TYPE_CODE",
        ]:
            if column in work.columns:
                work[column] = self._normalize_code(work[column])

        work = work.dropna(subset=groups + ["FLOW_MGD"]).copy()
        if (work["FLOW_MGD"] < 0).any():
            print(
                "Negative EPA DMR flow values found:\n"
                + work.loc[work["FLOW_MGD"] < 0, groups + ["FLOW_MGD"]]
                .head(20).to_string(index=False)
            )

        work["_limit_set_key"] = self._limit_set_key(work)
        work["stat_base_description"] = work["STATISTICAL_BASE_CODE"].map(
            lambda code: STATISTICAL_BASE_MAP.get(code, (900, None, False))[1]
        )
        work["_stat_base_rank"] = work["STATISTICAL_BASE_CODE"].map(
            lambda code: STATISTICAL_BASE_MAP.get(code, (900, None, False))[0]
        ).astype(int)
        work["monthly_average_compatible"] = work["STATISTICAL_BASE_CODE"].map(
            lambda code: STATISTICAL_BASE_MAP.get(code, (900, None, False))[2]
        )
        work["_monthly_rank"] = (~work["monthly_average_compatible"]).astype(int)
        work["_stat_type_rank"] = (
            work["STATISTICAL_BASE_TYPE_CODE"].map(STATISTICAL_TYPE_PRIORITY)
            .fillna(900).astype(int)
        )
        work["_location_rank"] = (
            work["MONITORING_LOCATION_CODE"].map(MONITORING_LOCATION_PRIORITY)
            .fillna(90).astype(int)
        )
        work["_known_rank"] = (~work["STATISTICAL_BASE_CODE"].isin(STATISTICAL_BASE_MAP)).astype(int)
        work["eligible_average_candidate"] = work["STATISTICAL_BASE_TYPE_CODE"].isin(
            STATISTICAL_TYPE_PRIORITY
        )
        eligible = work.loc[work["eligible_average_candidate"]].copy()
        if eligible.empty:
            work["selection_result"] = "excluded_no_average_candidate"
            return eligible, work

        qualifier = eligible.get("DMR_VALUE_QUALIFIER_CODE", pd.Series(pd.NA, index=eligible.index))
        eligible["_qualifier_rank"] = 10
        eligible.loc[qualifier.isna() | qualifier.eq("="), "_qualifier_rank"] = 0
        eligible["_nodi_rank"] = (
            eligible["NODI_CODE"].notna().astype(int)
            if "NODI_CODE" in eligible.columns else 0
        )
        eligible["_received"] = (
            pd.to_datetime(eligible["VALUE_RECEIVED_DATE"], errors="coerce")
            if "VALUE_RECEIVED_DATE" in eligible.columns else pd.NaT
        )
        eligible["selection_basis"] = (
            "location=" + eligible["MONITORING_LOCATION_CODE"].fillna("missing")
            + ";stat_base=" + eligible["STATISTICAL_BASE_CODE"].fillna("missing")
            + ";stat_type=" + eligible["STATISTICAL_BASE_TYPE_CODE"].fillna("missing")
        )

        exact = groups + [
            "_limit_set_key", "MONITORING_LOCATION_CODE", "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE", "FLOW_MGD",
        ]
        eligible["_candidate_copy_count"] = eligible.groupby(exact, dropna=False)[
            "FLOW_MGD"
        ].transform("size")
        eligible = eligible.drop_duplicates(exact, keep="first").copy()

        ranks = [
            "_stat_type_rank", "_location_rank", "_monthly_rank", "_known_rank",
            "_stat_base_rank", "_qualifier_rank", "_nodi_rank",
        ]
        eligible["_priority"] = list(zip(*(eligible[column] for column in ranks)))
        eligible["_best_priority"] = eligible.groupby(groups, dropna=False)[
            "_priority"
        ].transform("min")
        eligible["_is_top"] = eligible["_priority"].eq(eligible["_best_priority"])
        top = eligible.loc[eligible["_is_top"]].copy()

        summary = top.groupby(groups, dropna=False, as_index=False).agg(
            n_top_candidates=("FLOW_MGD", "size"),
            n_top_limit_sets=("_limit_set_key", "nunique"),
            n_top_flow_values=("FLOW_MGD", "nunique"),
            min_top_flow_mgd=("FLOW_MGD", "min"),
            max_top_flow_mgd=("FLOW_MGD", "max"),
            mean_top_flow_mgd=("FLOW_MGD", "mean"),
            candidate_limit_sets=("_limit_set_key", _join_unique),
            candidate_flow_values_mgd=("FLOW_MGD", _join_unique_numbers),
        )

        sort_columns = groups + ranks + ["_received"]
        ascending = [True] * len(groups) + [True] * len(ranks) + [False]
        for column in ["DMR_VALUE_ID", "DMR_FORM_VALUE_ID", "DMR_EVENT_ID", "LIMIT_VALUE_ID", "LIMIT_ID"]:
            if column in eligible.columns:
                temp = "_sort_" + column
                eligible[temp] = pd.to_numeric(eligible[column], errors="coerce")
                sort_columns.append(temp)
                ascending.append(False)
        eligible = eligible.sort_values(sort_columns, ascending=ascending, na_position="last")

        selected = eligible.drop_duplicates(groups, keep="first").copy()
        selected["single_selection_flow_mgd"] = selected["FLOW_MGD"]
        selected["single_selection_limit_set"] = selected["_limit_set_key"]
        selected = selected.merge(summary, on=groups, how="left", validate="one_to_one")

        average = selected["n_top_limit_sets"].gt(1) & selected["n_top_flow_values"].gt(1)
        equal = selected["n_top_limit_sets"].gt(1) & selected["n_top_flow_values"].eq(1)
        selected.loc[average, "FLOW_MGD"] = selected.loc[average, "mean_top_flow_mgd"]
        selected["selection_status"] = "selected_unique"
        selected.loc[equal, "selection_status"] = "selected_from_equal_value_limit_sets"
        selected.loc[average, "selection_status"] = "selected_mean_of_distinct_limit_sets"
        selected["mean_minus_single_mgd"] = selected["FLOW_MGD"] - selected["single_selection_flow_mgd"]
        selected["percent_change_from_single"] = pd.NA
        nonzero = selected["single_selection_flow_mgd"].ne(0)
        selected.loc[nonzero, "percent_change_from_single"] = (
            selected.loc[nonzero, "mean_minus_single_mgd"]
            / selected.loc[nonzero, "single_selection_flow_mgd"] * 100.0
        )
        selected["range_mgd"] = selected["max_top_flow_mgd"] - selected["min_top_flow_mgd"]
        selected["range_percent_of_mean"] = pd.NA
        nonzero_mean = selected["mean_top_flow_mgd"].ne(0)
        selected.loc[nonzero_mean, "range_percent_of_mean"] = (
            selected.loc[nonzero_mean, "range_mgd"]
            / selected.loc[nonzero_mean, "mean_top_flow_mgd"] * 100.0
        )

        audit_columns = groups + [
            "selection_status", "single_selection_flow_mgd", "single_selection_limit_set",
            "n_top_candidates", "n_top_limit_sets", "n_top_flow_values",
            "min_top_flow_mgd", "max_top_flow_mgd", "mean_top_flow_mgd",
            "candidate_limit_sets", "candidate_flow_values_mgd",
            "mean_minus_single_mgd", "percent_change_from_single",
            "range_mgd", "range_percent_of_mean",
        ]
        audit = work.merge(selected[audit_columns], on=groups, how="left", validate="many_to_one")
        audit["selection_result"] = "excluded_not_representative_average"
        audit.loc[audit["eligible_average_candidate"], "selection_result"] = "not_selected_lower_priority"
        top_ids = set(top["_candidate_id"])
        audit.loc[audit["_candidate_id"].isin(top_ids), "selection_result"] = "included_top_ranked_candidate"
        audit.loc[
            audit["_candidate_id"].isin(top_ids)
            & audit["selection_status"].eq("selected_mean_of_distinct_limit_sets"),
            "selection_result",
        ] = "included_in_limit_set_mean"
        return selected, audit

    def _canonical_outfall_geometry(self, outfalls):
        required = ["NPDES_NUM", "OUTFALL", "geometry"]
        missing = [column for column in required if column not in outfalls.columns]
        if missing:
            raise KeyError("TCEQ outfall fields missing: " + ", ".join(missing))
        outfalls = outfalls.copy()
        if outfalls.crs is None:
            outfalls = outfalls.set_crs("EPSG:4326")
        outfalls["PERMIT_NUM"] = self._normalize_npdes(outfalls["NPDES_NUM"])
        outfalls["PERM_FEATURE_NMBR"] = self._normalize_outfall(outfalls["OUTFALL"])
        outfalls = outfalls.dropna(subset=["PERMIT_NUM", "PERM_FEATURE_NMBR", "geometry"])
        if outfalls.empty:
            return gpd.GeoDataFrame(columns=["PERMIT_NUM", "PERM_FEATURE_NMBR", "geometry"], geometry="geometry", crs="EPSG:4326")

        outfalls["_horz_date_sort"] = pd.to_numeric(outfalls.get("HORZ_DATE"), errors="coerce")
        outfalls["_objectid_sort"] = pd.to_numeric(outfalls.get("OBJECTID"), errors="coerce")
        sort_columns = ["PERMIT_NUM", "PERM_FEATURE_NMBR", "_horz_date_sort", "_objectid_sort"]
        ascending = [True, True, False, False]
        canonical = outfalls.sort_values(sort_columns, ascending=ascending, na_position="last").drop_duplicates(
            ["PERMIT_NUM", "PERM_FEATURE_NMBR"], keep="first"
        )
        return gpd.GeoDataFrame(canonical, geometry="geometry", crs=outfalls.crs)

    def _scope(self, points, watersheds):
        eligible = watersheds.loc[
            watersheds["HAS_UNGAGE"].eq(1)
            & watersheds.geometry.notna()
            & ~watersheds.geometry.is_empty
        ].copy()
        joined = gpd.sjoin(
            points,
            eligible.to_crs(points.crs)[["WS_ID", "Estuary", "geometry"]],
            how="left",
            predicate="intersects",
        )
        coast_indexes = joined.loc[joined["WS_ID"].notna()].index.unique()
        coast = points.loc[points.index.isin(coast_indexes)].copy()
        remaining = points.loc[~points.index.isin(coast_indexes)].copy()
        coast["coastal_scope_status"] = "intersects_eligible_coastal_watershed"
        coast["distance_to_coastal_watershed_miles"] = 0.0
        if remaining.empty:
            return coast, remaining, remaining.copy()

        projected = remaining.to_crs(DISTANCE_CRS)
        union = eligible.to_crs(DISTANCE_CRS).geometry.unary_union
        projected["distance_to_coastal_watershed_miles"] = projected.geometry.distance(union) / METERS_PER_MILE
        nearby_mask = projected["distance_to_coastal_watershed_miles"].le(COASTAL_REVIEW_DISTANCE_MILES)
        nearby = projected.loc[nearby_mask].to_crs(points.crs).copy()
        outside = projected.loc[~nearby_mask].to_crs(points.crs).copy()
        nearby["coastal_scope_status"] = "near_coastal_watershed_unresolved"
        outside["coastal_scope_status"] = "outside_coastal_review_area"
        return coast, nearby, outside

    @staticmethod
    def _unique_volume(frame, record_columns):
        if frame.empty:
            return 0.0
        return float(frame.drop_duplicates(record_columns)["FLOW_ACFT_MONTH"].sum())

    @staticmethod
    def _write_duplicate_debug(reportable, audit, records):
        statuses = {"selected_mean_of_distinct_limit_sets", "selected_from_equal_value_limit_sets"}
        comparison = reportable.loc[reportable["selection_status"].isin(statuses)].copy()
        if not comparison.empty:
            comparison.to_csv("return_dmr_duplicate_limit_set_comparison.csv", index=False)
        keys = reportable[records + [
            column for column in [
                "coastal_scope_status", "distance_to_coastal_watershed_miles",
                "geometry_assignment_method", "geometry_donor_outfall",
            ] if column in reportable.columns
        ]].drop_duplicates(records)
        detail = audit.merge(keys, on=records, how="inner", validate="many_to_one")
        detail = detail.loc[detail["selection_status"].isin(statuses)]
        if not detail.empty:
            detail.to_csv("return_dmr_duplicate_limit_set_detail.csv", index=False)

    def build(self, start, end):
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        dmr = self.return_source.load_dmr(fiscal_year(start_ts), fiscal_year(end_ts))
        if dmr.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        dmr["MONITORING_PERIOD_END_DATE"] = pd.to_datetime(dmr["MONITORING_PERIOD_END_DATE"], errors="coerce")
        dmr["PARAMETER_CODE"] = self._normalize_code(dmr["PARAMETER_CODE"])
        dmr = dmr.loc[
            dmr["MONITORING_PERIOD_END_DATE"].between(start_ts, end_ts, inclusive="both")
            & dmr["PARAMETER_CODE"].eq("50050")
        ].copy()
        dmr["PERMIT_NUM"] = self._normalize_npdes(dmr["EXTERNAL_PERMIT_NMBR"])
        dmr["PERM_FEATURE_NMBR"] = self._normalize_outfall(dmr["PERM_FEATURE_NMBR"])
        dmr = dmr.dropna(subset=["PERMIT_NUM", "PERM_FEATURE_NMBR"])

        selected, audit = self._select_return_flow_candidates(dmr)
        if selected.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        records = ["EXTERNAL_PERMIT_NMBR", "PERMIT_NUM", "PERM_FEATURE_NMBR", "MONITORING_PERIOD_END_DATE"]
        feature = selected.copy()
        feature["days_in_month"] = feature["MONITORING_PERIOD_END_DATE"].dt.days_in_month
        feature["FLOW_ACFT_MONTH"] = mgd_to_afday(feature["FLOW_MGD"]) * feature["days_in_month"]

        outfalls = self.return_source.load_perm_feature_nmbrs()
        if self.return_source.last_outfall_retrieved_count != self.return_source.last_outfall_expected_count:
            raise RuntimeError("TCEQ outfall retrieval was incomplete.")
        canonical = self._canonical_outfall_geometry(outfalls)
        located, unlocated = fill_missing_geometry_from_group(
            feature, canonical,
            group_col="PERMIT_NUM", feature_col="PERM_FEATURE_NMBR", geometry_col="geometry",
            donor_sort_cols=["_horz_date_sort", "_objectid_sort", "PERM_FEATURE_NMBR"],
            assignment_col="geometry_assignment_method", donor_feature_col="geometry_donor_outfall",
        )
        if located.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        points = gpd.GeoDataFrame(located, geometry="geometry", crs=canonical.crs)
        watersheds = self.ctx.registry.load_watersheds()[["WS_ID", "Estuary", "HAS_UNGAGE", "geometry"]].copy()
        coastal, nearby, outside = self._scope(points, watersheds)
        reportable = pd.concat([coastal, nearby], ignore_index=True)

        assigned, unexpected, spatial_audit = assign_geometries_to_watersheds(
            coastal, watersheds, record_cols=records, ws_col="WS_ID", estuary_col="Estuary",
            include_flag_col="HAS_UNGAGE", include_flag_value=1, predicate="intersects",
        ) if not coastal.empty else (coastal.copy(), coastal.copy(), coastal.copy())
        if not unexpected.empty:
            unexpected = unexpected.copy()
            unexpected["coastal_scope_status"] = "unexpected_unresolved_after_coastal_intersection"
        unresolved = pd.concat([nearby, unexpected], ignore_index=True)
        spatial_audit = pd.concat([spatial_audit, nearby], ignore_index=True)

        if WRITE_DEBUG_FILES:
            self._write_duplicate_debug(reportable, audit, records)
            if not spatial_audit.empty:
                spatial_audit.to_csv("return_dmr_spatial_assignment_audit.csv", index=False)
            if not unresolved.empty:
                unresolved.to_csv("return_dmr_coastal_unresolved.csv", index=False)

        selected_volume = float(feature["FLOW_ACFT_MONTH"].sum())
        accounted = sum([
            self._unique_volume(assigned, records),
            self._unique_volume(unlocated, records),
            self._unique_volume(unresolved, records),
            self._unique_volume(outside, records),
        ])
        if abs(selected_volume - accounted) > max(1e-6, abs(selected_volume) * 1e-10):
            raise AssertionError(f"Return-flow volume not conserved: selected={selected_volume}; accounted={accounted}")

        if assigned.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)
        assigned["year"] = assigned["MONITORING_PERIOD_END_DATE"].dt.year
        assigned["month"] = assigned["MONITORING_PERIOD_END_DATE"].dt.month
        if WRITE_DEBUG_FILES:
            assigned.to_csv("return_dmr_outfall_join_audit.csv", index=False)

        monthly = assigned.groupby(["WS_ID", "Estuary", "year", "month"], as_index=False).agg(
            value_acft_month=("FLOW_ACFT_MONTH", "sum")
        )
        daily = expand_monthly_to_daily(
            monthly.rename(columns={"value_acft_month": "value", "WS_ID": "ws_id", "Estuary": "estuary"}),
            year_col="year", month_col="month", value_col="value", id_col="ws_id",
            estuary_col="estuary", value_is_monthly_total=True,
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

    def run(self, start=None, end=None):
        if start is None:
            watermark = self.ctx.storage.get_watermark(self.name, default="2015-01-01")
            start = watermark + pd.Timedelta(days=1) if watermark is not None else pd.Timestamp("2015-01-01")
        if end is None:
            end = pd.Timestamp.utcnow().normalize()
        print(f"[ReturnFlow] Running {start} -> {end}")
        frame = self.build(start, end)
        if frame.empty:
            print("[ReturnFlow] No rows written")
            return 0
        rows = self.ctx.storage.append(frame, run_start=start, run_end=end)
        self.ctx.storage.set_watermark(self.name, frame["date"].max())
        return rows


def build_return(monthly_df):
    frame = expand_monthly_to_daily(monthly_df)
    frame["component"] = "return"
    frame["flow_role"] = "return"
    frame["count_in_basin_sum"] = 1
    return frame
