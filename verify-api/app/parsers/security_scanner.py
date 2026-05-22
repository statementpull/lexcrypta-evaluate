import re

# Malicious PDF patterns — each is a bytes pattern found in weaponised PDFs.
# Sources: Adobe PSIRT advisories, VirusTotal PDF threat intelligence reports.
_MALICIOUS_PATTERNS: list[tuple[bytes, str]] = [
    # JavaScript execution
    (b"/JS ",          "Embedded JavaScript (/JS)"),
    (b"/JavaScript",   "Embedded JavaScript (/JavaScript)"),
    (b"/OpenAction",   "Auto-execute action on open (/OpenAction)"),
    (b"/AA ",          "Additional action trigger (/AA)"),
    # External process launch
    (b"/Launch",       "External application launch (/Launch)"),
    (b"/SubmitForm",   "Form data exfiltration (/SubmitForm)"),
    (b"/ImportData",   "External data import (/ImportData)"),
    # Embedded executables / content
    (b"/EmbeddedFile", "Embedded file attachment (/EmbeddedFile)"),
    (b"/RichMedia",    "Embedded rich media (Flash/multimedia) (/RichMedia)"),
    # Windows PE header (executable embedded inside a PDF stream)
    (b"MZ\x90\x00",   "Embedded Windows executable (PE header)"),
    (b"\x4d\x5a\x90\x00", "Embedded Windows executable (PE header)"),
]

# Maximum bytes to scan — large PDFs may have many legitimate matches for
# common keywords; limit to first 512 KB which covers the cross-reference table
# and all object headers where malicious code is injected.
_SCAN_LIMIT = 512 * 1024


def scan_pdf_bytes(content: bytes, filename: str = "") -> dict | None:
    """
    Scan raw PDF bytes for known malicious patterns.

    Returns a signal dict (severity RED) if threats are found, or None if clean.
    Checks only the first 512 KB — malicious code is always in headers/objects,
    never buried deep in uncompressed image data.
    """
    if not content:
        return None

    sample = content[:_SCAN_LIMIT]
    found = []

    for pattern, description in _MALICIOUS_PATTERNS:
        if pattern in sample:
            found.append(description)

    if not found:
        return None

    label = filename or "uploaded file"
    return {
        "transaction_date": "",
        "merchant": f"SECURITY THREAT DETECTED — {label}",
        "amount": 0.0,
        "signal_type": "security_threat",
        "severity": "red",
        "meta": {
            "filename": label,
            "threats_found": found,
            "note": (
                "This file contains patterns associated with malicious PDFs. "
                "Do not open outside a secure environment. "
                f"Detected: {'; '.join(found)}"
            ),
        },
        "raw": {},
    }


def validate_pdf_header(content: bytes, filename: str = "") -> str | None:
    """
    Verify the file is a real PDF by checking its magic bytes.

    Returns an error message string if invalid, or None if the header is valid.
    The file_detector already gates on %PDF, but this is a second line of
    defence for files that pass file_detector but arrive with a renamed extension.
    """
    if len(content) < 5:
        return f"{filename or 'File'}: file is too small to be a valid PDF."
    if not content.startswith(b"%PDF-"):
        return (
            f"{filename or 'File'}: file does not start with %PDF-. "
            "The file may have been renamed or tampered with."
        )
    return None
