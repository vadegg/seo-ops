from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore


def test_resume_skip(tmp_path):
    s = ArtifactStore(tmp_path / "2026-05-19")
    assert not s.exists(A.RESEARCHER)
    s.write_json(A.RESEARCHER, {"candidates": []})
    assert s.exists(A.RESEARCHER)
    assert s.read_json(A.RESEARCHER) == {"candidates": []}


def test_is_published_true_only_on_published(tmp_path):
    s = ArtifactStore(tmp_path / "d")
    assert s.is_published() is False
    s.write_json(A.PUBLISHER, {"status": "dry_run"})
    assert s.is_published() is False
    s.write_json(A.PUBLISHER, {"status": "published"})
    assert s.is_published() is True
