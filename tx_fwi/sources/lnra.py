# tx_fwi/sources/lake_texana.py

from pathlib import Path
import pandas as pd

from lktexana_append import (
    _load_ledger,
    _save_ledger,
    _file_signature,
)

from tx_fwi.transforms.temporal import normalize_daily_series


LK_TEXANA_FOLDER = Path(
    r"\\fileserver\CoastalScience\Data\External\Lake_Texana_Release"
)


def load_lake_texana_afday(start=None, end=None) -> pd.Series:
    """
    Returns ONLY new Lake Texana data (not full history).
    """

    ledger = _load_ledger()

    pdfs = sorted(LK_TEXANA_FOLDER.glob("*.pdf"))

    all_new_rows = []

    for p in pdfs:

        sig = _file_signature(p)
        sig_key = f"{sig['name']}|{sig['size']}|{sig['mtime']}"

        if ledger.get(sig_key):
            continue  # skip old PDFs

        print(f"[Lake Texana] Processing NEW PDF: {p.name}")

        try:
            df_month = parse_month_pdf(p)

            if df_month is not None and not df_month.empty:
                all_new_rows.append(df_month)

            ledger[sig_key] = True

        except Exception as e:
            print(f"[Lake Texana] Failed {p.name}: {e}")

    _save_ledger(ledger)

    if not all_new_rows:
        return pd.Series(dtype="float64")

    df = pd.concat(all_new_rows, ignore_index=True)

    date = pd.to_datetime(
        dict(year=df["year"], month=df["month"], day=df["day"]),
        errors="coerce"
    )

    s = pd.Series(df["value"].values, index=date)

    return normalize_daily_series(s, start=start, end=end)