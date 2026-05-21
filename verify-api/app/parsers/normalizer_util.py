import re

_LEGAL = re.compile(r'\b(PTY|LTD|INC|LLC|CORP|CO|PLC)\b', re.IGNORECASE)
_NOISE = re.compile(r'[^A-Z0-9\s]')
_WS = re.compile(r'\s+')


def normalize_description(desc: str) -> str:
    if not desc:
        return ""
    d = _LEGAL.sub('', desc.upper())
    d = _NOISE.sub(' ', d)
    return _WS.sub(' ', d).strip()
