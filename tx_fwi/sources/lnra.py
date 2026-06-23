# tx_fwi/sources/lake_texana.py

from __future__ import annotations
from pathlib import Path
import pandas as pd
import pdfplumber

# reuse your helpers
from lktexana_append import (
    _sanitize_text,
    _get_month_year_from_text_or_name,
    _parse_day_total_lines,
)

from tx_fwi.transforms.temporal import normalize_daily_series


LK_TEXANA_FOLDER = Path(
    r"\\fileserver\CoastalScience\Data\External\Lake_Texana_Release"
)


# ------------------------------------------------------------------
# ✅ CORE PIPELINE FUNCTION
# ------------------------------------------------------------------
def load_lake_texana_afday(start=None, end=None) -> pd.Series:
    """
    Pipeline-driven Lake Texana loader:

    - Parses all PDFs
    - Extracts daily flows
    - Returns complete time series (no intermediate file)
    """

    if not LK_TEXANA_FOLDER.exists():
        raise FileNotFoundError(LK_TEXANA_FOLDER)

    print("[Lake Texana] Parsing PDFs...")

    pdfs = sorted(LK_TEXANA_FOLDER.glob("*.pdf"))

    all_rows = []

    for p in pdfs:
        try:
            df_month = parse_month_pdf(p)
            if df_month is not None and not df_month.empty:
                all_rows.append(df_month)
        except Exception as e:
            print(f"[Lake Texana] Failed {p.name}: {e}")

    if not all_rows:
        return pd.Series(dtype="float64")

    df = pd.concat(all_rows, ignore_index=True)

    # ✅ build datetime index
    date = pd.to_datetime(
        dict(year=df["year"], month=df["month"], day=df["day"]),
        errors="coerce",
    )

    s = pd.Series(df["value"].values, index=date)

    s = s.dropna().sort_index()

    return normalize_daily_series(s, start=start, end=end)


# ------------------------------------------------------------------
# ✅ NEW PURE PARSER (replaces append_month_pdf write logic)
# ------------------------------------------------------------------
def parse_month_pdf(pdf_path: Path) -> pd.DataFrame:
    """
    Parse ONE Lake Texana PDF into DataFrame:

    columns:
        year, month, day, value
    """

    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    # ------------------------------------------------------
    # ✅ Extract all text
    # ------------------------------------------------------
    full_text = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += "\n" + text

    if not full_text.strip():
        return pd.DataFrame()

    # ------------------------------------------------------
    # ✅ Clean text
    # ------------------------------------------------------
    text = _sanitize_text(full_text)

    # ------------------------------------------------------
    # ✅ Detect month + year
    # ------------------------------------------------------
    month_name, year = _get_month_year_from_text_or_name(text, pdf_path)

    if not month_name or not year:
        raise ValueError(f"Could not extract month/year from {pdf_path.name}")

    month = {
        'January': 1, 'February': 2, 'March': 3, 'April': 4,
        'May': 5, 'June': 6, 'July': 7, 'August': 8,
        'September': 9, 'October': 10, 'November': 11, 'December': 12
    }[month_name]

    # ------------------------------------------------------
    # ✅ Parse day + values using your existing logic
    # ------------------------------------------------------
    parsed_rows = _parse_day_total_lines(text)

    if not parsed_rows:
        return pd.DataFrame()

    # parsed_rows = [(day, value, raw_line)]
    records = []

    for day, value, raw in parsed_rows:

        if day is None:
            continue

        try:
            d = int(day)
        except Exception:
            continue

        v = pd.to_numeric(value, errors="coerce")

        if pd.isna(v):
            continue

        records.append({
            "year": int(year),
            "month": int(month),
            "day": d,
            "value": float(v),
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    return df