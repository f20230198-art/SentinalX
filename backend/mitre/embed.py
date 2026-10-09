"""Text embeddings with MiniLM (normalised, so cosine = dot product); model loads lazily."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import numpy as np

# Small, fast embedding model; every text becomes a list of 384 numbers
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384


# Load the model once and reuse it (loading is slow)
@lru_cache(maxsize=2)
def get_model(name: str = DEFAULT_MODEL):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


def encode(texts: list[str], model_name: str = DEFAULT_MODEL, batch_size: int = 32) -> np.ndarray:
    """Embed a list of texts. Returns (N, EMBED_DIM) float32, L2-normalised."""
    # Turn each text into a vector; normalised so similarity = simple dot product
    model = get_model(model_name)
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vecs.astype(np.float32, copy=False)


# Vector -> bytes, to store in SQLite
def to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32, copy=False).tobytes()


# Bytes from SQLite -> vector
def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def stack_blobs(blobs: Iterable[bytes]) -> np.ndarray:
    """Stack a sequence of (EMBED_DIM,) blobs into an (N, EMBED_DIM) matrix."""
    arrs = [from_blob(b) for b in blobs]
    if not arrs:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    return np.vstack(arrs)
