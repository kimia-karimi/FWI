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
import zipfile
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
    "note",
]


STATE = "TX"

BASE_URL = (
    "https://echo.epa.gov/files/echodownloads/"
    "NPDES_by_state_year"
)

PERM_FEATURE_NMBR_URL = (
    "https://gisweb.tceq.texas.gov/arcgis/rest/services/"
    "Public/WW_oufalls/MapServer/0/query"
    "?outFields=*"
    "&where=1%3D1"
    "&f=geojson"
)
def fiscal_year(dt):
    dt = pd.Timestamp(dt)
    return dt.year + 1 if dt.month >= 10 else dt.year

class ReturnFlowComponent:

    name = "return"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
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
            .str.replace(r"\D+", "", regex=True)
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
    # --------------------------------------------------
    # Download DMR ZIP
    # --------------------------------------------------
    def _download_fy_zip(self, fy):

        fname = f"{STATE}_FY{fy}_NPDES_DMRS_LIMITS.zip"

        url = f"{BASE_URL}/{fname}"
        print(url)

        r = requests.get(url, timeout=300, verify=certifi.where(),)
        print(fy, r.status_code,r.headers.get("Content-Type"),len(r.content))
        if r.status_code != 200:
            return None

        return io.BytesIO(r.content)

    # --------------------------------------------------
    # Load all DMRs
    # --------------------------------------------------
    def _load_dmr(self,start_fy, end_fy):

        frames = []

        for fy in range(start_fy, end_fy + 1):

            z = self._download_fy_zip(fy)

            if z is None:
                continue

            with zipfile.ZipFile(z) as zipf:

                try:
                    dmr_name = next(
                        n
                        for n in zipf.namelist()
                        if "DMRS" in n.upper()
                    )

                    frames.append(
                        pd.read_csv(
                            zipf.open(dmr_name),
                            low_memory=False,
                        )
                    )

                except StopIteration:
                    continue

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)

    # --------------------------------------------------
    # Main build
    # --------------------------------------------------
    def build(self, start, end):
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        start_fy = fiscal_year(start_ts)
        end_fy = fiscal_year(end_ts)

        dmr = self._load_dmr(start_fy, end_fy)

        print("Loaded DMR rows:", len(dmr))

        if dmr.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        # --------------------------------------------------
        # Flow records only
        # --------------------------------------------------
        print(dmr.columns)
        dmr["MONITORING_PERIOD_END_DATE"] = pd.to_datetime(
            dmr["MONITORING_PERIOD_END_DATE"],
            errors="coerce",
        )
        print(dmr["MONITORING_PERIOD_END_DATE"] .min(), dmr["MONITORING_PERIOD_END_DATE"] .max())
        dmr = dmr[
            (dmr["MONITORING_PERIOD_END_DATE"] >= start_ts)
            & (dmr["MONITORING_PERIOD_END_DATE"] <= end_ts)
            & (dmr["PARAMETER_CODE"].astype(str).str.strip() == "50050")
        ].copy()

        print("Flow rows after date and parameter filter:", len(dmr))
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
        # --------------------------------------------------
        # Avoid inflating flow because one reported DMR value
        # can appear more than once due to limit rows.
        # --------------------------------------------------
        dedup_cols = [
            "EXTERNAL_PERMIT_NMBR",
            "PERMIT_NUM",
            "PERM_FEATURE_NMBR", #multiple feature/PERM_FEATURE_NMBR may be reported separately
            "MONITORING_PERIOD_END_DATE",
            "PARAMETER_CODE",
            "DMR_VALUE_ID",
            "FLOW_MGD",
        ]

        dedup_cols = [
            c for c in dedup_cols
            if c in dmr.columns
        ]

        dmr = dmr.drop_duplicates(
            subset=dedup_cols
        )
        print("DMR length after dedup:", len(dmr))
        # --------------------------------------------------
        # Monthly feature-level flow
        #
        #
        # keep each permit PERM_FEATURE_NMBR separate before spatial join.
        # --------------------------------------------------
        feature_monthly = (
            dmr
            .groupby(
                [
                    "EXTERNAL_PERMIT_NMBR",
                    "PERMIT_NUM",
                    "PERM_FEATURE_NMBR",
                    "MONITORING_PERIOD_END_DATE",
                ],
                as_index=False,
            )
            .agg(
                FLOW_MGD=("FLOW_MGD", "sum"),
                n_dmr_rows=("FLOW_MGD", "size"),
            )
        )
        print("Feature monthly rows:", len(feature_monthly))
        print(feature_monthly[ ["PERMIT_NUM","PERM_FEATURE_NMBR"]].head(20))
        feature_monthly["days_in_month"] = (
            feature_monthly["MONITORING_PERIOD_END_DATE"]
            .dt.days_in_month
        )

        feature_monthly["FLOW_ACFT_MONTH"] = mgd_to_afday(feature_monthly["FLOW_MGD"])*feature_monthly["days_in_month"]
        
        
        
        # --------------------------------------------------
        # Debug CSV:
        # permit, monitoring date, feature flow columns,
        # overall MGD, and overall acre-ft/month.
        # --------------------------------------------------
        debug = self._build_feature_debug(feature_monthly)
        self._write_debug_csv(debug)
        
        # --------------------------------------------------
        # Load PERM_FEATURE_NMBRs
        # --------------------------------------------------
        
        resp = requests.get(
            PERM_FEATURE_NMBR_URL,timeout=120,verify=certifi.where(),)

        resp.raise_for_status()

        geojson = resp.json()
        

        PERM_FEATURE_NMBRs = gpd.GeoDataFrame.from_features( geojson["features"], crs="EPSG:4326",)
        print("PERM_FEATURE_NMBRs rows:", len(PERM_FEATURE_NMBRs))
        print(PERM_FEATURE_NMBRs.columns.tolist())
        PERM_FEATURE_NMBRs["PERMIT_NUM"] = self._normalize_npdes(
            PERM_FEATURE_NMBRs["NPDES_NUM"]
        )
        PERM_FEATURE_NMBRs["PERM_FEATURE_NMBR"] = self._normalize_outfall(PERM_FEATURE_NMBRs["OUTFALL"])
        print("PERM_FEATURE_NMBRs rows before dropna:", len(PERM_FEATURE_NMBRs))
        PERM_FEATURE_NMBRs = PERM_FEATURE_NMBRs.dropna(
            subset=[
                "PERMIT_NUM",
                "PERM_FEATURE_NMBR",
                "geometry",
            ]
        )
        print("PERM_FEATURE_NMBRs rows after dropna:", len(PERM_FEATURE_NMBRs))
        print(PERM_FEATURE_NMBRs.columns.tolist())
        print(PERM_FEATURE_NMBRs[ ["PERMIT_NUM","PERM_FEATURE_NMBR"]].head(20))
        # --------------------------------------------------
        # One geometry per permit + PERM_FEATURE_NMBR.
        #
        # This prevents the old issue:
        # joining permit total to all PERM_FEATURE_NMBRs and multiplying flow.
        # --------------------------------------------------
        PERM_FEATURE_NMBRs_feature = (
            PERM_FEATURE_NMBRs
            .drop_duplicates(
                subset=[
                    "PERMIT_NUM",
                    "PERM_FEATURE_NMBR",
                ]
            )
            [
                [
                    "PERMIT_NUM",
                    "PERM_FEATURE_NMBR",
                    "geometry",
                ]
            ]
        )
 

        # --------------------------------------------------
        # Join DMR permit + feature -> TCEQ permit + PERM_FEATURE_NMBR
        # --------------------------------------------------
        dmr_geo = feature_monthly.merge(
            PERM_FEATURE_NMBRs_feature,
            on=["PERMIT_NUM","PERM_FEATURE_NMBR"],
            how="left",
            indicator= True
        )
        
        # Optional join-quality debug

        dmr_geo[
            [
                "EXTERNAL_PERMIT_NMBR",
                "PERMIT_NUM",
                "PERM_FEATURE_NMBR",
                "MONITORING_PERIOD_END_DATE",
                "FLOW_MGD",
                "FLOW_ACFT_MONTH",
                "_merge",
            ]
        ].to_csv(
           "return_dmr_PERM_FEATURE_NMBR_join_debug.csv",
            index=False,
        )
        print(dmr_geo["_merge"].value_counts(dropna=False))

        missing_geo = dmr_geo[dmr_geo["_merge"] == "left_only"].copy()

        if not missing_geo.empty:
            missing_geo[
                [
                    "EXTERNAL_PERMIT_NMBR",
                    "PERMIT_NUM",
                    "PERM_FEATURE_NMBR",
                    "MONITORING_PERIOD_END_DATE",
                    "FLOW_MGD",
                    "FLOW_ACFT_MONTH",
                ]
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
        ].drop(columns=["_merge"])

        dmr_geo = dmr_geo.dropna(
            subset=["geometry"]
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
            [["WS_ID", "Estuary", "geometry"]]
        )

        watersheds = watersheds.to_crs(
            dmr_geo.crs
        )

        dmr_geo = gpd.sjoin(
            dmr_geo,
            watersheds,
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

        n = self.ctx.storage.append(df)

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
