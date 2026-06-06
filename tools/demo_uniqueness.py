"""Demo the #37 uniqueness guard without running the pipeline.

Computes the internal MinHash similarity of a post body against a tiny
built-in fake corpus and shows the WARN line that the `uniqueness` step
would emit for an above-threshold (near-duplicate) match.

Usage:
    python tools/demo_uniqueness.py                 # built-in near-dup sample
    python tools/demo_uniqueness.py path/to/post.md # score your own body
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.uniqueness import DEFAULT_THRESHOLD, best_match  # noqa: E402

# A tiny built-in "already published" corpus.
CORPUS = [
    {"slug": "card-sorting-guide",
     "body": ("Card sorting reveals how users group concepts in their own "
              "mental model. Run an open sort first to surface vocabulary, "
              "then a closed sort to validate an information architecture.")},
    {"slug": "switching-costs-b2b",
     "body": ("Switching costs decide whether a B2B buyer ever migrates. Map "
              "the integrations, the retraining, and the political capital a "
              "champion must spend internally before a competing vendor is "
              "even evaluated. Most teams underestimate the retraining line "
              "item by an order of magnitude, and that is where the deal "
              "quietly dies.")},
]

# Default sample body: a near-paraphrase of "switching-costs-b2b".
SAMPLE = (
    "Switching costs decide whether a B2B buyer ever migrates. Map the "
    "integrations, the retraining, and the political capital a champion must "
    "spend internally before a competing vendor is even evaluated. Most teams "
    "underestimate the retraining line item by a wide margin, and that is "
    "exactly where the deal quietly dies in the end."
)


def main() -> int:
    if len(sys.argv) > 1:
        body = Path(sys.argv[1]).read_text(encoding="utf-8")
        label = sys.argv[1]
    else:
        body = SAMPLE
        label = "(built-in near-duplicate sample)"

    score, match = best_match(body, CORPUS)
    threshold = DEFAULT_THRESHOLD

    print(f"post body:   {label}")
    print(f"corpus size: {len(CORPUS)} published post(s)")
    print(f"threshold:   {threshold}")
    print(f"max similarity: {score:.4f}"
          + (f"  (closest: {match['slug']})" if match else ""))
    print()
    if score >= threshold:
        print(f"WARNING | uniqueness | body is highly similar "
              f"({score:.2f} >= {threshold:.2f}) to published post "
              f"'{match['slug']}' — review for paraphrase/self-repetition")
        print("\n(advisory only — the pipeline still publishes the post.)")
    else:
        print(f"INFO | uniqueness | max similarity {score:.2f} "
              f"(< {threshold:.2f}) — ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
