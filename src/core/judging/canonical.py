"""Canonical serialization for reproducible, signable results bundles.

Two portals given the same inputs must produce byte-identical output, so a
signature over these bytes means the same thing everywhere. Sorted keys, no
insignificant whitespace, UTF-8, stable float formatting.
"""
import hashlib
import json


def canonical_bytes(obj):
    """Deterministic JSON encoding of obj as UTF-8 bytes."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(obj):
    """Hex sha256 of the canonical encoding of obj."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
