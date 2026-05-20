"""Signed signal bundle updater.

Runs at container startup. Looks for a signed update bundle in /updates/.
Verifies signature, extracts signal files, compiles to .pyc, removes .py source.
Safe to run on every startup — no-ops if no bundle is present.
"""
import compileall
import hashlib
import hmac
import json
import logging
import os
import shutil
import tarfile
from pathlib import Path

log = logging.getLogger("evaluate.updater")

_UPDATE_SECRET = "$$Lexi2026-&Update-&Bundle-&Authenticate-&Signals$$"

UPDATES_DIR = Path("/updates")
SIGNALS_DIR = Path("/app/app/intelligence/signals")
VERSION_FILE = Path("/app/app/version.txt")


def _current_version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except FileNotFoundError:
        return "0.0"


def _write_version(version: str):
    VERSION_FILE.write_text(version.strip())


def _hmac_of_file(path: Path) -> str:
    h = hmac.new(_UPDATE_SECRET.encode(), digestmod=hashlib.sha256)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_bundle(bundle_path: Path, sig_path: Path) -> bool:
    if not sig_path.exists():
        log.error("UPDATE REJECTED: signature file not found: %s", sig_path)
        return False
    expected = sig_path.read_text().strip()
    actual = _hmac_of_file(bundle_path)
    if not hmac.compare_digest(expected, actual):
        log.error("UPDATE REJECTED: signature mismatch — bundle may be tampered")
        return False
    return True


def _apply_bundle(bundle_path: Path) -> str | None:
    tmp = UPDATES_DIR / "_extract"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            # Safety check — reject any paths outside the extract dir
            for member in tar.getmembers():
                member_path = (tmp / member.name).resolve()
                if not str(member_path).startswith(str(tmp.resolve())):
                    log.error("UPDATE REJECTED: unsafe path in bundle: %s", member.name)
                    return None
            # PEP 706 'data' filter: strict — rejects absolute paths, traversal,
            # device files, special permissions, and symlinks escaping the destination.
            tar.extractall(tmp, filter="data")

        # Read manifest
        manifest_path = tmp / "manifest.json"
        if not manifest_path.exists():
            log.error("UPDATE REJECTED: manifest.json missing from bundle")
            return None
        manifest = json.loads(manifest_path.read_text())
        new_version = manifest.get("version", "unknown")
        current = _current_version()

        if new_version <= current:
            log.info("UPDATE SKIPPED: bundle version %s <= current %s", new_version, current)
            return None

        # Copy signal files
        signals_src = tmp / "signals"
        if not signals_src.exists():
            log.error("UPDATE REJECTED: no signals/ directory in bundle")
            return None

        copied = 0
        for py_file in signals_src.glob("*.py"):
            dest = SIGNALS_DIR / py_file.name
            shutil.copy2(py_file, dest)
            copied += 1

        if copied == 0:
            log.warning("UPDATE: no signal files found in bundle")
            return None

        # Compile to .pyc and remove .py source
        compileall.compile_dir(str(SIGNALS_DIR), force=True, quiet=True)
        for py_file in SIGNALS_DIR.glob("*.py"):
            py_file.unlink()

        _write_version(new_version)
        changes = manifest.get("changes", [])
        log.info(
            "UPDATE APPLIED: v%s → v%s — %d signal(s) updated. Changes: %s",
            current, new_version, copied, "; ".join(changes)
        )
        return new_version

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_update_check():
    if not UPDATES_DIR.exists():
        return

    bundles = sorted(UPDATES_DIR.glob("evaluate-update-*.tar.gz"))
    if not bundles:
        return

    # Apply bundles in version order
    for bundle_path in bundles:
        sig_path = bundle_path.with_suffix(".tar.gz.sig")

        log.info("UPDATE: found bundle %s", bundle_path.name)

        if not _verify_bundle(bundle_path, sig_path):
            # Move rejected bundle to /updates/rejected/ for audit
            rejected_dir = UPDATES_DIR / "rejected"
            rejected_dir.mkdir(exist_ok=True)
            shutil.move(str(bundle_path), rejected_dir / bundle_path.name)
            if sig_path.exists():
                shutil.move(str(sig_path), rejected_dir / sig_path.name)
            continue

        applied = _apply_bundle(bundle_path)
        if applied:
            # Archive applied bundle for audit trail
            applied_dir = UPDATES_DIR / "applied"
            applied_dir.mkdir(exist_ok=True)
            shutil.move(str(bundle_path), applied_dir / bundle_path.name)
            if sig_path.exists():
                shutil.move(str(sig_path), applied_dir / sig_path.name)
        else:
            # Bundle was valid but not newer — remove cleanly
            bundle_path.unlink(missing_ok=True)
            sig_path.unlink(missing_ok=True)
