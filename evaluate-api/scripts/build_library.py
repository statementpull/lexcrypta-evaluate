"""
Run once after Carmen exports the 47 library tables from Supabase as CSVs.
Usage: python scripts/build_library.py --csv-dir /path/to/csvs --secret "LICENSE_SECRET_VALUE"

Produces: app/data/lexi_libraries.db.enc
"""
import argparse
import base64
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
from cryptography.fernet import Fernet


def derive_key(secret: str) -> bytes:
    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def build(csv_dir: str, secret: str, out_path: str):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    csv_files = list(Path(csv_dir).glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        return
    for csv_file in csv_files:
        table_name = csv_file.stem
        df = pd.read_csv(csv_file)
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        print(f"  Loaded {table_name}: {len(df)} rows")
    conn.close()
    raw = open(tmp.name, "rb").read()
    os.unlink(tmp.name)
    key = derive_key(secret)
    encrypted = Fernet(key).encrypt(raw)
    open(out_path, "wb").write(encrypted)
    print(f"\nBuilt: {out_path} ({len(encrypted):,} bytes)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv-dir", required=True)
    p.add_argument("--secret", required=True)
    p.add_argument("--out", default="app/data/lexi_libraries.db.enc")
    args = p.parse_args()
    build(args.csv_dir, args.secret, args.out)
