# tx_fwi/sources/lake_texana.py

from pathlib import Path
import pandas as pd
import pdfplumber
import re
import json
import time

from tx_fwi.transforms.temporal import normalize_daily_series

LK_TEXANA_FOLDER = Path( r"T:\\CoastalScience\\Data\\External\\Lake_Texana_Release")
MONTH_MAP = {
    'January': 1,'February': 2,'March': 3,'April': 4,'May': 5,'June': 6,
    'July': 7,'August': 8,'September': 9,'October': 10,'November': 11,'December': 12,
}
MONTH_WORDS = r'(Jan(?:u(?:a(?:r(?:y)?)?)?)?|Feb(?:r(?:u(?:a(?:r(?:y)?)?)?)?)?|Mar(?:c(?:h)?)?|Apr(?:i(?:l)?)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:u(?:s(?:t)?)?)?|Sep(?:t(?:e(?:m(?:b(?:e(?:r)?)?)?)?)?)?|Oct(?:o(?:b(?:e(?:r)?)?)?)?|Nov(?:e(?:m(?:b(?:e(?:r)?)?)?)?)?|Dec(?:e(?:m(?:b(?:e(?:r)?)?)?)?)?)'
YEAR_RE    = r'([12][0-9]{3})'
DAY_START  = re.compile(r'^(?:\D*?)(\d{1,2})\b')                  # first 1..31 near start
DAY_AFTER_MONTH = re.compile(rf'{MONTH_WORDS}\D*(\d{{1,2}})\b', re.I)
NUMBER_TOKEN = re.compile(r'[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?')

# Characters in the Private Use Area (PUA) often used by UI fonts (e.g., \uE116, \uF166)
PUA_RANGE = re.compile(r'[\uE000-\uF8FF]')
# Also remove stray non-breaking spaces, bullets, etc.
WEIRD_CHARS = re.compile(r'[\u00A0\u2022\u200B\u2009\u200A\u2006\u2003]')
LEDGER_PATH = LK_TEXANA_FOLDER / "processed_pdfs.json"  # keeps track of already processed files

def _load_ledger():
    if LEDGER_PATH.exists():
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_ledger(ledger):
    try:
        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
    except Exception as e:
        print(f"[WARN] Could not save ledger: {e}")

def _file_signature(p: Path):
    """A simple signature to avoid reprocessing the same PDF."""
    try:
        stat = p.stat()
        # Use name + size + modified time as the signature
        return {"name": p.name, "size": stat.st_size, "mtime": int(stat.st_mtime)}
    except Exception:
        return {"name": p.name, "size": None, "mtime": None}


def _sanitize_text(text: str) -> str:
    """Strip private-use glyphs and common non-printing symbols that pollute header lines."""
    if not text:
        return ''
    s = PUA_RANGE.sub(' ', text)       # remove PUA glyphs
    s = WEIRD_CHARS.sub(' ', s)        # remove non-breaking / bullets / narrow spaces
    # Collapse multiple spaces
    s = re.sub(r'[ \t]+', ' ', s)
    # Normalize hyphenation in broken month words (optional, helps matching)
    s = s.replace('Sept emb er', 'September').replace('Octo ber', 'October')
    return s
def _normalize_month_word(raw: str):
    """Normalize broken/spaced month token into canonical English full month name."""
    s = re.sub(r'\s+', '', raw or '', flags=re.I).lower()
    for full in MONTH_MAP.keys():
        if s.startswith(full[:3].lower()):
            return full
    if s.startswith('sep'):
        return 'September'
    return None

def _get_month_year_from_text_or_name(text: str, file_path: Path):
    """Extract (month_name, year) robustly from text; fallback to filename if needed."""
    # 1) Try page text
    m = re.search(rf'{MONTH_WORDS}[^\dA-Za-z]*{YEAR_RE}', text or '', re.I)
    if m:
        raw_month = m.group(1)
        year = int(m.group(m.lastindex))
        month_name = _normalize_month_word(raw_month)
        return month_name, year

    # 2) Fallback: filename (e.g., "SEP2025 Lake Texana Release Data.pdf")
    name = file_path.name
    year_m = re.search(YEAR_RE, name)
    year = int(year_m.group(1)) if year_m else None

    month_name = None
    for full in MONTH_MAP.keys():
        if re.search(full[:3], name, re.I):
            month_name = full
            break

    return month_name, year

def _score_daily_line(line: str):
    """
    Score a line to determine if it looks like a true daily row:
    - Base: number of numeric tokens (daily lines usually have >= 6)
    - Bonus: +3 if line does NOT contain month words (reduces header-blended lines)
    """
    nums = NUMBER_TOKEN.findall(line)
    score = len(nums)
    if not re.search(MONTH_WORDS, line, re.I):
        score += 3
    return score, nums
def _parse_day_total_lines(text: str):
    """
    Parse lines of text:
    - Detect day either at line start or after month word (handles '... Sept 1 42.18 ...')
    - Use scoring to pick the best line per day (dedupe day #1).
    - Require at least 6 numeric tokens to consider a line a true daily row.
    Returns list of (day:int, total:float|None, raw_line:str)
    """
    parsed_per_day = {}  # day -> (score, total, raw_line)
    lines = [ln.strip() for ln in (text or '').split('\n') if ln.strip()]

    for ln in lines:
        # Try day after month
        day = None
        m2 = DAY_AFTER_MONTH.search(ln)
        if m2:
            try:
                day = int(m2.group(m2.lastindex))
            except Exception:
                day = None
        # Fallback: day near start
        if day is None:
            m1 = DAY_START.match(ln)
            if m1:
                try:
                    day = int(m1.group(1))
                except Exception:
                    day = None

        if day is None or not (1 <= day <= 31):
            continue

        # Score line and get numeric tokens
        score, nums = _score_daily_line(ln)
        # Require at least 6 numeric tokens (Elevation, Content, Spillway, ROW, Seepage, Total)
        if len(nums) < 6:
            continue

        total_str = nums[-1]
        try:
            total = float(total_str.replace(',', ''))
        except Exception:
            total = None

        # Keep the best-scoring line for each day
        keep = True
        if day in parsed_per_day:
            prev_score = parsed_per_day[day][0]
            if score <= prev_score:
                keep = False
        if keep:
            parsed_per_day[day] = (score, total, ln)

    # Return sorted by day
    
    out = [(d, parsed_per_day[d][1], parsed_per_day[d][2]) for d in sorted(parsed_per_day.keys())]
    return out
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
    full_text = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += "\n" + text

    if not full_text.strip():
        return pd.DataFrame()

    # ------------------------------------------------------
    #  Clean text
    # ------------------------------------------------------
    text = _sanitize_text(full_text)
    month_name, year = _get_month_year_from_text_or_name(text, pdf_path)

    if not month_name or not year:
        raise ValueError(f"Could not extract month/year from {pdf_path.name}")

    month = {
        'January': 1, 'February': 2, 'March': 3, 'April': 4,
        'May': 5, 'June': 6, 'July': 7, 'August': 8,
        'September': 9, 'October': 10, 'November': 11, 'December': 12
    }[month_name]
    
    # parsed_rows = [(day, value, raw_line)]
    parsed_rows = _parse_day_total_lines(text)

    if not parsed_rows:
        return pd.DataFrame()

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
    df = df.dropna(subset=["value"])

    return df





    
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
            else:
                print(f"[Lake Texana] No valid data in {p.name}")


        except Exception as e:
            print(f"[Lake Texana] Failed {p.name}: {e}")

    _save_ledger(ledger)

    if not all_new_rows:
        return pd.Series(dtype="float64")

    df = pd.concat(all_new_rows, ignore_index=True)
    df = df.drop_duplicates(subset=["year", "month", "day"], keep="last")
    date = pd.to_datetime(
        dict(year=df["year"], month=df["month"], day=df["day"]),
        errors="coerce"
    )

    s = pd.Series(df["value"].values, index=date)
    s = s.dropna()
    s = s.sort_index()
    s.name = "lake_texana"

    return normalize_daily_series(s, start=start, end=end)



s = load_lake_texana_afday("2023-01-01", "2025-01-01")
print(len(s), s.head(), s.tail())