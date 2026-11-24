"""Dependency-free lexical embedding: hashed character-trigram + word term frequencies.

This is NOT a semantic (neural) embedding. It is deterministic, needs no model download
and no API key, and is good enough to find nodes that share vocabulary with a question.
The README states this limitation explicitly.
"""
from __future__ import annotations

import math
import re
import zlib

DIM = 256
_WORD = re.compile(r"[a-z0-9]+")


def _features(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    feats = [f"w:{w}" for w in words]
    for w in words:
        padded = f"#{w}#"
        feats += [f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2)]
    return feats


def embed(text: str, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    for f in _features(text):
        h = zlib.crc32(f.encode("utf-8"))
        vec[h % dim] += 1.0 if (h >> 16) & 1 else -1.0  # signed hashing trick
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]
