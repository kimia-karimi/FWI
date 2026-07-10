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
START_FY = 2015

BASE_URL = (
    "https://echo.epa.gov/files/echodownloads/"
    "NPDES_by_state_year"
)

OUTFALL_URL = (
    "https://gisweb.tceq.texas.gov/arcgis/rest/services/"
    "Public/WW_oufalls/MapServer/0/query"
    "?outFields=*"
    "&where=1%3D1"
    "&f=geojson"
)


class ReturnFlowComponent:

    name = "return_flow"

    def __init__(self, ctx: RunContext):
        self.ctx = ctx

    # --------------------------------------------------
    # Download DMR ZIP
    # --------------------------------------------------
    def _download_fy_zip(self, fy):

        fname = f"{STATE}_FY{fy}_NPDES_DMRS_LIMITS.zip"

        url = f"{BASE_URL}/{fname}"

        r = requests.get(url, timeout=300)

        if r.status_code != 200:
            print(f"[ReturnFlow] Failed FY {fy}")
            return None

        return io.BytesIO(r.content)

    # --------------------------------------------------
    # Load all DMRs
    # --------------------------------------------------
    def _load_dmr(self):

        current_year = pd.Timestamp.now().year
        current_month = pd.Timestamp.now().month

        current_fy = (
            current_year + 1
            if current_month >= 10
            else current_year
        )

        frames = []

        for fy in range(START_FY, current_fy + 1):

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

        dmr = self._load_dmr()

        if dmr.empty:
            return pd.DataFrame(columns=REQUIRED_OUT_COLS)

        # --------------------------------------------------
        # Flow records only
        # --------------------------------------------------
        dmr = dmr[
            dmr["PARAMETER_DESC"]
            == "Flow, in conduit or thru treatment plant"
        ].copy()

        dmr["MONITORING_PERIOD_END_DATE"] = pd.to_datetime(
            dmr["MONITORING_PERIOD_END_DATE"],
            errors="coerce",
        )

        dmr["FLOW_MGD"] = pd.to_numeric(
            dmr["DMR_VALUE_STANDARD_UNITS"],
            errors="coerce",
        )

        dmr = dmr.dropna(
            subset=[
                "MONITORING_PERIOD_END_DATE",
                "FLOW_MGD",
            ]
        )

        # --------------------------------------------------
        # Permit normalization
        # --------------------------------------------------
        dmr["NPDES_NUM"] = (
            dmr["EXTERNAL_PERMIT_NMBR"]
            .astype(str)
            .str.replace(r"\D+", "", regex=True)
            .str.lstrip("0")
        )
        

        # --------------------------------------------------
        # Load outfalls
        # --------------------------------------------------
        
        resp = requests.get(
            OUTFALL_URL,timeout=120,)

        resp.raise_for_status()

        geojson = resp.json()


        outfalls = gpd.GeoDataFrame.from_features( geojson["features"], crs="EPSG:4326",)

        outfalls["NPDES_NUM"] = (
            outfalls["NPDES_NUM"]
            .astype(str)
            .str.replace(r"\D+", "", regex=True)
            .str.lstrip("0")
        )

        # --------------------------------------------------
        # Join flow -> outfall
        # --------------------------------------------------
        dmr_geo = dmr.merge(
            outfalls[["NPDES_NUM", "geometry"]],
            on="NPDES_NUM",
            how="left",
        )

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
            [["WS_ID", "estuary", "geometry"]]
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
        # --------------------------------------------------
        monthly_ws = (
            dmr_geo
            .groupby(
                [
                    "WS_ID",
                    "ESTUARY",
                    "year",
                    "month",
                ],
                as_index=False,
            )["FLOW_MGD"]
            .sum()
        )

        # --------------------------------------------------
        # Expand monthly -> daily
        # --------------------------------------------------
        daily = expand_monthly_to_daily(
            monthly_ws.rename(
                columns={
                    "FLOW_MGD": "value",
                    "WS_ID": "ws_id",
                    "ESTUARY": "estuary",
                }
            ),
            year_col="year",
            month_col="month",
            value_col="value",
            id_col="ws_id",
            estuary_col="estuary",
            value_is_monthly_total=False,
        )

        if daily.empty:
            return pd.DataFrame(
                columns=REQUIRED_OUT_COLS
            )

        # --------------------------------------------------
        # Convert MGD -> AFD
        # --------------------------------------------------
        daily["value_afday"] = mgd_to_afday(
            daily["value_afday"]
        )

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
