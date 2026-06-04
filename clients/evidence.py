"""Evidence corpus retrieval — pure-Python BM25 over *.md / *.txt.

Returns top passages plus the source filename so the Writer can ground
claims and the Editor can verify support. No external dependency.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _split_passages(text: str) -> list[str]:
    """Split on blank lines; merge tiny fragments up to a min length."""
    raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    passages, buf = [], ""
    for p in raw:
        buf = f"{buf}\n\n{p}".strip() if buf else p
        if len(buf) >= 240:
            passages.append(buf)
            buf = ""
    if buf:
        passages.append(buf)
    return passages


class EvidenceClient:
    def __init__(self, evidence_dir: Path, logger=None,
                 k1: float = 1.5, b: float = 0.75):
        self._dir = Path(evidence_dir)
        self._log = logger
        self._k1 = k1
        self._b = b
        self._passages: list[dict] = []
        self._df: Counter = Counter()
        self._avg_len = 0.0
        self._indexed = False

    def _index(self) -> None:
        if self._indexed:
            return
        files = []
        if self._dir.is_dir():
            files = sorted(
                p for p in self._dir.rglob("*")
                if p.suffix.lower() in {".md", ".txt"} and p.is_file()
            )
        total_len = 0
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for passage in _split_passages(text):
                toks = _tokenize(passage)
                if not toks:
                    continue
                tf = Counter(toks)
                self._passages.append({
                    "file": fp.name,
                    "path": str(fp),
                    "text": passage,
                    "tf": tf,
                    "len": len(toks),
                })
                total_len += len(toks)
                for term in tf:
                    self._df[term] += 1
        n = len(self._passages)
        self._avg_len = (total_len / n) if n else 0.0
        self._indexed = True
        if self._log:
            self._log.info("evidence: indexed %d passages from %d files",
                            n, len(files))

    def _idf(self, term: str) -> float:
        n = len(self._passages)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int = 8) -> list[dict]:
        """Return up to ``k`` passages: {file, path, text, score}."""
        self._index()
        if not self._passages:
            return []
        q_terms = _tokenize(query)
        scored = []
        for p in self._passages:
            score = 0.0
            for term in q_terms:
                tf = p["tf"].get(term, 0)
                if tf == 0:
                    continue
                denom = tf + self._k1 * (
                    1 - self._b + self._b * p["len"] / (self._avg_len or 1)
                )
                score += self._idf(term) * (tf * (self._k1 + 1)) / denom
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [
            {"file": p["file"], "path": p["path"],
             "text": p["text"], "score": round(s, 4)}
            for s, p in scored[:k]
        ]
