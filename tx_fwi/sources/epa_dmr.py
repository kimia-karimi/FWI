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
        # --------------------------------------------------
    # EPA DMR and limit retrieval
    # --------------------------------------------------
    def _download_fy_zip(
        self,
        fiscal_year: int,
    ) -> io.BytesIO | None:
        """
        Download one EPA Texas fiscal-year DMR/limits ZIP file.

        Return None when EPA reports that the requested archive does
        not exist. Other HTTP errors and malformed ZIP responses are
        raised.
        """
        filename = (
            f"{self.STATE}_FY{fiscal_year}_"
            "NPDES_DMRS_LIMITS.zip"
        )

        url = f"{self.BASE_URL}/{filename}"

        print(
            "[ReturnFlowSource] Downloading EPA "
            f"DMR/limits archive: FY{fiscal_year}"
        )

        response = requests.get(
            url,
            timeout=300,
            verify=certifi.where(),
        )

        if response.status_code == 404:
            print(
                "[ReturnFlowSource] EPA DMR/limits "
                f"archive not found: FY{fiscal_year}"
            )
            return None

        response.raise_for_status()

        if not response.content:
            raise RuntimeError(
                "EPA returned an empty DMR/limits ZIP "
                f"response for fiscal year {fiscal_year}."
            )

        return io.BytesIO(response.content)

    @staticmethod
    def _csv_members(
        archive: zipfile.ZipFile,
    ) -> list[str]:
        """
        Return the non-directory CSV members in an EPA archive.
        """
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
        """
        Read only the header row from one archived CSV member.
        """
        try:
            header = pd.read_csv(
                archive.open(member),
                nrows=0,
                low_memory=False,
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not read the header from EPA "
                f"archive member {member!r}."
            ) from exc

        return {
            str(column).strip().upper()
            for column in header.columns
        }

    @classmethod
    def _classify_archive_members(
        cls,
        archive: zipfile.ZipFile,
    ) -> tuple[str, str]:
        """
        Identify the DMR-value CSV and limit-definition CSV.

        Identification is based primarily on required columns rather
        than filenames because archive member names may differ among
        fiscal years.

        Returns
        -------
        dmr_member
            CSV containing reported DMR values.

        limits_member
            CSV containing permit-limit definitions.
        """
        csv_members = cls._csv_members(
            archive
        )

        if not csv_members:
            raise RuntimeError(
                "EPA DMR/limits ZIP does not contain "
                "any CSV files. Archive members include:\n"
                + "\n".join(
                    archive.namelist()[:50]
                )
            )

        dmr_required_columns = {
            "EXTERNAL_PERMIT_NMBR",
            "PERM_FEATURE_NMBR",
            "MONITORING_PERIOD_END_DATE",
            "PARAMETER_CODE",
            "DMR_VALUE_STANDARD_UNITS",
        }

        limits_required_columns = {
            "EXTERNAL_PERMIT_NMBR",
            "PERM_FEATURE_NMBR",
            "PARAMETER_CODE",
            "LIMIT_SET_ID",
            "LIMIT_SET_DESIGNATOR",
            "LIMIT_ID",
            "LIMIT_VALUE_ID",
            "STATISTICAL_BASE_CODE",
            "STATISTICAL_BASE_TYPE_CODE",
        }

        dmr_candidates: list[str] = []
        limits_candidates: list[str] = []
        member_columns: dict[str, set[str]] = {}

        for member in csv_members:
            columns = cls._read_member_columns(
                archive,
                member,
            )

            member_columns[member] = columns

            if dmr_required_columns.issubset(
                columns
            ):
                dmr_candidates.append(member)

            if limits_required_columns.issubset(
                columns
            ):
                limits_candidates.append(member)

        if not dmr_candidates:
            diagnostic = "\n".join(
                (
                    f"{member}: "
                    + ", ".join(
                        sorted(columns)[:25]
                    )
                )
                for member, columns
                in member_columns.items()
            )

            raise RuntimeError(
                "EPA archive does not contain a CSV "
                "with the required DMR-value columns. "
                "Expected at least:\n"
                + ", ".join(
                    sorted(
                        dmr_required_columns
                    )
                )
                + "\nAvailable CSV headers:\n"
                + diagnostic
            )

        if not limits_candidates:
            diagnostic = "\n".join(
                (
                    f"{member}: "
                    + ", ".join(
                        sorted(columns)[:25]
                    )
                )
                for member, columns
                in member_columns.items()
            )

            raise RuntimeError(
                "EPA archive does not contain a CSV "
                "with the required limit-definition "
                "columns. Expected at least:\n"
                + ", ".join(
                    sorted(
                        limits_required_columns
                    )
                )
                + "\nAvailable CSV headers:\n"
                + diagnostic
            )

        if len(dmr_candidates) > 1:
            # Prefer the candidate whose filename does not look
            # like the permit-limit definition file.
            preferred = [
                member
                for member in dmr_candidates
                if "LIMIT" not in member.upper()
            ]

            if len(preferred) == 1:
                dmr_candidates = preferred

        if len(limits_candidates) > 1:
            # Prefer the member explicitly identified as limits.
            preferred = [
                member
                for member in limits_candidates
                if "LIMIT" in member.upper()
            ]

            if len(preferred) == 1:
                limits_candidates = preferred

        if len(dmr_candidates) != 1:
            raise RuntimeError(
                "EPA archive contains multiple possible "
                "DMR-value CSV files and the correct "
                "member could not be determined:\n"
                + "\n".join(dmr_candidates)
            )

        if len(limits_candidates) != 1:
            raise RuntimeError(
                "EPA archive contains multiple possible "
                "limit-definition CSV files and the "
                "correct member could not be determined:\n"
                + "\n".join(limits_candidates)
            )

        dmr_member = dmr_candidates[0]
        limits_member = limits_candidates[0]

        if dmr_member == limits_member:
            raise RuntimeError(
                "EPA archive member classification "
                "identified the same CSV as both the "
                "DMR-value file and the limit-definition "
                f"file: {dmr_member!r}."
            )

        return (
            dmr_member,
            limits_member,
        )

    @staticmethod
    def _read_archive_member(
        archive: zipfile.ZipFile,
        member: str,
        *,
        fiscal_year: int,
        dataset_name: str,
    ) -> pd.DataFrame:
        """
        Read one EPA CSV archive member and validate that it contains
        data rows.
        """
        try:
            frame = pd.read_csv(
                archive.open(member),
                low_memory=False,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not read EPA {dataset_name} "
                f"CSV {member!r} for FY{fiscal_year}."
            ) from exc

        if frame.empty:
            raise RuntimeError(
                f"EPA {dataset_name} CSV {member!r} "
                f"for FY{fiscal_year} contains no rows."
            )

        frame["_source_fiscal_year"] = (
            fiscal_year
        )

        frame["_source_archive_member"] = (
            member
        )

        return frame

    def _load_fy_dmr_and_limits(
        self,
        fiscal_year: int,
    ) -> tuple[
        pd.DataFrame,
        pd.DataFrame,
    ] | None:
        """
        Download and load both EPA datasets for one fiscal year.

        The fiscal-year archive is considered complete only when both
        the DMR-value file and the limit-definition file are present
        and contain rows.
        """
        zip_buffer = self._download_fy_zip(
            fiscal_year
        )

        if zip_buffer is None:
            return None

        try:
            with zipfile.ZipFile(
                zip_buffer
            ) as archive:
                (
                    dmr_member,
                    limits_member,
                ) = self._classify_archive_members(
                    archive
                )

                print(
                    "[ReturnFlowSource] EPA FY"
                    f"{fiscal_year} DMR member: "
                    f"{dmr_member}"
                )

                print(
                    "[ReturnFlowSource] EPA FY"
                    f"{fiscal_year} limits member: "
                    f"{limits_member}"
                )

                dmr = self._read_archive_member(
                    archive,
                    dmr_member,
                    fiscal_year=fiscal_year,
                    dataset_name="DMR value",
                )

                limits = self._read_archive_member(
                    archive,
                    limits_member,
                    fiscal_year=fiscal_year,
                    dataset_name="limit definition",
                )

        except zipfile.BadZipFile as exc:
            raise RuntimeError(
                "EPA returned an invalid ZIP file "
                f"for fiscal year {fiscal_year}."
            ) from exc

        print(
            "[ReturnFlowSource] Loaded EPA FY"
            f"{fiscal_year}: "
            f"{len(dmr):,} DMR rows; "
            f"{len(limits):,} limit rows"
        )

        return (
            dmr,
            limits,
        )

    def load_dmr_and_limits(
        self,
        start_fy: int,
        end_fy: int,
    ) -> tuple[
        pd.DataFrame,
        pd.DataFrame,
    ]:
        """
        Load DMR values and limit definitions for an inclusive
        fiscal-year range.

        Every available annual archive must contain both datasets.
        A partial annual archive raises an error rather than allowing
        the return-flow workflow to continue with incomplete source
        information.
        """
        if end_fy < start_fy:
            raise ValueError(
                "end_fy cannot be earlier than "
                "start_fy."
            )

        dmr_frames: list[pd.DataFrame] = []
        limits_frames: list[pd.DataFrame] = []

        requested_years = list(
            range(
                start_fy,
                end_fy + 1,
            )
        )

        missing_archives: list[int] = []

        for fiscal_year in requested_years:
            loaded = (
                self._load_fy_dmr_and_limits(
                    fiscal_year
                )
            )

            if loaded is None:
                missing_archives.append(
                    fiscal_year
                )
                continue

            dmr, limits = loaded

            dmr_frames.append(dmr)
            limits_frames.append(limits)

        if not dmr_frames:
            print(
                "[ReturnFlowSource] No EPA DMR/"
                "limits archives were available for "
                f"FY{start_fy}-FY{end_fy}."
            )

            return (
                pd.DataFrame(),
                pd.DataFrame(),
            )

        if len(dmr_frames) != len(
            limits_frames
        ):
            raise AssertionError(
                "Internal EPA source error: loaded "
                "DMR and limit fiscal-year counts do "
                "not match."
            )

        dmr_result = pd.concat(
            dmr_frames,
            ignore_index=True,
            sort=False,
        )

        limits_result = pd.concat(
            limits_frames,
            ignore_index=True,
            sort=False,
        )

        loaded_dmr_years = set(
            pd.to_numeric(
                dmr_result[
                    "_source_fiscal_year"
                ],
                errors="coerce",
            )
            .dropna()
            .astype(int)
        )

        loaded_limit_years = set(
            pd.to_numeric(
                limits_result[
                    "_source_fiscal_year"
                ],
                errors="coerce",
            )
            .dropna()
            .astype(int)
        )

        if loaded_dmr_years != loaded_limit_years:
            raise RuntimeError(
                "EPA DMR and limit datasets do not "
                "cover the same fiscal years. "
                f"DMR years={sorted(loaded_dmr_years)}; "
                "limit years="
                f"{sorted(loaded_limit_years)}."
            )

        print(
            "[ReturnFlowSource] EPA DMR rows "
            f"loaded: {len(dmr_result):,}"
        )

        print(
            "[ReturnFlowSource] EPA limit rows "
            f"loaded: {len(limits_result):,}"
        )

        if missing_archives:
            print(
                "[ReturnFlowSource] EPA archives "
                "not available for fiscal years: "
                + ", ".join(
                    str(year)
                    for year
                    in missing_archives
                )
            )

        return (
            dmr_result,
            limits_result,
        )

    def load_dmr(
        self,
        start_fy: int,
        end_fy: int,
    ) -> pd.DataFrame:
        """
        Backward-compatible DMR-only interface.

        This method still validates and loads the corresponding limit
        file for every annual archive, then returns only the DMR-value
        DataFrame.
        """
        dmr, _ = self.load_dmr_and_limits(
            start_fy,
            end_fy,
        )

        return dmr

    def load_limits(
        self,
        start_fy: int,
        end_fy: int,
    ) -> pd.DataFrame:
        """
        Load only the limit-definition result while still validating
        that both files exist in every annual archive.
        """
        _, limits = (
            self.load_dmr_and_limits(
                start_fy,
                end_fy,
            )
        )

        return limits

    # Backward compatibility for older component code.
    def _load_dmr(
        self,
        start_fy: int,
        end_fy: int,
    ) -> pd.DataFrame:
        return self.load_dmr(
            start_fy,
            end_fy,
        )
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
        ) -> list:
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
        dmr, limits = (
            self.load_dmr_and_limits(
                start_fy,
                end_fy,
            )
        )
        return {
            "dmr": dmr,
            "limits": limits,
            "perm_feature_nmbrs": (
                self.load_perm_feature_nmbrs()
            ),
        }
