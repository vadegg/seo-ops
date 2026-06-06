"""Per-run artifact store + resume / idempotency.

State between steps lives on disk as files, never as in-memory objects.
Naming convention: numeric prefix = pipeline order, name = owning step.
A step whose output artifact already exists is skipped (resume).
"""

from __future__ import annotations

import json
from pathlib import Path

# Canonical artifact names (spec pipeline table).
RESEARCHER = "01-researcher.candidates.json"
STRATEGIST = "02-strategist.topic.json"
OUTLINER = "03-outliner.brief.json"
EVIDENCE = "03b-evidence.json"          # support artifact (Python-gathered)
WRITER = "04-writer.draft.md"
EDITOR_MD = "05-editor.edited.md"
EDITOR_CRITIQUE = "05-editor.critique.json"
UNIQUENESS = "05b-uniqueness.json"      # #37 near-duplicate score (advisory)
HUMANIZER = "05c-humanizer.md"          # de-AI'd body (#39); assembler input
ASSEMBLER = "06-assembler.post.md"
ASSEMBLER_META = "06-assembler.meta.json"   # slug info for the Publisher
PUBLISHER = "07-publisher.status.json"


class ArtifactStore:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.run_dir / name

    def exists(self, name: str) -> bool:
        p = self.path(name)
        return p.is_file() and p.stat().st_size > 0

    def write_json(self, name: str, obj) -> Path:
        p = self.path(name)
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                     encoding="utf-8")
        return p

    def read_json(self, name: str):
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write_text(self, name: str, text: str) -> Path:
        p = self.path(name)
        p.write_text(text, encoding="utf-8")
        return p

    def read_text(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")

    def is_published(self) -> bool:
        """True only if step 7 finished with status == 'published'.

        Makes a same-day re-run a no-op. A dry-run writes status
        'dry_run', so dry-runs remain repeatable.
        """
        if not self.exists(PUBLISHER):
            return False
        try:
            return self.read_json(PUBLISHER).get("status") == "published"
        except (ValueError, OSError):
            return False
