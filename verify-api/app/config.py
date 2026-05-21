import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://verify:verify_pw@localhost:5436/verify")
DEMO_KEY = os.getenv("DEMO_KEY", "LEXV-DEMO-2026-BARTILOTTA")
LICENSE_SECRET = os.getenv("LICENSE_SECRET", "dev-secret")
MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "50"))
MAX_CSV_MB = int(os.getenv("MAX_CSV_MB", "100"))
