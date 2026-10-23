"""Normalize historic and new monthly FWI tables to comparable component rows.

Comparison mapping:
    new gaged = new gaged + new gaged_upstream
    new model = new model (including rows already mapped from ungaged/TxRR)
    diversion, return = same-named components

Inputs may be wide monthly tables or long tables. Historic long tables can identify
components by text or hydrologytype_id (1=gaged, 2=model, 3=diversion, 4=return).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

import pandas as pd

from validation_metrics import COMPONENTS, KEY_COLUMNS, add_row_diagnostics, assert_unique_keys

TYPE_ID_MAP = {1: "gaged", 2: "model", 3: "diversion", 4: "return"}
COMPONENT_MAP = {
    "gage": "gaged", "gaged": "gaged", "usgs": "gaged", "ibwc": "gaged",
    "gaged_upstream": "gaged_upstream", "upstream_gaged": "gaged_upstream",
    "model": "model", "ungaged": "model", "txrr": "model",
    "diversion": "diversion", "diverted": "diversion", "div": "diversion",
    "return": "return", "ret": "return", "return_flow": "return",
}
ESTUARY_ALIASES = {
    "sabine": "Sabine Lake", "sabine_lake": "Sabine Lake",
    "brazos": "Brazos River Estuary", "brazos_river": "Brazos River Estuary",
    "brazos_river_estuary": "Brazos River Estuary",
    "galveston": "Galveston Bay", "galveston_bay": "Galveston Bay",
    "matagorda": "Matagorda Bay", "matagorda_bay": "Matagorda Bay",
    "san_bernard": "San Bernard River Estuary",
    "san_bernard_river_estuary": "San Bernard River Estuary",
    "east_matagorda": "East Matagorda Bay", "east_matagorda_bay": "East Matagorda Bay",
    "aransas": "Aransas Bay", "aransas_bay": "Aransas Bay",
    "corpus_christi": "Corpus Christi Bay", "corpus_christi_bay": "Corpus Christi Bay",
    "laguna_madre": "Laguna Madre Estuary", "laguna_madre_estuary": "Laguna Madre Estuary",
    "san_antonio": "San Antonio Bay", "san_antonio_bay": "San Antonio Bay",
}


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_") for c in out.columns]
    return out


def _read(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _pick(df: pd.DataFrame, names: list[str], required: bool = True) -> str | None:
    col = next((x for x in names if x in df.columns), None)
    if required and col is None:
        raise ValueError(f"Missing one of {names}; found {list(df.columns)}")
    return col


def _estuary_name(value: object) -> str:
    raw = str(value).strip()
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return ESTUARY_ALIASES.get(key, raw)


def _date_parts(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    year = _pick(out, ["year"], required=False)
    month = _pick(out, ["month"], required=False)
    if year and month:
        out["Year"] = pd.to_numeric(out[year], errors="coerce").astype("Int64")
        out["Month"] = pd.to_numeric(out[month], errors="coerce").astype("Int64")
    else:
        date = _pick(out, ["date", "month_date", "datetime", "datetime_utc"])
        parsed = pd.to_datetime(out[date], errors="coerce")
        out["Year"] = parsed.dt.year.astype("Int64")
        out["Month"] = parsed.dt.month.astype("Int64")
    return out


def normalize_monthly(path: str | Path, *, source: str) -> pd.DataFrame:
    """Return Year/Month/Estuary/component/value_af from wide or long monthly data."""
    df = _date_parts(_clean_columns(_read(path)))
    estuary = _pick(df, ["estuary", "estuary_name", "watershed_name", "bay", "bay_system"])
    df["Estuary"] = df[estuary].map(_estuary_name)

    component_col = _pick(df, ["component", "hydrology_type", "hydrologytype", "type", "name"], False)
    type_id_col = _pick(df, ["hydrologytype_id", "hydrology_type_id", "type_id"], False)
    value_col = _pick(df, ["value_af", "value_acft", "value", "monthly_value", "flow_af"], False)

    if value_col and (component_col or type_id_col):
        if component_col:
            norm = (df[component_col].astype(str).str.strip().str.lower()
                    .str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_").map(COMPONENT_MAP))
        else:
            norm = pd.Series(index=df.index, dtype="object")
        if type_id_col:
            norm = norm.fillna(pd.to_numeric(df[type_id_col], errors="coerce").map(TYPE_ID_MAP))
        long = df.assign(component=norm, value_af=pd.to_numeric(df[value_col], errors="coerce"))
        long = long[long["component"].isin(COMPONENTS + ["gaged_upstream"])]
        long = (long.groupby(["Year", "Month", "Estuary", "component"], as_index=False,
                             dropna=False)["value_af"].sum(min_count=1))
    else:
        present = [c for c in COMPONENTS + ["gaged_upstream"] if c in df.columns]
        if not present:
            raise ValueError("No recognizable component columns found in wide table.")
        long = df.melt(id_vars=["Year", "Month", "Estuary"], value_vars=present,
                       var_name="component", value_name="value_af")
        long["value_af"] = pd.to_numeric(long["value_af"], errors="coerce")

    if source == "new":
        long["component"] = long["component"].replace({"gaged_upstream": "gaged"})
        long = (long.groupby(KEY_COLUMNS, as_index=False, dropna=False)["value_af"].sum(min_count=1))
    else:
        long = long[long["component"].isin(COMPONENTS)]

    assert_unique_keys(long, KEY_COLUMNS, label=source)
    return long


def compare_component_tables(
    historic_path: str | Path,
    new_path: str | Path,
    *,
    abs_tolerance_af: float = 1.0,
    pct_tolerance: float = 1.0,
) -> pd.DataFrame:
    old = normalize_monthly(historic_path, source="historic").rename(columns={"value_af": "historic_af"})
    new = normalize_monthly(new_path, source="new").rename(columns={"value_af": "new_af"})
    compared = old.merge(new, on=KEY_COLUMNS, how="outer", validate="one_to_one")
    compared = add_row_diagnostics(compared, abs_tolerance_af=abs_tolerance_af,
                                   pct_tolerance=pct_tolerance)
    return compared.sort_values(KEY_COLUMNS).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare historic and new monthly FWI components.")
    parser.add_argument("--historic", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--abs-tolerance-af", type=float, default=1.0)
    parser.add_argument("--pct-tolerance", type=float, default=1.0)
    args = parser.parse_args()
    out = compare_component_tables(args.historic, args.new,
                                   abs_tolerance_af=args.abs_tolerance_af,
                                   pct_tolerance=args.pct_tolerance)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"Wrote {args.output}: {len(out):,} rows")
    print(out["status"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
