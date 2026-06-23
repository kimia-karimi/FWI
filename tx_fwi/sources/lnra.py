from __future__ import annotations
from pathlib import Path
import pandas as pd

from tx_fwi.transforms.temporal import normalize_daily_series

# ✅ your existing parser
from lktexana_append import process_new_pdfs_once


LK_TEXANA_FOLDER = Path(r"\\fileserver\CoastalScience\Data\External\Lake_Texana_Release")
LK_TEXANA_FILE = LK_TEXANA_FOLDER / "lktexanag"


def update_lake_texana_source():
    """
    Run your existing PDF processing BEFORE reading file.
    """
    print("[Lake Texana] Updating from PDFs...")
    process_new_pdfs_once(LK_TEXANA_FOLDER, LK_TEXANA_FILE)


def load_lake_texana_afday(start=None, end=None) -> pd.Series:

    # ✅ step 1: update file
    update_lake_texana_source()

    # ✅ step 2: read file
    df = pd.read_csv(
        LK_TEXANA_FILE,
        delim_whitespace=True,
        header=None,
        names=["year", "month", "day", "value"]
    )

    date = pd.to_datetime(
        dict(year=df.year, month=df.month, day=df.day),
        errors="coerce"
    )

    s = pd.Series(df["value"].values, index=date)
    s = s.dropna()

    return normalize_daily_series(s, start=start, end=end)
