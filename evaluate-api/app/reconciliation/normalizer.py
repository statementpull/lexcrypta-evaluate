from app.parsers.normalizer_util import normalize_description
from difflib import SequenceMatcher


def description_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()
