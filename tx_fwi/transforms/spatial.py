"""Spatial helpers used by diversion and return-flow processing."""
from __future__ import annotations
import pandas as pd
import geopandas as gpd


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

    joined = gpd.sjoin(gpts, w[[ws_col, "EST_GROUP", "geometry"]], how="left", predicate=predicate)
    joined[ws_col] = joined[ws_col].astype(str).str.strip().apply(lambda x: x.zfill(5) if x.isdigit() else x)
    return joined
