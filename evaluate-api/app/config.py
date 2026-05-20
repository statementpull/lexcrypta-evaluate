import os

FIRM_TYPE = os.getenv("FIRM_TYPE", "ma")  # "ma" or "insolvency"

VERDICT_LANGUAGE = {
    "ma": {"pass": "PASS", "refer": "REFER"},
    "insolvency": {"pass": "PASS", "refer": "FAIL"},
}


def verdict(passed: bool) -> str:
    lang = VERDICT_LANGUAGE.get(FIRM_TYPE, VERDICT_LANGUAGE["ma"])
    return lang["pass"] if passed else lang["refer"]


LAS_THRESHOLDS = {
    "clear": 20,
    "elevated": 45,
    "refer": 100,
}

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://lexcrypta:lexcrypta_pw@localhost:5435/lexcrypta")
JWT_SECRET = os.getenv("JWT_SECRET_KEY", "offline-secret-key-change-in-production-32chars")
LICENSE_SECRET = os.getenv("LICENSE_SECRET", "dev-secret")
MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "100"))
MAX_CSV_MB = int(os.getenv("MAX_CSV_MB", "250"))
MAX_TOTAL_MB = int(os.getenv("MAX_TOTAL_MB", "500"))
