# tx_fwi/sources/epa_dmr.py
from __future__ import annotations

import io
import zipfile

import certifi
import geopandas as gpd
import pandas as pd
import requests

from tx_fwi.components.base import RunContext


class ReturnFlowSource:
    """Retrieve and parse EPA DMR values and TCEQ outfall geometry."""

    name = "epa_dmr"
    STATE = "TX"
    PARAMETER_CODE = "50050"

    BASE_URL = (
        "https://echo.epa.gov/files/echodownloads/"
        "NPDES_by_state_year"
    )

    PERM_FEATURE_NMBR_URL = (
        "https://gisweb.tceq.texas.gov/"
        "arcgis/rest/services/"
        "Public/WW_oufalls/MapServer/0/query"
    )

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.last_outfall_expected_count: int | None = None
        self.last_outfall_retrieved_count: int | None = None

    # --------------------------------------------------
    # EPA DMR retrieval
    # --------------------------------------------------
    def _download_fy_zip(self, fiscal_year: int) -> io.BytesIO | None:
        filename = (
            f"{self.STATE}_FY{fiscal_year}_"
            "NPDES_DMRS_LIMITS.zip"
        )
        url = f"{self.BASE_URL}/{filename}"

        print(
            "[ReturnFlowSource] Downloading EPA DMR archive: "
            f"FY{fiscal_year}"
        )

        response = requests.get(
            url,
            timeout=300,
            verify=certifi.where(),
        )

        if response.status_code == 404:
            print(
                "[ReturnFlowSource] EPA DMR archive not found: "
                f"FY{fiscal_year}"
            )
            return None

        response.raise_for_status()

        if not response.content:
            raise RuntimeError(
                "EPA returned an empty DMR ZIP response for "
                f"fiscal year {fiscal_year}."
            )

        return io.BytesIO(response.content)

    @staticmethod
    def _csv_members(archive: zipfile.ZipFile) -> list[str]:
        return [
            member
            for member in archive.namelist()
            if not member.endswith("/")
            and member.lower().endswith(".csv")
        ]

    @staticmethod
    def _read_member_columns(
        archive: zipfile.ZipFile,
        member: str,
    ) -> set[str]:
        header = pd.read_csv(
            archive.open(member),
            nrows=0,
            low_memory=False,
        )
        return {
            str(column).strip().upper()
            for column in header.columns
        }

    @classmethod
    def _find_dmr_member(cls, archive: zipfile.ZipFile) -> str:
        """Identify the reported-value CSV by its required columns."""
        required = {
            "EXTERNAL_PERMIT_NMBR",
            "PERM_FEATURE_NMBR",
            "MONITORING_PERIOD_END_DATE",
            "PARAMETER_CODE",
            "DMR_VALUE_STANDARD_UNITS",
        }

        candidates = []
        for member in cls._csv_members(archive):
            columns = cls._read_member_columns(archive, member)
            if required.issubset(columns):
                candidates.append(member)

        if not candidates:
            raise RuntimeError(
                "EPA archive does not contain a DMR reported-value "
                "CSV with the required columns. Archive members:\n"
                + "\n".join(archive.namelist()[:50])
            )

        preferred = [
            member
            for member in candidates
            if "LIMIT" not in member.upper()
        ]
        if len(preferred) == 1:
            return preferred[0]
        if len(candidates) == 1:
            return candidates[0]

        raise RuntimeError(
            "EPA archive contains multiple possible DMR value files:\n"
            + "\n".join(candidates)
        )

    @staticmethod
    def _filtered_usecols(columns: list[str]) -> list[str]:
        """Retain fields needed by return-flow selection and auditing."""
        needed = {
            "ACTIVITY_ID",
            "EXTERNAL_PERMIT_NMBR",
            "VERSION_NMBR",
            "PERM_FEATURE_ID",
            "PERM_FEATURE_NMBR",
            "PERM_FEATURE_TYPE_CODE",
            "LIMIT_SET_ID",
            "LIMIT_SET_DESIGNATOR",
            "LIMIT_SET_SCHEDULE_ID",
            "LIMIT_ID",
            "LIMIT_VALUE_ID",
            "LIMIT_VALUE_TYPE_CODE",
            "PARAMETER_CODE",
            "PARAMETER_DESC",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
            "DMR_EVENT_ID",
            "MONITORING_PERIOD_END_DATE",
            "DMR_FORM_VALUE_ID",
            "DMR_VALUE_ID",
            "DMR_VALUE_STANDARD_UNITS",
            "DMR_VALUE_QUALIFIER_CODE",
            "VALUE_RECEIVED_DATE",
            "NODI_CODE",
        }
        return [column for column in columns if column.upper() in needed]

    def _read_filtered_dmr_member(
        self,
        archive: zipfile.ZipFile,
        member: str,
        *,
        fiscal_year: int,
        chunksize: int = 200_000,
    ) -> pd.DataFrame:
        """Read one statewide DMR CSV in chunks and retain flow only."""
        header = pd.read_csv(
            archive.open(member),
            nrows=0,
            low_memory=False,
        )
        usecols = self._filtered_usecols(list(header.columns))

        required = {
            "EXTERNAL_PERMIT_NMBR",
            "PERM_FEATURE_NMBR",
            "MONITORING_PERIOD_END_DATE",
            "PARAMETER_CODE",
            "DMR_VALUE_STANDARD_UNITS",
            "MONITORING_LOCATION_CODE",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
        }
        missing = required - set(usecols)
        if missing:
            raise KeyError(
                "EPA DMR file is missing required columns: "
                + ", ".join(sorted(missing))
            )

        frames: list[pd.DataFrame] = []
        source_rows = 0
        retained_rows = 0

        reader = pd.read_csv(
            archive.open(member),
            usecols=usecols,
            chunksize=chunksize,
            low_memory=False,
        )

        for chunk_number, chunk in enumerate(reader, start=1):
            source_rows += len(chunk)
            parameter = (
                chunk["PARAMETER_CODE"]
                .astype("string")
                .str.strip()
                .str.replace(r"\.0$", "", regex=True)
            )
            chunk = chunk.loc[
                parameter.eq(self.PARAMETER_CODE)
            ].copy()

            if chunk.empty:
                continue

            chunk["PARAMETER_CODE"] = self.PARAMETER_CODE
            chunk["_source_fiscal_year"] = fiscal_year
            chunk["_source_archive_member"] = member
            frames.append(chunk)
            retained_rows += len(chunk)

            print(
                "[ReturnFlowSource] FY"
                f"{fiscal_year} chunk {chunk_number}: "
                f"{source_rows:,} source rows; "
                f"{retained_rows:,} flow rows retained"
            )

        if not frames:
            return pd.DataFrame(columns=usecols)

        if len(frames) == 1:
            return frames[0].reset_index(drop=True)

        return pd.concat(
            frames,
            ignore_index=True,
            sort=False,
            copy=False,
        )

    def load_dmr(
        self,
        start_fy: int,
        end_fy: int,
        *,
        chunksize: int = 200_000,
    ) -> pd.DataFrame:
        """Load parameter-50050 DMR values for an inclusive FY range."""
        if end_fy < start_fy:
            raise ValueError("end_fy cannot be earlier than start_fy.")

        frames: list[pd.DataFrame] = []

        for fiscal_year in range(start_fy, end_fy + 1):
            zip_buffer = self._download_fy_zip(fiscal_year)
            if zip_buffer is None:
                continue

            try:
                with zipfile.ZipFile(zip_buffer) as archive:
                    dmr_member = self._find_dmr_member(archive)
                    frame = self._read_filtered_dmr_member(
                        archive,
                        dmr_member,
                        fiscal_year=fiscal_year,
                        chunksize=chunksize,
                    )
            except zipfile.BadZipFile as exc:
                raise RuntimeError(
                    "EPA returned an invalid ZIP file for "
                    f"fiscal year {fiscal_year}."
                ) from exc

            if frame.empty:
                print(
                    "[ReturnFlowSource] No flow records in EPA DMR "
                    f"FY{fiscal_year}"
                )
                continue

            frames.append(frame)
            print(
                "[ReturnFlowSource] Loaded EPA DMR FY"
                f"{fiscal_year}: {len(frame):,} flow rows"
            )

        if not frames:
            return pd.DataFrame()

        if len(frames) == 1:
            result = frames[0].reset_index(drop=True)
        else:
            result = pd.concat(
                frames,
                ignore_index=True,
                sort=False,
                copy=False,
            )

        print(
            "[ReturnFlowSource] EPA flow rows loaded: "
            f"{len(result):,}"
        )
        return result

    def _load_dmr(self, start_fy: int, end_fy: int) -> pd.DataFrame:
        return self.load_dmr(start_fy, end_fy)

    # --------------------------------------------------
    # TCEQ wastewater-outfall retrieval
    # --------------------------------------------------
    def _get_outfall_count(self) -> int:
        response = requests.get(
            self.PERM_FEATURE_NMBR_URL,
            params={
                "where": "1=1",
                "returnCountOnly": "true",
                "f": "json",
            },
            timeout=120,
            verify=certifi.where(),
        )
        response.raise_for_status()
        payload = response.json()

        if "error" in payload:
            raise RuntimeError(
                "TCEQ outfall count request failed: "
                f"{payload['error']}"
            )
        if "count" not in payload:
            raise RuntimeError(
                "TCEQ outfall count response does not contain count."
            )
        return int(payload["count"])

    def _get_outfall_page(
        self,
        *,
        offset: int,
        batch_size: int,
    ) -> list:
        response = requests.get(
            self.PERM_FEATURE_NMBR_URL,
            params={
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": batch_size,
                "orderByFields": "OBJECTID ASC",
            },
            timeout=120,
            verify=certifi.where(),
        )
        response.raise_for_status()
        payload = response.json()

        if "error" in payload:
            raise RuntimeError(
                "TCEQ outfall page request failed at "
                f"offset {offset}: {payload['error']}"
            )
        features = payload.get("features")
        if features is None:
            raise RuntimeError(
                "TCEQ outfall response does not contain features at "
                f"offset {offset}."
            )
        return features

    def load_perm_feature_nmbrs(
        self,
        *,
        batch_size: int = 2000,
    ) -> gpd.GeoDataFrame:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero.")

        expected_count = self._get_outfall_count()
        self.last_outfall_expected_count = expected_count

        print(
            "[ReturnFlowSource] TCEQ outfalls reported by service: "
            f"{expected_count:,}"
        )

        if expected_count == 0:
            self.last_outfall_retrieved_count = 0
            return gpd.GeoDataFrame(
                columns=["geometry"],
                geometry="geometry",
                crs="EPSG:4326",
            )

        features: list[dict] = []
        offset = 0

        while offset < expected_count:
            batch = self._get_outfall_page(
                offset=offset,
                batch_size=batch_size,
            )
            if not batch:
                raise RuntimeError(
                    "TCEQ outfall pagination ended early. "
                    f"Retrieved={len(features):,}; expected={expected_count:,}."
                )
            features.extend(batch)
            offset += len(batch)
            print(
                "[ReturnFlowSource] Loaded TCEQ outfalls: "
                f"{len(features):,} / {expected_count:,}"
            )
            if len(batch) < batch_size:
                break

        result = gpd.GeoDataFrame.from_features(
            features,
            crs="EPSG:4326",
        )
        self.last_outfall_retrieved_count = len(result)

        if len(result) != expected_count:
            raise RuntimeError(
                "TCEQ outfall retrieval count mismatch. "
                f"Expected={expected_count:,}; received={len(result):,}."
            )

        required = ["OBJECTID", "NPDES_NUM", "OUTFALL", "geometry"]
        missing = [column for column in required if column not in result]
        if missing:
            raise KeyError(
                "TCEQ outfall response is missing fields: "
                + ", ".join(missing)
            )

        duplicate_objectids = result["OBJECTID"].duplicated(keep=False)
        if duplicate_objectids.any():
            raise ValueError(
                "TCEQ outfall pagination returned duplicate OBJECTIDs:\n"
                + result.loc[
                    duplicate_objectids,
                    ["OBJECTID", "NPDES_NUM", "OUTFALL"],
                ].head(50).to_string(index=False)
            )

        print(
            "[ReturnFlowSource] TCEQ outfall retrieval complete: "
            f"{len(result):,} features; "
            f"{int(result.geometry.isna().sum()):,} without geometry"
        )
        return result

    def fetch(self, start_fy: int, end_fy: int) -> dict[str, object]:
        return {
            "dmr": self.load_dmr(start_fy, end_fy),
            "perm_feature_nmbrs": self.load_perm_feature_nmbrs(),
        }
