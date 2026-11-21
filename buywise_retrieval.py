"""Retrieval shared by the shop and the blinded, design-level experiment."""

import math
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from nltk.stem import PorterStemmer

MODEL = "openai/clip-vit-base-patch32"
REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
ONLINE_REVISION = "6b7c523703ae26e39be4b03774966f8571c7f523"
stemmer = PorterStemmer()


@lru_cache(maxsize=4096)
def terms(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[^\W_]+", text)
    return frozenset(
        stemmer.stem(w) if w.isascii() and w.isalpha() else w for w in words
    )


def dimensions(title):
    match = re.search(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*cm\b", title or "", re.I
    )
    return f"{match[1]} × {match[2]} cm" if match else None


def threshold():
    value = float(os.getenv("BUYWISE_SIMILARITY", "0.90"))
    if not -1 <= value <= 1:
        raise ValueError("BUYWISE_SIMILARITY must be between -1 and 1.")
    return value


@dataclass(frozen=True)
class Rug:
    id: int
    key: str
    design: str
    title: str
    image: str
    vector: tuple


def catalog(conn, *, online=False):
    rows = conn.execute(
        """
        SELECT p.id, p.model_key, COALESCE(NULLIF(p.design_key,''),p.image_url),
               p.title,p.image_url,e.embedding
        FROM products p JOIN product_image_embeddings e ON e.product_id=p.id
        WHERE p.category='rugs' AND e.model_name=%s AND e.model_revision=ANY(%s) AND e.source_url=p.image_url
        ORDER BY p.model_key
    """,
        (MODEL, [REVISION, ONLINE_REVISION] if online else [REVISION]),
    ).fetchall()
    result = {}
    for product_id, key, design, title, image, vector in rows:
        if len(vector) != 512 or any(not math.isfinite(x) for x in vector):
            continue
        norm = math.hypot(*vector)
        if norm:
            result[product_id] = Rug(
                product_id, key, design, title, image, tuple(x / norm for x in vector)
            )
    return result


def ranked(items, reference, method):
    """Return one representative per design, choosing its highest-scoring variant."""
    if method not in {"image", "title"}:
        raise ValueError("Unknown retrieval method")
    query = terms(reference.title)
    scores = []
    for rug in items.values():
        if rug.design == reference.design or rug.image == reference.image:
            continue
        if method == "image":
            score = max(
                -1.0,
                min(
                    1.0,
                    math.fsum(
                        a * b for a, b in zip(reference.vector, rug.vector, strict=True)
                    ),
                ),
            )
        else:
            score = len(query & terms(rug.title))
            if not score:
                continue
        scores.append((rug, score))
    seen = set()
    seen_images = set()
    for rug, score in sorted(scores, key=lambda pair: (-pair[1], pair[0].key)):
        if rug.design not in seen and rug.image not in seen_images:
            yield rug, score
            seen.add(rug.design)
            seen_images.add(rug.image)
