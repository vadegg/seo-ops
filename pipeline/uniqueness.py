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


_SLUG_LINE = re.compile(r"^slug:\s*[\"']?([^\"'\n]+)", re.MULTILINE)
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _slug_of(path: Path, text: str) -> str:
    head = text[:2000]
    m = _SLUG_LINE.search(head)
    if m:
        return m.group(1).strip()
    return _DATE_PREFIX.sub("", path.stem)


def published_corpus(topic_history_path: Path,
                     blog_content_dir: Path | None = None, *,
                     exclude_prefix: str = "") -> list[dict]:
    """Bodies of already-published posts, newest source first.

    The real corpus is the cloned Astro content collection the Publisher
    writes into (``runs/_blog_repo/src/content/blog``) — it is the only place
    the full post bodies exist. ``topic_history.json`` entries *may* carry a
    ``body`` (backfilled), and those are folded in as a fallback, but in
    production they never do: reading history alone yielded ``corpus_size: 0``
    on every run from 05.2026 to 08.2026, so the guard scored 0.0 and stayed
    silent while the same topic shipped eight times.

    ``exclude_prefix`` drops files whose name starts with it — used to keep
    the current run's own post (``<run-date>-<slug>.md``) out of the corpus on
    a re-run, where it would otherwise match itself at ~1.0.
    """
    out: list[dict] = []
    seen: set[str] = set()

    if blog_content_dir:
        d = Path(blog_content_dir)
        for f in sorted(d.glob("*.md")) if d.is_dir() else []:
            if exclude_prefix and f.name.startswith(exclude_prefix):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            body = _FRONTMATTER.sub("", text).strip()
            if not body:
                continue
            slug = _slug_of(f, text)
            seen.add(slug)
            out.append({"slug": slug, "topic": "", "body": body})

    try:
        import json
        data = json.loads(Path(topic_history_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for entry in data.get("published", []):
        body = (entry.get("body") or "").strip()
        slug = entry.get("slug", "")
        if body and slug not in seen:
            out.append({"slug": slug, "topic": entry.get("topic", ""),
                        "body": body})
    return out
