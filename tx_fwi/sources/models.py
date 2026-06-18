"""Load model-produced daily watershed outputs such as TxRR or future HEC-HMS outputs."""
from __future__ import annotations
from pathlib import Path
import pandas as pd


def load_daily_model_output(
    path: str | Path,
    *,
    date_col: str = "date",
    ws_col: str = "ws_id",
    value_col: str = "value_afday",
    estuary_col: str | None = "estuary",
) -> pd.DataFrame:
    """
    Load a daily model output table and normalize it to canonical columns.

    Expected output columns:
    date, id, id_type, estuary, component, source, value_afday,
    flow_role, count_in_basin_sum, note
    """
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col]).dt.normalize()
    out["id"] = df[ws_col].astype(str).str.strip().apply(lambda x: x.zfill(5) if x.isdigit() else x)
    out["id_type"] = "watershed"
    out["estuary"] = df[estuary_col] if estuary_col and estuary_col in df.columns else None
    out["component"] = "ungaged"
    out["source"] = "model"
    out["value_afday"] = pd.to_numeric(df[value_col], errors="coerce")
    out["flow_role"] = "direct"
    out["count_in_basin_sum"] = 1
    out["note"] = None
    return out
