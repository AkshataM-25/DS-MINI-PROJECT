import hashlib
import unicodedata
from collections.abc import Iterable


SHINGLE_SIZE = 5


def normalize_text(text: str) -> list[str]:
    normalized = "".join(
        character
        for character in text.lower()
        if not unicodedata.category(character).startswith("P")
    )
    return normalized.split()


def create_shingles(text: str) -> set[str]:
    words = normalize_text(text)
    hashed_shingles = set()
    for index in range(max(0, len(words) - SHINGLE_SIZE + 1)):
        phrase = "\x1f".join(words[index:index + SHINGLE_SIZE]).encode("utf-8")
        hashed_shingles.add(hashlib.blake2b(phrase, digest_size=8).hexdigest())
    return hashed_shingles


def calculate_similarity(first: set[str], second: set[str]) -> float:
    if not first or not second:
        return 0.0
    union = first | second
    return len(first & second) / len(union)


def comparison_fingerprint(comparisons: Iterable[tuple[str, str, float]]) -> str:
    digest = hashlib.sha256()
    for document_a, document_b, similarity in comparisons:
        digest.update(
            f"{document_a}\0{document_b}\0{float(similarity):.6f}\n".encode("utf-8")
        )
    return digest.hexdigest()
