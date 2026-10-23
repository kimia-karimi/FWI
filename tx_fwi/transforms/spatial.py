"""Spatial helpers used by diversion and return-flow processing."""
from __future__ import annotations
import pandas as pd
import geopandas as gpd
from collections.abc import Sequence

"""
Shared spatial-assignment helpers for diversion and return-flow processing.

Assignment rule
---------------
1. One source record matching one watershed:
   assign that watershed.

2. One source record matching multiple watersheds in one estuary:
   retain the record once;
   choose a deterministic canonical WS_ID;
   preserve all candidate WS_ID values in the audit.

3. One source record matching watersheds in different estuaries:
   do not assign automatically;
   return the record in the unresolved output.

4. One source record matching no watershed:
   return the record in the unresolved output.

This preserves estuary totals without duplicating diversion or return flow.
"""



DEFAULT_WATERSHED_COLUMNS = [
    "WS_ID",
    "Estuary",
    "HAS_UNGAGE",
    "geometry",
]


def normalize_ws_id(value):
    """
    Normalize a watershed identifier while preserving nonnumeric IDs.
    """
    if value is None or pd.isna(value):
        return pd.NA

    text = str(value).strip()

    if not text:
        return pd.NA

    if text.endswith(".0"):
        text = text[:-2]

    return text.zfill(5) if text.isdigit() else text

def fill_missing_geometry_from_group(
    records: pd.DataFrame,
    geometry_lookup: gpd.GeoDataFrame,
    *,
    group_col: str,
    feature_col: str,
    geometry_col: str = "geometry",
    donor_sort_cols: Sequence[str] | None = None,
    assignment_col: str = "geometry_assignment_method",
    donor_feature_col: str = "geometry_donor_feature",
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """
    Join records to exact feature geometry, then fill missing geometry
    from the first available feature within the same group.

    Intended uses
    -------------
    Return flow:
        group_col = "PERMIT_NUM"
        feature_col = "PERM_FEATURE_NMBR"

    Diversion:
        group_col = "WR_ID"
        feature_col = a point identifier, when available

    Rules
    -----
    1. Exact group + feature geometry is always preferred.
    2. Missing geometry receives the first available geometry from the
       same group.
    3. The fallback geometry is used only once for each source record.
    4. The donor feature is retained for auditing.
    5. Records with no geometry anywhere in the group remain unresolved.

    Returns
    -------
    assigned
        Records with exact or group-fallback geometry.

    unresolved
        Records for which the group has no available geometry.
    """
    record_required = [
        group_col,
        feature_col,
    ]

    lookup_required = [
        group_col,
        feature_col,
        geometry_col,
    ]

    missing_record_cols = [
        col
        for col in record_required
        if col not in records.columns
    ]

    missing_lookup_cols = [
        col
        for col in lookup_required
        if col not in geometry_lookup.columns
    ]

    if missing_record_cols:
        raise KeyError(
            "Records are missing geometry-join columns: "
            + ", ".join(missing_record_cols)
        )

    if missing_lookup_cols:
        raise KeyError(
            "Geometry lookup is missing columns: "
            + ", ".join(missing_lookup_cols)
        )

    if not isinstance(
        geometry_lookup,
        gpd.GeoDataFrame,
    ):
        raise TypeError(
            "geometry_lookup must be a GeoDataFrame."
        )

    if geometry_lookup.crs is None:
        raise ValueError(
            "geometry_lookup has no CRS."
        )

    source_records = records.copy()
    lookup = geometry_lookup.copy()

    # Keep only usable group, feature, and geometry records.
    lookup = lookup.dropna(
        subset=[
            group_col,
            feature_col,
            geometry_col,
        ]
    ).copy()

    if lookup.empty:
        unresolved = source_records.copy()

        unresolved[assignment_col] = (
            "unresolved_no_group_geometry"
        )

        unresolved[donor_feature_col] = pd.NA

        unresolved = gpd.GeoDataFrame(
            unresolved,
            geometry=[
                None
            ] * len(unresolved),
            crs=geometry_lookup.crs,
        )

        return unresolved.iloc[0:0].copy(), unresolved

    # Check whether the same group + feature key has several
    # genuinely different geometries.
    lookup["_geometry_wkt"] = (
        lookup[geometry_col]
        .to_wkt()
    )

    geometry_key_summary = (
        lookup
        .groupby(
            [
                group_col,
                feature_col,
            ],
            dropna=False,
            as_index=False,
        )
        .agg(
            n_distinct_geometries=(
                "_geometry_wkt",
                "nunique",
            )
        )
    )

    conflicting_keys = (
        geometry_key_summary.loc[
            geometry_key_summary[
                "n_distinct_geometries"
            ] > 1
        ]
        .copy()
    )

    if not conflicting_keys.empty:
        conflict_rows = lookup.merge(
            conflicting_keys[
                [
                    group_col,
                    feature_col,
                ]
            ],
            on=[
                group_col,
                feature_col,
            ],
            how="inner",
        )

        raise ValueError(
            "Multiple distinct geometries exist for the same "
            f"{group_col} + {feature_col} key:\n"
            + conflict_rows[
                [
                    group_col,
                    feature_col,
                    "_geometry_wkt",
                ]
            ]
            .head(50)
            .to_string(index=False)
        )

    # Collapse repeated copies of the same exact geometry.
    exact_lookup = (
        lookup
        .drop_duplicates(
            subset=[
                group_col,
                feature_col,
            ],
            keep="first",
        )
        [
            [
                group_col,
                feature_col,
                geometry_col,
            ]
        ]
        .copy()
    )

    exact_lookup = exact_lookup.rename(
        columns={
            geometry_col: "_exact_geometry",
        }
    )

    joined = source_records.merge(
        exact_lookup,
        on=[
            group_col,
            feature_col,
        ],
        how="left",
        validate="many_to_one",
        indicator="_exact_geometry_merge",
    )

    joined[assignment_col] = pd.NA
    joined[donor_feature_col] = pd.NA

    exact_mask = (
        joined["_exact_geometry"]
        .notna()
    )

    joined.loc[
        exact_mask,
        assignment_col,
    ] = "exact_feature_geometry"

    joined.loc[
        exact_mask,
        donor_feature_col,
    ] = joined.loc[
        exact_mask,
        feature_col,
    ]

    # Build a stable group-level donor table.
    #
    # Use the supplied donor columns when available.
    # Otherwise sort by feature identifier.
    if donor_sort_cols is None:
        donor_sort_cols = [
            feature_col,
        ]

    actual_sort_cols = [
        col
        for col in donor_sort_cols
        if col in lookup.columns
    ]

    if feature_col not in actual_sort_cols:
        actual_sort_cols.append(
            feature_col
        )

    donor_lookup = (
        lookup
        .sort_values(
            by=[
                group_col,
                *actual_sort_cols,
            ],
            kind="stable",
            na_position="last",
        )
        .drop_duplicates(
            subset=[group_col],
            keep="first",
        )
        [
            [
                group_col,
                feature_col,
                geometry_col,
            ]
        ]
        .rename(
            columns={
                feature_col: (
                    "_fallback_donor_feature"
                ),
                geometry_col: (
                    "_fallback_geometry"
                ),
            }
        )
    )

    joined = joined.merge(
        donor_lookup,
        on=group_col,
        how="left",
        validate="many_to_one",
    )

    fallback_mask = (
        joined["_exact_geometry"].isna()
        & joined["_fallback_geometry"].notna()
    )

    joined.loc[
        fallback_mask,
        assignment_col,
    ] = "group_first_geometry_fallback"

    joined.loc[
        fallback_mask,
        donor_feature_col,
    ] = joined.loc[
        fallback_mask,
        "_fallback_donor_feature",
    ]

    # Exact geometry takes precedence over fallback geometry.
    joined[geometry_col] = (
        joined["_exact_geometry"]
        .where(
            joined["_exact_geometry"].notna(),
            joined["_fallback_geometry"],
        )
    )

    unresolved_mask = (
        joined[geometry_col].isna()
    )

    joined.loc[
        unresolved_mask,
        assignment_col,
    ] = "unresolved_no_group_geometry"

    assigned = joined.loc[
        ~unresolved_mask
    ].copy()

    unresolved = joined.loc[
        unresolved_mask
    ].copy()

    temporary_cols = [
        "_exact_geometry",
        "_fallback_geometry",
        "_fallback_donor_feature",
        "_exact_geometry_merge",
    ]

    assigned = assigned.drop(
        columns=temporary_cols,
        errors="ignore",
    )

    unresolved = unresolved.drop(
        columns=temporary_cols,
        errors="ignore",
    )

    assigned = gpd.GeoDataFrame(
        assigned,
        geometry=geometry_col,
        crs=geometry_lookup.crs,
    )

    unresolved = gpd.GeoDataFrame(
        unresolved,
        geometry=geometry_col,
        crs=geometry_lookup.crs,
    )

    return assigned, unresolved
   
def prepare_watersheds(
    watersheds: gpd.GeoDataFrame,
    *,
    ws_col: str = "WS_ID",
    estuary_col: str = "Estuary",
    include_flag_col: str | None = "HAS_UNGAGE",
    include_flag_value=1,
    target_crs=None,
) -> gpd.GeoDataFrame:
    """
    Prepare watershed polygons for point assignment.

    Watersheds are dissolved by WS_ID and estuary so duplicate polygon
    pieces for the same watershed do not create duplicate point matches.

    If include_flag_col is provided, only rows matching include_flag_value
    are retained.
    """
    required = [
        ws_col,
        estuary_col,
        "geometry",
    ]

    if include_flag_col is not None:
        required.append(include_flag_col)

    missing = [
        col for col in required
        if col not in watersheds.columns
    ]

    if missing:
        raise KeyError(
            "Watershed registry is missing columns: "
            + ", ".join(missing)
        )

    prepared = watersheds[required].copy()

    if include_flag_col is not None:
        prepared = prepared.loc[
            prepared[include_flag_col].eq(
                include_flag_value
            )
        ].copy()

    prepared = prepared.dropna(
        subset=[
            ws_col,
            estuary_col,
            "geometry",
        ]
    ).copy()

    prepared[ws_col] = (
        prepared[ws_col]
        .map(normalize_ws_id)
    )

    prepared[estuary_col] = (
        prepared[estuary_col]
        .astype("string")
        .str.strip()
    )

    prepared = prepared.loc[
        prepared[ws_col].notna()
        & prepared[estuary_col].notna()
    ].copy()

    if prepared.crs is None:
        raise ValueError(
            "Watershed GeoDataFrame has no CRS."
        )

    if target_crs is not None:
        prepared = prepared.to_crs(
            target_crs
        )

    dissolve_cols = [
        ws_col,
        estuary_col,
    ]

    if include_flag_col is not None:
        dissolve_cols.append(
            include_flag_col
        )

    prepared = prepared.dissolve(
        by=dissolve_cols,
        as_index=False,
    )

    return gpd.GeoDataFrame(
        prepared,
        geometry="geometry",
        crs=prepared.crs,
    )


def _candidate_string(values) -> str | pd.NA:
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

    if not unique_values:
        return pd.NA

    return ";".join(unique_values)


def resolve_same_estuary_matches(
    spatial_matches: gpd.GeoDataFrame,
    *,
    record_cols: Sequence[str],
    ws_col: str = "WS_ID",
    estuary_col: str = "Estuary",
) -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
]:
    """
    Resolve point-to-watershed matches without duplicating source values.

    Parameters
    ----------
    spatial_matches
        Output of a left point-to-polygon spatial join.

    record_cols
        Columns defining one hydrologic source record.

        Return-flow example:
            EXTERNAL_PERMIT_NMBR
            PERMIT_NUM
            PERM_FEATURE_NMBR
            MONITORING_PERIOD_END_DATE

        Diversion assignment example:
            WR_ID

        The diversion amount itself remains grouped by WR_ID + YEAR,
        but the physical assignment is stable by WR_ID.

    Returns
    -------
    resolved
        Exactly one row per resolved source record.

    unresolved
        Records with no watershed or candidate watersheds in more than
        one estuary.

    audit
        All candidate matches with the final assignment method.
    """
    missing_record_cols = [
        col for col in record_cols
        if col not in spatial_matches.columns
    ]

    if missing_record_cols:
        raise KeyError(
            "Spatial matches are missing record columns: "
            + ", ".join(missing_record_cols)
        )

    required = [
        ws_col,
        estuary_col,
        "geometry",
    ]

    missing = [
        col for col in required
        if col not in spatial_matches.columns
    ]

    if missing:
        raise KeyError(
            "Spatial matches are missing columns: "
            + ", ".join(missing)
        )

    work = spatial_matches.copy()

    work[ws_col] = (
        work[ws_col]
        .map(normalize_ws_id)
    )

    work[estuary_col] = (
        work[estuary_col]
        .astype("string")
        .str.strip()
    )

    resolved_groups = []
    unresolved_groups = []
    audit_groups = []

    grouped = work.groupby(
        list(record_cols),
        dropna=False,
        sort=False,
    )

    for _, group in grouped:
        group = group.copy()

        watershed_ids = sorted(
            group[ws_col]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )

        estuaries = sorted(
            group[estuary_col]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )

        candidate_ws_ids = (
            ";".join(watershed_ids)
            if watershed_ids
            else pd.NA
        )

        candidate_estuaries = (
            ";".join(estuaries)
            if estuaries
            else pd.NA
        )

        group["candidate_WS_IDs"] = (
            candidate_ws_ids
        )

        group["candidate_estuaries"] = (
            candidate_estuaries
        )

        group["n_candidate_watersheds"] = (
            len(watershed_ids)
        )

        group["n_candidate_estuaries"] = (
            len(estuaries)
        )

        if not watershed_ids:
            group[
                "spatial_assignment_method"
            ] = "unresolved_no_watershed"

            group[
                "spatial_assignment_resolved"
            ] = False

            unresolved_groups.append(group)
            audit_groups.append(group)
            continue

        if len(estuaries) > 1:
            group[
                "spatial_assignment_method"
            ] = "unresolved_multiple_estuaries"

            group[
                "spatial_assignment_resolved"
            ] = False

            unresolved_groups.append(group)
            audit_groups.append(group)
            continue

        # One watershed is unambiguous. Multiple watersheds in the same
        # estuary are estuary-resolved but watershed-ambiguous.
        canonical_ws_id = watershed_ids[0]
        common_estuary = estuaries[0]

        selected = (
            group.loc[
                group[ws_col]
                .astype("string")
                .eq(canonical_ws_id)
            ]
            .sort_values(
                by=[ws_col],
                kind="stable",
            )
            .iloc[[0]]
            .copy()
        )

        selected[ws_col] = canonical_ws_id
        selected[estuary_col] = (
            common_estuary
        )

        selected[
            "candidate_WS_IDs"
        ] = candidate_ws_ids

        selected[
            "candidate_estuaries"
        ] = candidate_estuaries

        selected[
            "n_candidate_watersheds"
        ] = len(watershed_ids)

        selected[
            "n_candidate_estuaries"
        ] = len(estuaries)

        selected[
            "spatial_assignment_resolved"
        ] = True

        if len(watershed_ids) == 1:
            method = (
                "single_watershed_assignment"
            )
        else:
            method = (
                "same_estuary_boundary_assignment"
            )

        selected[
            "spatial_assignment_method"
        ] = method

        group[
            "spatial_assignment_method"
        ] = method

        group[
            "spatial_assignment_resolved"
        ] = True

        group[
            "selected_WS_ID"
        ] = canonical_ws_id

        group[
            "selected_estuary"
        ] = common_estuary

        resolved_groups.append(selected)
        audit_groups.append(group)

    if resolved_groups:
        resolved = pd.concat(
            resolved_groups,
            ignore_index=True,
            sort=False,
        )
    else:
        resolved = work.iloc[0:0].copy()

    if unresolved_groups:
        unresolved = pd.concat(
            unresolved_groups,
            ignore_index=True,
            sort=False,
        )
    else:
        unresolved = work.iloc[0:0].copy()

    if audit_groups:
        audit = pd.concat(
            audit_groups,
            ignore_index=True,
            sort=False,
        )
    else:
        audit = work.iloc[0:0].copy()

    for frame_name, frame in [
        ("resolved", resolved),
        ("unresolved", unresolved),
        ("audit", audit),
    ]:
        if "geometry" not in frame.columns:
            continue

        if isinstance(
            spatial_matches,
            gpd.GeoDataFrame,
        ):
            if frame_name == "resolved":
                resolved = gpd.GeoDataFrame(
                    frame,
                    geometry="geometry",
                    crs=spatial_matches.crs,
                )
            elif frame_name == "unresolved":
                unresolved = (
                    gpd.GeoDataFrame(
                        frame,
                        geometry="geometry",
                        crs=spatial_matches.crs,
                    )
                )
            else:
                audit = gpd.GeoDataFrame(
                    frame,
                    geometry="geometry",
                    crs=spatial_matches.crs,
                )

    if not resolved.empty:
        duplicate_resolved = (
            resolved.duplicated(
                subset=list(record_cols),
                keep=False,
            )
        )

        if duplicate_resolved.any():
            raise AssertionError(
                "Spatial resolver returned more than "
                "one row per source record:\n"
                + resolved.loc[
                    duplicate_resolved,
                    list(record_cols)
                    + [
                        ws_col,
                        estuary_col,
                    ],
                ]
                .head(50)
                .to_string(index=False)
            )

    return resolved, unresolved, audit


def assign_geometries_to_watersheds(
    points: gpd.GeoDataFrame,
    watersheds: gpd.GeoDataFrame,
    *,
    record_cols: Sequence[str],
    ws_col: str = "WS_ID",
    estuary_col: str = "Estuary",
    include_flag_col: str | None = "HAS_UNGAGE",
    include_flag_value=1,
    predicate: str = "intersects",
) -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
]:
    """
    Assign point geometries to watersheds and resolve same-estuary
    multiple-watershed matches.

    A left join is used so unmatched records remain available for audit.
    """
    if not isinstance(
        points,
        gpd.GeoDataFrame,
    ):
        raise TypeError(
            "points must be a GeoDataFrame."
        )

    if points.crs is None:
        raise ValueError(
            "Point GeoDataFrame has no CRS."
        )

    prepared_watersheds = (
        prepare_watersheds(
            watersheds,
            ws_col=ws_col,
            estuary_col=estuary_col,
            include_flag_col=include_flag_col,
            include_flag_value=include_flag_value,
            target_crs=points.crs,
        )
    )

    polygon_columns = [
        ws_col,
        estuary_col,
    ]

    if include_flag_col is not None:
        polygon_columns.append(
            include_flag_col
        )

    polygon_columns.append("geometry")

    matches = gpd.sjoin(
        points,
        prepared_watersheds[
            polygon_columns
        ],
        how="left",
        predicate=predicate,
    )

    matches = matches.drop(
        columns=["index_right"],
        errors="ignore",
    )

    return resolve_same_estuary_matches(
        matches,
        record_cols=record_cols,
        ws_col=ws_col,
        estuary_col=estuary_col,
    )


def assign_xy_points_to_watersheds(
    points_df: pd.DataFrame,
    watersheds: gpd.GeoDataFrame,
    *,
    record_cols: Sequence[str],
    lon_col: str = "LONG_DD",
    lat_col: str = "LAT_DD",
    ws_col: str = "WS_ID",
    estuary_col: str = "Estuary",
    include_flag_col: str | None = "HAS_UNGAGE",
    include_flag_value=1,
    predicate: str = "intersects",
    crs: str = "EPSG:4269",
) -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
]:
    """
    Create point geometry from longitude and latitude, assign watersheds,
    and resolve same-estuary multiple-watershed matches.
    """
    required = (
        list(record_cols)
        + [
            lon_col,
            lat_col,
        ]
    )

    missing = [
        col for col in required
        if col not in points_df.columns
    ]

    if missing:
        raise KeyError(
            "Point data are missing columns: "
            + ", ".join(missing)
        )

    points = points_df.copy()

    points[lon_col] = pd.to_numeric(
        points[lon_col],
        errors="coerce",
    )

    points[lat_col] = pd.to_numeric(
        points[lat_col],
        errors="coerce",
    )

    invalid_coordinates = points.loc[
        points[lon_col].isna()
        | points[lat_col].isna()
    ].copy()

    valid_points = points.dropna(
        subset=[
            lon_col,
            lat_col,
        ]
    ).copy()

    point_gdf = gpd.GeoDataFrame(
        valid_points,
        geometry=gpd.points_from_xy(
            valid_points[lon_col],
            valid_points[lat_col],
        ),
        crs=crs,
    )

    resolved, unresolved, audit = (
        assign_geometries_to_watersheds(
            point_gdf,
            watersheds,
            record_cols=record_cols,
            ws_col=ws_col,
            estuary_col=estuary_col,
            include_flag_col=(
                include_flag_col
            ),
            include_flag_value=(
                include_flag_value
            ),
            predicate=predicate,
        )
    )

    if not invalid_coordinates.empty:
        invalid_coordinates[
            "spatial_assignment_method"
        ] = "unresolved_invalid_coordinates"

        invalid_coordinates[
            "spatial_assignment_resolved"
        ] = False

        invalid_coordinates[
            "candidate_WS_IDs"
        ] = pd.NA

        invalid_coordinates[
            "candidate_estuaries"
        ] = pd.NA

        invalid_coordinates[
            "n_candidate_watersheds"
        ] = 0

        invalid_coordinates[
            "n_candidate_estuaries"
        ] = 0

        invalid_geometry = gpd.GeoDataFrame(
            invalid_coordinates,
            geometry=[
                None
            ] * len(invalid_coordinates),
            crs=crs,
        )

        unresolved = pd.concat(
            [
                unresolved,
                invalid_geometry,
            ],
            ignore_index=True,
            sort=False,
        )

        audit = pd.concat(
            [
                audit,
                invalid_geometry,
            ],
            ignore_index=True,
            sort=False,
        )

    return resolved, unresolved, audit
def assign_points_to_watersheds(
    points_df: pd.DataFrame,
    watersheds: gpd.GeoDataFrame,
    *,
    lon_col: str = "LONG_DD",
    lat_col: str = "LAT_DD",
    ws_col: str = "WS_ID",
    predicate: str = "intersects",
    crs: str = "EPSG:4269",
) -> gpd.GeoDataFrame:
    """
    Assign point records to coastal watersheds by spatial join.

    Used by both diversion and return-flow pipelines after ID-to-coordinate
    joins have produced latitude/longitude points.
    """
    pts = points_df.copy()
    pts[lon_col] = pd.to_numeric(pts[lon_col], errors="coerce")
    pts[lat_col] = pd.to_numeric(pts[lat_col], errors="coerce")
    pts = pts.dropna(subset=[lon_col, lat_col])

    gpts = gpd.GeoDataFrame(
        pts,
        geometry=gpd.points_from_xy(pts[lon_col], pts[lat_col]),
        crs=crs,
    )

    w = watersheds.copy()
    if w.crs is None:
        w = w.set_crs(crs)
    w = w.to_crs(gpts.crs)

    joined = gpd.sjoin(gpts, w[[ws_col, "Estuary", "geometry"]], how="left", predicate=predicate)
    joined[ws_col] = joined[ws_col].astype(str).str.strip().apply(lambda x: x.zfill(5) if x.isdigit() else x)
    return joined
