# tx_fwi/outputs/swis.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from coastal import models


# ----------------------------------------------------------------------
# SWIS configuration
# ----------------------------------------------------------------------

PARAMETER_ID = 43

# test_kk
HYDROLOGY_STATUS_ID = 4

QCFLAG_ID = 1
QCRULE_ID = 10

IS_ACTIVE = False


# Existing SWIS hydrology types only.
COMPONENT_TO_HYDROLOGY_TYPE = {
    "gaged": "gauge",
    "gage": "gauge",
    "gaged_local": "gauge",
    "gaged_adjusted": "gauge",
    "gaged_upstream": "gauge",
    "ungaged": "model",
    "model": "model",
    "diversion": "diversion",
    "return": "return",
    "return_flow": "return",
}


# These components are intentionally not loaded into SWIS.
EXCLUDED_COMPONENTS = {
    "precipitation",
    "precip",
    "evaporation",
    "evap",
}


# Map canonical estuary names to parent watershed codes in SWIS.
#
# The numeric database IDs are intentionally not hard-coded because
# IDs may differ between development, staging, and production.
ESTUARY_TO_WATERSHED_CODE = {
    "Sabine Lake": "sabine",
    "Brazos River Estuary": "brazos_river",
    "Galveston Bay": "galveston",
    "Matagorda Bay": "matagorda",
    "San Bernard River Estuary": "san_bernard",
    "East Matagorda Bay": "east_matagorda",
    "Aransas Bay": "aransas",
    "Corpus Christi Bay": "corpus_christi",
    "Laguna Madre Estuary": "laguna_madre",
    "San Antonio Bay": "san_antonio",
}


# The HydrologySeries model defines name as String(16).
MAX_SERIES_NAME_LENGTH = 16


CANONICAL_REQUIRED_COLUMNS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "value_afday",
]


SWIS_EXPORT_COLUMNS = [
    "date",
    "value",
    "location_level",
    "location_code",
    "hydrology_type",
    "series_name",
    "description",
]


# ----------------------------------------------------------------------
# Result objects
# ----------------------------------------------------------------------

@dataclass
class SwisPreparationResult:
    data: pd.DataFrame
    canonical_rows: int
    excluded_rows: int
    prepared_rows: int
    series_count: int


@dataclass
class SwisLoadResult:
    dry_run: bool = False

    series_created: int = 0
    series_reused: int = 0

    values_inserted: int = 0
    values_updated: int = 0
    values_unchanged: int = 0

    prepared_rows: int = 0
    errors: list[str] = field(
        default_factory=list
    )

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "series_created": self.series_created,
            "series_reused": self.series_reused,
            "values_inserted": self.values_inserted,
            "values_updated": self.values_updated,
            "values_unchanged": self.values_unchanged,
            "prepared_rows": self.prepared_rows,
            "errors": self.errors,
        }


# ----------------------------------------------------------------------
# Normalization helpers
# ----------------------------------------------------------------------

def _normalize_component(value) -> str:
    if value is None or pd.isna(value):
        return ""

    return str(value).strip().lower()


def _normalize_estuary(value) -> str:
    if value is None or pd.isna(value):
        return ""

    return str(value).strip()


def _normalize_location_code(value) -> str:
    """
    Normalize an FWI watershed/subwatershed identifier.

    Numeric identifiers are padded to five digits to match the
    formatting used by the canonical FWI workflow.
    """
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    if text.isdigit():
        return text.zfill(5)

    return text


def _safe_series_name(
    location_code: str,
    suffix: str,
) -> str:
    """
    Produce a HydrologySeries name no longer than 16 characters.
    """
    raw_name = f"{location_code}-{suffix}"

    return raw_name[:MAX_SERIES_NAME_LENGTH]


def _subwatershed_series_name(
    location_code: str,
    hydrology_type: str,
) -> str:
    suffixes = {
        "gage": "gage-kk",
        "model": "model-kk",
        "diversion": "div-kk",
        "return": "ret-kk",
    }

    suffix = suffixes[hydrology_type]

    return _safe_series_name(
        location_code,
        suffix,
    )


def _upstream_series_name(
    watershed_code: str,
) -> str:
    abbreviations = {
        "sabine": "sab",
        "brazos_river": "brz",
        "galveston": "galv",
        "matagorda": "mat",
        "san_bernard": "stb",
        "east_matagorda": "emat",
        "aransas": "ara",
        "corpus_christi": "cc",
        "laguna_madre": "lm",
        "san_antonio": "sa",
    }

    prefix = abbreviations.get(
        watershed_code,
        watershed_code[:5],
    )

    return _safe_series_name(
        prefix,
        "upgage-kk",
    )


# ----------------------------------------------------------------------
# Canonical-data preparation
# ----------------------------------------------------------------------

def _validate_canonical_columns(
    canonical_df: pd.DataFrame,
) -> None:
    missing = [
        col
        for col in CANONICAL_REQUIRED_COLUMNS
        if col not in canonical_df.columns
    ]

    if missing:
        raise KeyError(
            "Canonical FWI data are missing "
            "required SWIS columns: "
            + ", ".join(missing)
        )


def _normalize_canonical_data(
    canonical_df: pd.DataFrame,
) -> pd.DataFrame:
    _validate_canonical_columns(
        canonical_df
    )

    work = canonical_df.copy()

    work["date"] = pd.to_datetime(
        work["date"],
        errors="coerce",
    ).dt.date

    work["component"] = (
        work["component"]
        .map(_normalize_component)
    )

    work["estuary"] = (
        work["estuary"]
        .map(_normalize_estuary)
    )

    work["id"] = (
        work["id"]
        .map(_normalize_location_code)
    )

    work["value_afday"] = pd.to_numeric(
        work["value_afday"],
        errors="coerce",
    )

    invalid_dates = work["date"].isna()

    if invalid_dates.any():
        sample = work.loc[
            invalid_dates,
            [
                "date",
                "id",
                "component",
            ],
        ].head(20)

        raise ValueError(
            "Canonical FWI data contain invalid dates:\n"
            + sample.to_string(index=False)
        )

    invalid_values = (
        work["value_afday"].isna()
        | ~np.isfinite(work["value_afday"])
    )

    if invalid_values.any():
        sample = work.loc[
            invalid_values,
            [
                "date",
                "id",
                "component",
                "value_afday",
            ],
        ].head(20)

        raise ValueError(
            "Canonical FWI data contain missing "
            "or non-finite flow values:\n"
            + sample.to_string(index=False)
        )

    negative_values = (
        work["value_afday"] < 0
    )

    if negative_values.any():
        sample = work.loc[
            negative_values,
            [
                "date",
                "id",
                "estuary",
                "component",
                "value_afday",
            ],
        ].head(50)

        raise ValueError(
            "SWIS hydrology values must be "
            "nonnegative. Negative values found:\n"
            + sample.to_string(index=False)
        )

    return work


def _prepare_local_gage(
    work: pd.DataFrame,
) -> pd.DataFrame:
    """
    Prepare one final local gage contribution per subwatershed/date.

    A subwatershed should have either gaged_local or gaged_adjusted.
    If both nevertheless appear, gaged_adjusted is treated as the
    final local contribution and takes precedence.
    """
    gage_components = {
        "gaged",
        "gage",
        "gaged_local",
        "gaged_adjusted",
    }

    gage_rows = work.loc[
        work["component"].isin(
            gage_components
        )
    ].copy()

    if gage_rows.empty:
        return pd.DataFrame(
            columns=SWIS_EXPORT_COLUMNS
        )

    gage_rows["_component_priority"] = (
        gage_rows["component"]
        .map(
            {
                "gaged_adjusted": 0,
                "gaged_local": 1,
                "gaged": 1,
                "gage": 1,
            }
        )
        .fillna(99)
        .astype(int)
    )

    # Detect duplicate values within the same component before
    # applying adjusted-over-local precedence.
    same_component_duplicates = (
        gage_rows.duplicated(
            subset=[
                "id",
                "date",
                "component",
            ],
            keep=False,
        )
    )

    if same_component_duplicates.any():
        duplicate_groups = (
            gage_rows.loc[
                same_component_duplicates
            ]
            .groupby(
                [
                    "id",
                    "date",
                    "component",
                ],
                dropna=False,
            )["value_afday"]
            .nunique()
        )

        conflicting = duplicate_groups.loc[
            duplicate_groups > 1
        ]

        if not conflicting.empty:
            conflict_index = (
                conflicting
                .reset_index()[
                    [
                        "id",
                        "date",
                        "component",
                    ]
                ]
            )

            conflict_rows = gage_rows.merge(
                conflict_index,
                on=[
                    "id",
                    "date",
                    "component",
                ],
                how="inner",
            )

            raise ValueError(
                "Conflicting local gage values were "
                "found for the same subwatershed, "
                "date, and component:\n"
                + conflict_rows[
                    [
                        "id",
                        "date",
                        "component",
                        "value_afday",
                        "source",
                    ]
                ]
                .head(50)
                .to_string(index=False)
            )

    # Collapse exact repeated component/date values.
    gage_rows = (
        gage_rows
        .drop_duplicates(
            subset=[
                "id",
                "date",
                "component",
                "value_afday",
            ],
            keep="first",
        )
        .sort_values(
            [
                "id",
                "date",
                "_component_priority",
            ],
            kind="stable",
        )
    )

    # gaged_adjusted is the final local contribution.
    gage_rows = (
        gage_rows
        .drop_duplicates(
            subset=[
                "id",
                "date",
            ],
            keep="first",
        )
        .copy()
    )

    gage_rows["location_level"] = (
        "subwatershed"
    )

    gage_rows["location_code"] = (
        gage_rows["id"]
    )

    gage_rows["hydrology_type"] = (
        "gage"
    )

    gage_rows["series_name"] = (
        gage_rows["location_code"]
        .map(
            lambda code: (
                _subwatershed_series_name(
                    code,
                    "gage",
                )
            )
        )
    )

    gage_rows["description"] = (
        "tx_fwi final local gaged flow"
    )

    gage_rows = gage_rows.rename(
        columns={
            "value_afday": "value",
        }
    )

    return gage_rows[
        SWIS_EXPORT_COLUMNS
    ]


def _prepare_upstream_gage(
    work: pd.DataFrame,
) -> pd.DataFrame:
    """
    Sum all upstream/system gage components by estuary and date.

    Individual upstream components remain available in the canonical
    tx_fwi data. SWIS receives one parent-watershed gage series per
    estuary.
    """
    upstream = work.loc[
        work["component"].eq(
            "gaged_upstream"
        )
    ].copy()

    if upstream.empty:
        return pd.DataFrame(
            columns=SWIS_EXPORT_COLUMNS
        )

    missing_estuary = (
        upstream["estuary"].eq("")
    )

    if missing_estuary.any():
        sample = upstream.loc[
            missing_estuary,
            [
                "date",
                "id",
                "component",
                "value_afday",
            ],
        ].head(20)

        raise ValueError(
            "Upstream gage rows are missing "
            "an estuary assignment:\n"
            + sample.to_string(index=False)
        )

    unknown_estuaries = sorted(
        set(upstream["estuary"])
        - set(
            ESTUARY_TO_WATERSHED_CODE
        )
    )

    if unknown_estuaries:
        raise KeyError(
            "No SWIS parent-watershed mapping "
            "exists for these estuaries: "
            + ", ".join(unknown_estuaries)
        )

    upstream = (
        upstream
        .groupby(
            [
                "date",
                "estuary",
            ],
            as_index=False,
            dropna=False,
        )
        .agg(
            value=(
                "value_afday",
                "sum",
            )
        )
    )

    upstream["location_level"] = (
        "watershed"
    )

    upstream["location_code"] = (
        upstream["estuary"]
        .map(
            ESTUARY_TO_WATERSHED_CODE
        )
    )

    upstream["hydrology_type"] = (
        "gage"
    )

    upstream["series_name"] = (
        upstream["location_code"]
        .map(_upstream_series_name)
    )

    upstream["description"] = (
        "tx_fwi aggregated upstream "
        "gaged flow"
    )

    return upstream[
        SWIS_EXPORT_COLUMNS
    ]


def _prepare_subwatershed_component(
    work: pd.DataFrame,
    *,
    components: Iterable[str],
    hydrology_type: str,
    description: str,
) -> pd.DataFrame:
    """
    Prepare a component stored at the SWIS subwatershed level.
    """
    components = {
        _normalize_component(component)
        for component in components
    }

    subset = work.loc[
        work["component"].isin(
            components
        )
    ].copy()

    if subset.empty:
        return pd.DataFrame(
            columns=SWIS_EXPORT_COLUMNS
        )

    missing_id = subset["id"].eq("")

    if missing_id.any():
        sample = subset.loc[
            missing_id,
            [
                "date",
                "component",
                "estuary",
                "value_afday",
            ],
        ].head(20)

        raise ValueError(
            f"{hydrology_type} rows are missing "
            "subwatershed identifiers:\n"
            + sample.to_string(index=False)
        )

    duplicate_mask = subset.duplicated(
        subset=[
            "id",
            "date",
        ],
        keep=False,
    )

    if duplicate_mask.any():
        duplicate_groups = (
            subset.loc[
                duplicate_mask
            ]
            .groupby(
                [
                    "id",
                    "date",
                ],
                dropna=False,
            )["value_afday"]
            .nunique()
        )

        conflicting = duplicate_groups.loc[
            duplicate_groups > 1
        ]

        if not conflicting.empty:
            conflict_keys = (
                conflicting
                .reset_index()[
                    [
                        "id",
                        "date",
                    ]
                ]
            )

            conflict_rows = subset.merge(
                conflict_keys,
                on=[
                    "id",
                    "date",
                ],
                how="inner",
            )

            raise ValueError(
                f"Conflicting {hydrology_type} "
                "values were found for the same "
                "subwatershed and date:\n"
                + conflict_rows[
                    [
                        "id",
                        "date",
                        "component",
                        "value_afday",
                        "source",
                    ]
                ]
                .head(50)
                .to_string(index=False)
            )

    subset = subset.drop_duplicates(
        subset=[
            "id",
            "date",
            "value_afday",
        ],
        keep="first",
    ).copy()

    subset["location_level"] = (
        "subwatershed"
    )

    subset["location_code"] = subset["id"]

    subset["hydrology_type"] = (
        hydrology_type
    )

    subset["series_name"] = (
        subset["location_code"]
        .map(
            lambda code: (
                _subwatershed_series_name(
                    code,
                    hydrology_type,
                )
            )
        )
    )

    subset["description"] = description

    subset = subset.rename(
        columns={
            "value_afday": "value",
        }
    )

    return subset[
        SWIS_EXPORT_COLUMNS
    ]


def prepare_swis_export(
    canonical_df: pd.DataFrame,
) -> SwisPreparationResult:
    """
    Convert canonical tx_fwi output into SWIS-ready timeseries rows.

    This function does not access the database and can be tested
    independently.
    """
    canonical_rows = len(canonical_df)

    work = _normalize_canonical_data(
        canonical_df
    )

    excluded_mask = (
        work["component"]
        .isin(EXCLUDED_COMPONENTS)
    )

    excluded_rows = int(
        excluded_mask.sum()
    )

    work = work.loc[
        ~excluded_mask
    ].copy()

    unknown_components = sorted(
        set(work["component"])
        - set(
            COMPONENT_TO_HYDROLOGY_TYPE
        )
    )

    if unknown_components:
        raise KeyError(
            "No SWIS mapping exists for these "
            "FWI components: "
            + ", ".join(unknown_components)
        )

    frames = [
        _prepare_local_gage(work),
        _prepare_upstream_gage(work),
        _prepare_subwatershed_component(
            work,
            components=[
                "ungaged",
                "model",
            ],
            hydrology_type="model",
            description=(
                "tx_fwi ungaged/model flow"
            ),
        ),
        _prepare_subwatershed_component(
            work,
            components=[
                "diversion",
            ],
            hydrology_type="diversion",
            description=(
                "tx_fwi diversion flow"
            ),
        ),
        _prepare_subwatershed_component(
            work,
            components=[
                "return",
                "return_flow",
            ],
            hydrology_type="return",
            description=(
                "tx_fwi return flow"
            ),
        ),
    ]

    frames = [
        frame
        for frame in frames
        if not frame.empty
    ]

    if not frames:
        prepared = pd.DataFrame(
            columns=SWIS_EXPORT_COLUMNS
        )
    else:
        prepared = pd.concat(
            frames,
            ignore_index=True,
        )

    if prepared.empty:
        return SwisPreparationResult(
            data=prepared,
            canonical_rows=canonical_rows,
            excluded_rows=excluded_rows,
            prepared_rows=0,
            series_count=0,
        )

    prepared["date"] = pd.to_datetime(
        prepared["date"]
    ).dt.date

    prepared["value"] = pd.to_numeric(
        prepared["value"],
        errors="coerce",
    )

    invalid_values = (
        prepared["value"].isna()
        | ~np.isfinite(prepared["value"])
        | prepared["value"].lt(0)
    )

    if invalid_values.any():
        sample = prepared.loc[
            invalid_values
        ].head(50)

        raise ValueError(
            "Prepared SWIS data contain invalid "
            "or negative values:\n"
            + sample.to_string(index=False)
        )

    series_date_duplicates = (
        prepared.duplicated(
            subset=[
                "location_level",
                "location_code",
                "hydrology_type",
                "date",
            ],
            keep=False,
        )
    )

    if series_date_duplicates.any():
        sample = prepared.loc[
            series_date_duplicates
        ].sort_values(
            [
                "location_level",
                "location_code",
                "hydrology_type",
                "date",
            ]
        ).head(100)

        raise ValueError(
            "Prepared SWIS data contain more than "
            "one value for the same series/date:\n"
            + sample.to_string(index=False)
        )

    prepared = prepared.sort_values(
        [
            "location_level",
            "location_code",
            "hydrology_type",
            "date",
        ],
        kind="stable",
    ).reset_index(drop=True)

    series_count = (
        prepared[
            [
                "location_level",
                "location_code",
                "hydrology_type",
            ]
        ]
        .drop_duplicates()
        .shape[0]
    )

    return SwisPreparationResult(
        data=prepared,
        canonical_rows=canonical_rows,
        excluded_rows=excluded_rows,
        prepared_rows=len(prepared),
        series_count=series_count,
    )


# ----------------------------------------------------------------------
# SWIS database loader
# ----------------------------------------------------------------------

class SwisLoader:
    """
    Load prepared tx_fwi data into the existing SWIS hydrology schema.

    This loader directly uses the existing SQLAlchemy models. It does
    not call the Flask hydrology views and does not use CSV upload.
    """

    def __init__(
        self,
        session: Session,
        *,
        parameter_id: int = PARAMETER_ID,
        hydrologystatus_id: int = (
            HYDROLOGY_STATUS_ID
        ),
        qcflag_id: int = QCFLAG_ID,
        qcrule_id: int = QCRULE_ID,
        is_active: bool = IS_ACTIVE,
    ):
        self.session = session

        self.parameter_id = parameter_id
        self.hydrologystatus_id = (
            hydrologystatus_id
        )
        self.qcflag_id = qcflag_id
        self.qcrule_id = qcrule_id
        self.is_active = is_active

        self._type_ids = {}
        self._subwatersheds = {}
        self._watersheds = {}

    # ------------------------------------------------------------------
    # Reference validation and lookup
    # ------------------------------------------------------------------

    def validate_reference_records(
        self,
    ) -> None:
        parameter = (
            self.session
            .query(models.Parameter)
            .filter_by(
                id=self.parameter_id
            )
            .one_or_none()
        )

        if parameter is None:
            raise KeyError(
                "SWIS parameter ID "
                f"{self.parameter_id} was not found."
            )

        status = (
            self.session
            .query(models.HydrologyStatus)
            .filter_by(
                id=self.hydrologystatus_id
            )
            .one_or_none()
        )

        if status is None:
            raise KeyError(
                "SWIS hydrology status ID "
                f"{self.hydrologystatus_id} "
                "was not found."
            )

        qcflag = (
            self.session
            .query(models.QCFlag)
            .filter_by(
                id=self.qcflag_id
            )
            .one_or_none()
        )

        if qcflag is None:
            raise KeyError(
                "SWIS QC flag ID "
                f"{self.qcflag_id} was not found."
            )

        qcrule = (
            self.session
            .query(models.QCRule)
            .filter_by(
                id=self.qcrule_id
            )
            .one_or_none()
        )

        if qcrule is None:
            raise KeyError(
                "SWIS QC rule ID "
                f"{self.qcrule_id} was not found."
            )

        self._load_type_ids()

    def _load_type_ids(
        self,
    ) -> None:
        required_names = {
            "gage",
            "model",
            "diversion",
            "return",
        }

        records = (
            self.session
            .query(models.HydrologyType)
            .filter(
                models.HydrologyType.name.in_(
                    required_names
                )
            )
            .all()
        )

        self._type_ids = {
            str(record.name).strip().lower():
                record.id
            for record in records
        }

        missing = sorted(
            required_names
            - set(self._type_ids)
        )

        if missing:
            raise KeyError(
                "The following existing SWIS "
                "hydrology types were not found: "
                + ", ".join(missing)
            )

    def _get_subwatershed(
        self,
        watershed_code: str,
    ):
        normalized = _normalize_location_code(
            watershed_code
        )

        if normalized in self._subwatersheds:
            return self._subwatersheds[
                normalized
            ]

        # First try the normalized/padded code.
        record = (
            self.session
            .query(models.SubWatershed)
            .filter_by(
                watershed_code=normalized
            )
            .one_or_none()
        )

        # If SWIS stores an unpadded numeric code,
        # try that representation too.
        if (
            record is None
            and normalized.isdigit()
        ):
            unpadded = str(int(normalized))

            record = (
                self.session
                .query(models.SubWatershed)
                .filter_by(
                    watershed_code=unpadded
                )
                .one_or_none()
            )

        if record is None:
            raise KeyError(
                "No SWIS SubWatershed was found "
                "for FWI watershed code "
                f"{watershed_code!r}."
            )

        if record.geography_id is None:
            raise ValueError(
                "SWIS SubWatershed "
                f"{record.watershed_code!r} "
                "does not have a geography_id."
            )

        self._subwatersheds[
            normalized
        ] = record

        return record

    def _get_watershed(
        self,
        watershed_code: str,
    ):
        normalized = str(
            watershed_code
        ).strip().lower()

        if normalized in self._watersheds:
            return self._watersheds[
                normalized
            ]

        record = (
            self.session
            .query(models.Watershed)
            .filter_by(
                watershed_code=normalized
            )
            .one_or_none()
        )

        if record is None:
            raise KeyError(
                "No SWIS parent Watershed was found "
                f"for code {watershed_code!r}."
            )

        if record.geography_id is None:
            raise ValueError(
                "SWIS parent Watershed "
                f"{record.watershed_code!r} "
                "does not have a geography_id."
            )

        self._watersheds[
            normalized
        ] = record

        return record

    # ------------------------------------------------------------------
    # Series handling
    # ------------------------------------------------------------------

    def _find_series(
        self,
        *,
        location_level: str,
        location_record,
        hydrology_type_id: int,
        series_name: str,
    ):
        query = (
            self.session
            .query(models.HydrologySeries)
            .filter(
                models.HydrologySeries
                .hydrologytype_id
                == hydrology_type_id,
                models.HydrologySeries
                .hydrologystatus_id
                == self.hydrologystatus_id,
                models.HydrologySeries
                .parameter_id
                == self.parameter_id,
            )
        )

        if location_level == "subwatershed":
            query = query.filter(
                models.HydrologySeries
                .subwatershed_id
                == location_record.id,
                models.HydrologySeries
                .watershed_id
                .is_(None),
            )
        elif location_level == "watershed":
            query = query.filter(
                models.HydrologySeries
                .watershed_id
                == location_record.id,
                models.HydrologySeries
                .subwatershed_id
                .is_(None),
            )
        else:
            raise ValueError(
                "Unsupported SWIS location level: "
                f"{location_level}"
            )

        matches = query.all()

        if not matches:
            return None

        if len(matches) == 1:
            return matches[0]

        # If more than one series matches the structural key,
        # use the exact series name when possible.
        named_matches = [
            series
            for series in matches
            if series.name == series_name
        ]

        if len(named_matches) == 1:
            return named_matches[0]

        match_ids = [
            series.id
            for series in matches
        ]

        raise ValueError(
            "Multiple SWIS HydrologySeries records "
            "match the same location/type/status/"
            "parameter combination. "
            f"Matching IDs: {match_ids}"
        )

    def _create_series(
        self,
        *,
        location_level: str,
        location_record,
        hydrology_type_id: int,
        series_name: str,
        description: str,
    ):
        kwargs = {
            "name": series_name[
                :MAX_SERIES_NAME_LENGTH
            ],
            "description": description,
            "hydrologytype_id":
                hydrology_type_id,
            "hydrologystatus_id":
                self.hydrologystatus_id,
            "parameter_id":
                self.parameter_id,
            "qcflag_id":
                self.qcflag_id,
            "qcrule_id":
                self.qcrule_id,
            "geography_id":
                location_record.geography_id,
            "created_date":
                date.today(),
            "is_active":
                self.is_active,
        }

        if location_level == "subwatershed":
            kwargs["subwatershed_id"] = (
                location_record.id
            )

            kwargs["watershed_id"] = None

        elif location_level == "watershed":
            kwargs["watershed_id"] = (
                location_record.id
            )

            kwargs["subwatershed_id"] = None

        else:
            raise ValueError(
                "Unsupported SWIS location level: "
                f"{location_level}"
            )

        series = models.HydrologySeries(
            **kwargs
        )

        self.session.add(series)
        self.session.flush()

        return series

    def get_or_create_series(
        self,
        *,
        location_level: str,
        location_code: str,
        hydrology_type: str,
        series_name: str,
        description: str,
    ) -> tuple[object, bool]:
        hydrology_type = (
            str(hydrology_type)
            .strip()
            .lower()
        )

        if hydrology_type not in self._type_ids:
            raise KeyError(
                "SWIS hydrology type was not "
                f"loaded: {hydrology_type}"
            )

        type_id = self._type_ids[
            hydrology_type
        ]

        if location_level == "subwatershed":
            location_record = (
                self._get_subwatershed(
                    location_code
                )
            )
        elif location_level == "watershed":
            location_record = (
                self._get_watershed(
                    location_code
                )
            )
        else:
            raise ValueError(
                "Unsupported location level: "
                f"{location_level}"
            )

        series = self._find_series(
            location_level=location_level,
            location_record=location_record,
            hydrology_type_id=type_id,
            series_name=series_name,
        )

        if series is not None:
            return series, False

        series = self._create_series(
            location_level=location_level,
            location_record=location_record,
            hydrology_type_id=type_id,
            series_name=series_name,
            description=description,
        )

        return series, True

    # ------------------------------------------------------------------
    # Value upsert
    # ------------------------------------------------------------------

    def _upsert_series_values(
        self,
        series,
        values: pd.DataFrame,
        result: SwisLoadResult,
    ) -> None:
        values = values[
            [
                "date",
                "value",
            ]
        ].copy()

        values["date"] = pd.to_datetime(
            values["date"]
        ).dt.date

        values["value"] = pd.to_numeric(
            values["value"],
            errors="raise",
        )

        if (
            values["value"].lt(0).any()
            or ~np.isfinite(
                values["value"]
            ).all()
        ):
            raise ValueError(
                "Cannot load negative or "
                "non-finite SWIS values for "
                f"HydrologySeries ID {series.id}."
            )

        if values.duplicated(
            subset=["date"]
        ).any():
            duplicates = values.loc[
                values.duplicated(
                    subset=["date"],
                    keep=False,
                )
            ]

            raise ValueError(
                "More than one value was prepared "
                "for a SWIS series/date:\n"
                + duplicates.head(50)
                .to_string(index=False)
            )

        dates = values["date"].tolist()

        existing_records = (
            self.session
            .query(models.HydrologyValue)
            .filter(
                models.HydrologyValue
                .hydrologyseries_id
                == series.id,
                models.HydrologyValue
                .date.in_(dates),
            )
            .all()
        )

        existing_by_date = {
            record.date: record
            for record in existing_records
        }

        for row in values.itertuples(
            index=False
        ):
            value = float(row.value)

            existing = existing_by_date.get(
                row.date
            )

            if existing is None:
                record = models.HydrologyValue(
                    date=row.date,
                    value=value,
                    hydrologyseries_id=series.id,
                    hydrologytype_id=(
                        series.hydrologytype_id
                    ),
                )

                self.session.add(record)

                result.values_inserted += 1
                continue

            old_value = float(
                existing.value
            )

            if np.isclose(
                old_value,
                value,
                rtol=1e-12,
                atol=1e-9,
            ):
                result.values_unchanged += 1
                continue

            existing.value = value

            existing.hydrologytype_id = (
                series.hydrologytype_id
            )

            result.values_updated += 1

    def _update_series_metadata(
        self,
        series,
    ) -> None:
        (
            date_starting,
            date_ending,
            count,
        ) = (
            self.session
            .query(
                func.min(
                    models.HydrologyValue.date
                ),
                func.max(
                    models.HydrologyValue.date
                ),
                func.count(
                    models.HydrologyValue.id
                ),
            )
            .filter(
                models.HydrologyValue
                .hydrologyseries_id
                == series.id
            )
            .one()
        )

        series.date_starting = date_starting
        series.date_ending = date_ending
        series.count = int(count or 0)

    # ------------------------------------------------------------------
    # Public loading interface
    # ------------------------------------------------------------------

    def load_prepared(
        self,
        prepared_df: pd.DataFrame,
        *,
        dry_run: bool = False,
    ) -> SwisLoadResult:
        result = SwisLoadResult(
            dry_run=dry_run,
            prepared_rows=len(prepared_df),
        )

        if prepared_df.empty:
            return result

        self.validate_reference_records()

        series_cols = [
            "location_level",
            "location_code",
            "hydrology_type",
            "series_name",
            "description",
        ]

        grouped = prepared_df.groupby(
            series_cols,
            dropna=False,
            sort=True,
        )

        try:
            for series_key, values in grouped:
                (
                    location_level,
                    location_code,
                    hydrology_type,
                    series_name,
                    description,
                ) = series_key

                series, created = (
                    self.get_or_create_series(
                        location_level=(
                            location_level
                        ),
                        location_code=(
                            location_code
                        ),
                        hydrology_type=(
                            hydrology_type
                        ),
                        series_name=(
                            series_name
                        ),
                        description=(
                            description
                        ),
                    )
                )

                if created:
                    result.series_created += 1
                else:
                    result.series_reused += 1

                self._upsert_series_values(
                    series,
                    values,
                    result,
                )

                self.session.flush()

                self._update_series_metadata(
                    series
                )

            if dry_run:
                self.session.rollback()
            else:
                self.session.commit()

        except Exception as error:
            self.session.rollback()

            result.errors.append(
                str(error)
            )

            raise

        return result

    def load_canonical(
        self,
        canonical_df: pd.DataFrame,
        *,
        dry_run: bool = False,
    ) -> tuple[
        SwisPreparationResult,
        SwisLoadResult,
    ]:
        preparation = prepare_swis_export(
            canonical_df
        )

        load_result = self.load_prepared(
            preparation.data,
            dry_run=dry_run,
        )

        return preparation, load_result


# ----------------------------------------------------------------------
# Input helper
# ----------------------------------------------------------------------

def read_canonical_file(
    path: str | Path,
) -> pd.DataFrame:
    path = Path(path)

    suffix = path.suffix.lower()

    if suffix == ".parquet":
        return pd.read_parquet(path)

    if suffix == ".csv":
        return pd.read_csv(path)

    if suffix in {
        ".xlsx",
        ".xls",
    }:
        return pd.read_excel(path)

    raise ValueError(
        "Unsupported canonical input format: "
        f"{path.suffix}"
    )
