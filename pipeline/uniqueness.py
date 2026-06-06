"""#37 Deterministic near-duplicate detection (no external API).

The pipeline never checked generated text for uniqueness, so a draft could
silently near-paraphrase a web-research source or repeat an earlier post as
the corpus grows. This module adds a *local* guard that runs between the
Editor and the Assembler.

Algorithm — word-level k-shingling + MinHash:
  * normalize the body to a lowercased word stream (markdown/punctuation
    stripped),
  * build the set of overlapping ``k``-word shingles,
  * hash each shingle with a family of ``num_perm`` salted hashes and keep
    the per-permutation minimum (the MinHash signature),
  * the fraction of signature slots that agree between two documents is an
    unbiased estimate of their Jaccard similarity.

Why MinHash over raw Jaccard? It is O(num_perm) to compare two signatures
regardless of document length, so checking a new post against a growing
corpus stays cheap, and it needs nothing beyond the standard library
(``hashlib``). Why shingles over a bag of words? Word order matters for
paraphrase detection — "users decide" vs "decide users" share every word
but no 2-shingle, so shingling resists trivial reordering.

Default threshold ``0.55``: empirically, independently written posts on
the same topic land well below ~0.3 even when they share vocabulary
(distinct shingles dominate), whereas a true paraphrase or a re-run of the
same brief sits comfortably above ~0.6. 0.55 leaves margin on both sides so
the WARN fires on real overlap, not on topical neighbours. The check is
advisory — it never blocks publication; it only raises a WARN that feeds the
run telemetry/digest (and could trigger a forced Editor rewrite later).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Tunables (kept here so config.py can mirror them; the step reads the
# threshold from config and passes the rest through).
DEFAULT_SHINGLE_K = 5
DEFAULT_NUM_PERM = 128
DEFAULT_THRESHOLD = 0.55

_FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_WORD = re.compile(r"[a-z0-9]+")


def _normalize_words(text: str) -> list[str]:
    """Lowercase word stream with frontmatter/markup/punctuation removed."""
    text = _FRONTMATTER.sub("", text or "")
    return _WORD.findall(text.lower())


def shingles(text: str, k: int = DEFAULT_SHINGLE_K) -> set[str]:
    """Set of overlapping ``k``-word shingles. Short texts fall back to a
    single shingle of all their words so similarity is still defined."""
    words = _normalize_words(text)
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _hash(salt: int, token: str) -> int:
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8,
                        salt=salt.to_bytes(8, "little"))
    return int.from_bytes(h.digest(), "little")


def minhash(text: str, *, k: int = DEFAULT_SHINGLE_K,
            num_perm: int = DEFAULT_NUM_PERM) -> list[int]:
    """MinHash signature: per-permutation minimum over the shingle set."""
    sh = shingles(text, k)
    if not sh:
        return [0] * num_perm
    return [min(_hash(p, s) for s in sh) for p in range(num_perm)]


def _signature_jaccard(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    agree = sum(1 for x, y in zip(a, b) if x == y)
    return agree / len(a)


def estimate_similarity(text_a: str, text_b: str, *,
                        k: int = DEFAULT_SHINGLE_K,
                        num_perm: int = DEFAULT_NUM_PERM) -> float:
    """Estimated Jaccard similarity of two texts in [0, 1]."""
    return _signature_jaccard(
        minhash(text_a, k=k, num_perm=num_perm),
        minhash(text_b, k=k, num_perm=num_perm))


def best_match(body: str, corpus: list[dict], *,
               k: int = DEFAULT_SHINGLE_K,
               num_perm: int = DEFAULT_NUM_PERM) -> tuple[float, dict | None]:
    """Return (max_similarity, closest_entry) of ``body`` against a corpus of
    ``{"slug"/"body"/...}`` dicts. Empty corpus -> (0.0, None)."""
    sig = minhash(body, k=k, num_perm=num_perm)
    best_score = 0.0
    best_entry: dict | None = None
    for entry in corpus:
        score = _signature_jaccard(
            sig, minhash(entry.get("body", ""), k=k, num_perm=num_perm))
        if score >= best_score:
            best_score, best_entry = score, entry
    return (best_score, best_entry) if best_entry is not None else (0.0, None)


def published_corpus(topic_history_path: Path) -> list[dict]:
    """Published posts that carry a body, read from topic_history.json.

    topic_history is the committed dedupe guard; entries may carry a ``body``
    (e.g. backfilled from the live blog content collection). Entries without
    a body are skipped — there is nothing to compare against. The live Astro
    content collection is the richer source but is not present in this repo,
    so the corpus is whatever published bodies are locally available; an
    empty corpus simply yields a 0.0 score (no false positives)."""
    try:
        import json
        data = json.loads(Path(topic_history_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for entry in data.get("published", []):
        body = (entry.get("body") or "").strip()
        if body:
            out.append({"slug": entry.get("slug", ""),
                        "topic": entry.get("topic", ""),
                        "body": body})
    return out
