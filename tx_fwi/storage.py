# tx_fwi/storage.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import pandas as pd


REQUIRED_COLS = [
    "date",
    "id",
    "id_type",
    "estuary",
    "component",
    "source",
    "value_afday",
    "flow_role",
    "count_in_basin_sum",
    "data_as_of",
    "note",
]


DEDUP_COLS = [
    "date",
    "id",
    "id_type",
    "component",
    "source",
    "flow_role",
]


import hashlib

from tx_fwi.qaqc.models import AppendResult


def stable_frame_hash(df: pd.DataFrame, *, sort_cols: list[str]) -> str:
    if df.empty:
        return hashlib.sha256(b"").hexdigest()
    ordered = df.sort_values(sort_cols, kind="stable").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).values.tobytes()
    return hashlib.sha256(row_hashes).hexdigest()


class CheckedAppendMixin:
    """Mix into the existing Storage class or copy append_checked into it."""

    def append_checked(
        self,
        df: pd.DataFrame,
        *,
        component: str,
        run_start,
        run_end,
        prior_watermark,
    ) -> AppendResult:
        prepared = df.copy()
        prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce").dt.normalize()
        key_cols = ["date", "id", "id_type", "component", "source", "flow_role"]

        if prepared[key_cols].isna().any().any():
            return AppendResult("blocked", len(prepared), 0, _date_text(prior_watermark), _date_text(prior_watermark), None, None, "Prepared append contains null key fields.")
        if prepared.duplicated(subset=key_cols, keep=False).any():
            return AppendResult("blocked", len(prepared), 0, _date_text(prior_watermark), _date_text(prior_watermark), None, None, "Prepared append contains duplicate output keys.")

        existing = self.read_component(component)
        before_history = _history_through(existing, prior_watermark)
        before_hash = stable_frame_hash(before_history, sort_cols=key_cols)

        if prior_watermark is not None and (prepared["date"] <= pd.Timestamp(prior_watermark)).any():
            return AppendResult("blocked", len(prepared), 0, _date_text(prior_watermark), _date_text(prior_watermark), before_hash, before_hash, "Routine append overlaps approved historical dates.")

        appended_rows = self.append(prepared, run_start=run_start, run_end=run_end)
        stored_after = self.read_component(component)
        after_history = _history_through(stored_after, prior_watermark)
        after_hash = stable_frame_hash(after_history, sort_cols=key_cols)

        if before_hash != after_hash:
            raise RuntimeError("Approved historical records changed during routine append.")
        if appended_rows != len(prepared):
            raise RuntimeError("Stored append count does not match prepared record count.")

        resulting_watermark = prepared["date"].max()
        self.set_watermark(component, resulting_watermark)
        return AppendResult(
            status="success",
            prepared_rows=len(prepared),
            appended_rows=appended_rows,
            prior_watermark=_date_text(prior_watermark),
            resulting_watermark=_date_text(resulting_watermark),
            historical_hash_before=before_hash,
            historical_hash_after=after_hash,
            message="Append completed without changing approved history.",
        )


def _history_through(df: pd.DataFrame, watermark) -> pd.DataFrame:
    if watermark is None or df.empty:
        return df.iloc[0:0].copy()
    dates = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    return df.loc[dates <= pd.Timestamp(watermark).normalize()].copy()


def _date_text(value) -> str | None:
    return str(pd.Timestamp(value).date()) if value is not None else None

@dataclass
class Storage:
    """
    Single-file append storage for FWI timeseries.

    root example:
    Path(r"\\\\fileserver\\CoastalScience\\Data\\Hydrology\\fwi_master")
    """

    root: Path

    @property
    def master_path(self) -> Path:
        return self.root / "data"/ "fwi_timeseries.parquet"

    @property
    def state_path(self) -> Path:
        return self.root / "data"/ "watermarks.json"
    @property
    def incremental_dir(self) -> Path:
        return self.root / "data" / "incremental"

    def write_incremental(
        self,
        df: pd.DataFrame,
        run_start,
        run_end
    ) -> Path:

        start = pd.to_datetime(
            df["date"].min()
        )

        end = pd.to_datetime(
            df["date"].max()
        )

        filename = (
            f"FWI_"
            f"{run_start:%Y%m%d}_"
            f"{run_end:%Y%m%d}.parquet"
        )


        self.incremental_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        path = (
            self.incremental_dir
            / filename
        )

        df.to_parquet(
            path,
            index=False,
        )

        return path
        
    def write_manifest(
        self,
        df: pd.DataFrame,
    ):

        start = pd.to_datetime(
            df["date"].min()
        )

        end = pd.to_datetime(
            df["date"].max()
        )

        manifest = {
            "run_date":
                pd.Timestamp.utcnow()
                .strftime("%Y-%m-%d"),

            "run_start":
                start.strftime("%Y-%m-%d"),

            "run_end":
                end.strftime("%Y-%m-%d"),

            "rows":
                len(df),

            "components": {}
        } 

        for comp, grp in df.groupby("component"):

            manifest["components"][comp] = {

                "rows":
                    len(grp),

                "data_start":
                    str(grp["date"].min().date()),

                "data_end":
                    str(grp["date"].max().date()),

                "data_as_of":
                    str(
                        pd.to_datetime(
                            grp["data_as_of"].max()
                        ).date()
                    ),
            }

        filename = (
            f"FWI_"
            f"{start:%Y%m%d}_"
            f"{end:%Y%m%d}.json"
        )

        path = (
            self.incremental_dir
            / filename
        )

        path.write_text(
            json.dumps(
                manifest,
                indent=2,
            ),
            encoding="utf-8",
        )

        return path
    def load_watermarks(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    @property
    def component_status_path(self) -> Path:
        return (
            self.root
            / "metadata"
            / "component_status.json"
        )
    def save_watermarks(self, wm: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(wm, indent=2), encoding="utf-8")

    def get_watermark(
        self,
        component: str,
        default: str | None = None,
    ) -> pd.Timestamp | None:
        wm = self.load_watermarks()
        value = wm.get(component, default)
        return pd.to_datetime(value) if value else None

    def set_watermark(self, component: str, dt: pd.Timestamp) -> None:
        wm = self.load_watermarks()
        wm[component] = pd.to_datetime(dt).strftime("%Y-%m-%d")
        self.save_watermarks(wm)

    def validate_schema(self, df: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()

        self.validate_schema(out)

        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        out["id"] = out["id"].astype(str)
        out["id_type"] = out["id_type"].astype(str)
        out["estuary"] = out["estuary"].astype(str)
        out["component"] = out["component"].astype(str)
        out["source"] = out["source"].astype(str)
        out["value_afday"] = pd.to_numeric(out["value_afday"], errors="coerce")
        out["count_in_basin_sum"] = (
            pd.to_numeric(out["count_in_basin_sum"], errors="coerce")
            .fillna(0)
            .astype(int)
        )

        return out[REQUIRED_COLS]

    def append(self, df_new: pd.DataFrame, run_start, run_end) -> int:
        """
        Append new rows into one master parquet file.

        If fwi_timeseries.parquet does not exist, it is created.
        """
        if df_new is None or df_new.empty:
            return 0

        self.master_path.parent.mkdir(parents=True, exist_ok=True)
        df_new = self.normalize(df_new)
        self.write_incremental(df_new, run_start, run_end)
        self.write_manifest(df_new)

        if self.master_path.exists():
            df_old = pd.read_parquet(self.master_path)
            df = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df = df_new.copy()

        df = (
            df.drop_duplicates(subset=DEDUP_COLS, keep="last")
            .sort_values(["date", "estuary", "id_type", "id", "component"])
            .reset_index(drop=True)
        )

        df.to_parquet(self.master_path, index=False)

        return len(df_new)

    
