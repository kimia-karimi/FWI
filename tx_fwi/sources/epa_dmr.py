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
    """
    Retrieve and parse source data used by ReturnComponent.

    This source owns:

    - EPA fiscal-year DMR ZIP downloads
    - EPA DMR CSV parsing
    - paginated TCEQ wastewater-outfall retrieval
    - source-level completeness validation

    Return-flow selection, unit conversion, geometry resolution,
    watershed assignment, aggregation, storage, and watermarks
    remain in ReturnComponent.
    """

    name = "epa_dmr"
    STATE = "TX"

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

        # Populated by load_perm_feature_nmbrs().
        self.last_outfall_expected_count: int | None = None
        self.last_outfall_retrieved_count: int | None = None

    # --------------------------------------------------
    # EPA DMR retrieval
    # --------------------------------------------------
    def _download_fy_zip(
        self,
        fiscal_year: int,
    ) -> io.BytesIO | None:
        """
        Download one EPA Texas fiscal-year DMR ZIP file.

        Return None when EPA reports that the requested file does
        not exist. Other HTTP and ZIP errors are raised.
        """
        filename = (
            f"{self.STATE}_FY{fiscal_year}_"
            "NPDES_DMRS_LIMITS.zip"
        )

        url = f"{self.BASE_URL}/{filename}"

        print(
            "[ReturnFlowSource] Downloading EPA DMR: "
            f"FY{fiscal_year}"
        )

        response = requests.get(
            url,
            timeout=300,
            verify=certifi.where(),
        )

        if response.status_code == 404:
            print(
                "[ReturnFlowSource] EPA DMR file not found: "
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
    def _find_dmr_member(
        archive: zipfile.ZipFile,
    ) -> str:
        """
        Find the DMR CSV member in an EPA fiscal-year ZIP archive.
        """
        candidates = [
            member
            for member in archive.namelist()
            if "DMRS" in member.upper()
            and member.lower().endswith(".csv")
        ]

        if not candidates:
            raise RuntimeError(
                "EPA DMR ZIP does not contain a DMR CSV file. "
                "Archive members include:\n"
                + "\n".join(
                    archive.namelist()[:50]
                )
            )

        # Prefer a DMR file that is not obviously a limits-only file.
        preferred = [
            member
            for member in candidates
            if "LIMIT" not in member.upper()
        ]

        return (
            preferred[0]
            if preferred
            else candidates[0]
        )

    def load_dmr(
        self,
        start_fy: int,
        end_fy: int,
    ) -> pd.DataFrame:
        """
        Load EPA DMR records for an inclusive fiscal-year range.
        """
        if end_fy < start_fy:
            raise ValueError(
                "end_fy cannot be earlier than start_fy."
            )

        frames: list[pd.DataFrame] = []

        for fy in range(start_fy, end_fy + 1):
            zip_buffer = self._download_fy_zip(fy)

            if zip_buffer is None:
                continue

            try:
                with zipfile.ZipFile(
                    zip_buffer
                ) as archive:
                    dmr_member = self._find_dmr_member(
                        archive
                    )

                    frame = pd.read_csv(
                        archive.open(dmr_member),
                        low_memory=False,
                    )

            except zipfile.BadZipFile as exc:
                raise RuntimeError(
                    "EPA returned an invalid ZIP file for "
                    f"fiscal year {fy}."
                ) from exc

            frame["_source_fiscal_year"] = fy
            frame["_source_archive_member"] = (
                dmr_member
            )

            frames.append(frame)

            print(
                "[ReturnFlowSource] Loaded EPA DMR "
                f"FY{fy}: {len(frame):,} rows"
            )

        if not frames:
            return pd.DataFrame()

        result = pd.concat(
            frames,
            ignore_index=True,
            sort=False,
        )

        print(
            "[ReturnFlowSource] EPA DMR rows loaded: "
            f"{len(result):,}"
        )

        return result

    # Backward compatibility for the current ReturnComponent.
    # New code should call load_dmr().
    def _load_dmr(
        self,
        start_fy: int,
        end_fy: int,
    ) -> pd.DataFrame:
        return self.load_dmr(
            start_fy,
            end_fy,
        )

    # --------------------------------------------------
    # TCEQ wastewater-outfall retrieval
    # --------------------------------------------------
    def _get_outfall_count(self) -> int:
        """
        Return the total number of records reported by the ArcGIS
        wastewater-outfall layer.
        """
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
                "TCEQ outfall count response does not "
                "contain a count field."
            )

        return int(payload["count"])

    def _get_outfall_page(
        self,
        *,
        offset: int,
        batch_size: int,
        ) -> list
        """
        Retrieve one ordered page from the ArcGIS outfall layer.
        """
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
                "TCEQ outfall response does not contain "
                f"a features collection at offset {offset}."
            )

        return features

    def load_perm_feature_nmbrs(
        self,
        *,
        batch_size: int = 2000,
    ) -> gpd.GeoDataFrame:
        """
        Load every TCEQ wastewater-outfall feature.

        The ArcGIS service limits the number of records returned
        in one request, so this method retrieves ordered pages and
        verifies that the final feature count matches the count
        reported by the service.
        """
        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero."
            )

        expected_count = (
            self._get_outfall_count()
        )

        self.last_outfall_expected_count = (
            expected_count
        )

        print(
            "[ReturnFlowSource] TCEQ outfalls "
            f"reported by service: {expected_count:,}"
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
                    "TCEQ wastewater-outfall pagination "
                    "ended before the expected record count. "
                    f"Offset={offset:,}; "
                    f"retrieved={len(features):,}; "
                    f"expected={expected_count:,}."
                )

            features.extend(batch)
            offset += len(batch)

            print(
                "[ReturnFlowSource] Loaded TCEQ "
                f"outfalls: {len(features):,} / "
                f"{expected_count:,}"
            )

            if len(batch) < batch_size:
                break

        result = gpd.GeoDataFrame.from_features(
            features,
            crs="EPSG:4326",
        )

        self.last_outfall_retrieved_count = (
            len(result)
        )

        if len(result) != expected_count:
            raise RuntimeError(
                "TCEQ wastewater-outfall retrieval was "
                "incomplete or returned an unexpected count. "
                f"Expected {expected_count:,} features but "
                f"received {len(result):,}."
            )

        required_cols = [
            "OBJECTID",
            "NPDES_NUM",
            "OUTFALL",
            "geometry",
        ]

        missing_cols = [
            col
            for col in required_cols
            if col not in result.columns
        ]

        if missing_cols:
            raise KeyError(
                "TCEQ wastewater-outfall response is "
                "missing required fields: "
                + ", ".join(missing_cols)
            )

        duplicate_objectids = result[
            "OBJECTID"
        ].duplicated(keep=False)

        if duplicate_objectids.any():
            duplicate_cols = [
                col
                for col in [
                    "OBJECTID",
                    "NPDES_NUM",
                    "OUTFALL",
                    "GIS_NUMBER",
                ]
                if col in result.columns
            ]

            raise ValueError(
                "TCEQ wastewater-outfall pagination "
                "returned duplicate OBJECTID values:\n"
                + result.loc[
                    duplicate_objectids,
                    duplicate_cols,
                ]
                .sort_values("OBJECTID")
                .head(50)
                .to_string(index=False)
            )

        null_geometry_count = int(
            result.geometry.isna().sum()
        )

        print(
            "[ReturnFlowSource] TCEQ outfall "
            "retrieval complete: "
            f"{len(result):,} features; "
            f"{null_geometry_count:,} without geometry"
        )

        return result

    # --------------------------------------------------
    # Combined source interface
    # --------------------------------------------------
    def fetch(
        self,
        start_fy: int,
        end_fy: int,
    ) -> dict[str, object]:
        """
        Retrieve all source datasets needed by ReturnComponent.
        """
        return {
            "dmr": self.load_dmr(
                start_fy,
                end_fy,
            ),
            "perm_feature_nmbrs": (
                self.load_perm_feature_nmbrs()
            ),
        }
