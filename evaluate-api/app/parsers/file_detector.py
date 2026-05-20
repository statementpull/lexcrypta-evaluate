import io
from typing import BinaryIO

MYOB_GL_HEADERS = {"account", "job", "memo", "debit", "credit"}
MYOB_PL_HEADERS = {"account", "ytd", "this month"}
BANK_CSV_HEADERS = {"date", "description", "debit", "credit", "balance"}


def detect_file_type(file: BinaryIO, filename: str) -> str:
    header = file.read(8)
    file.seek(0)

    if header[:4] == b"%PDF":
        return "bank_pdf"

    fname = filename.lower()
    if fname.endswith(".csv"):
        text = file.read(512).decode("utf-8", errors="replace").lower()
        file.seek(0)
        first_line = text.split("\n")[0]
        cols = {c.strip().strip('"') for c in first_line.split(",")}
        if MYOB_GL_HEADERS.issubset(cols):
            return "myob_gl"
        if MYOB_PL_HEADERS.issubset(cols):
            return "myob_pl"
        if BANK_CSV_HEADERS.issubset(cols):
            return "bank_csv"
        # Fallback: if it has date + description columns, treat as bank CSV
        if "date" in cols and ("description" in cols or "narrative" in cols or "memo" in cols):
            return "bank_csv"
        return "csv_unknown"

    return "unknown"
