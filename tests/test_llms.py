"""llms.txt generation (#17)."""

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.llms import render
from pipeline.orchestrator import run_selected_steps
from pipeline.steps import STEP_NAMES


def test_render_lists_articles_newest_first():
    il = {"posts": [
        {"title": "Old", "url": "/old", "date": "2026-01-01"},
        {"title": "New", "url": "/new", "date": "2026-06-01"},
    ]}
    out = render(site_name="Glasgow Research", author_name="Vadim Glazkov",
                 base_url="https://blog.glasgow.works/blog", internal_links=il)
    assert "# Glasgow Research" in out
    assert "Author: Vadim Glazkov" in out
    assert out.index("[New](/new)") < out.index("[Old](/old)")


def test_render_includes_new_post_at_top():
    out = render(site_name="GR", author_name="V",
                 base_url="https://b/blog", internal_links={"posts": []},
                 new_post={"title": "Fresh", "url": "/fresh",
                           "date": "2026-06-04"})
    assert "[Fresh](/fresh)" in out


def test_publish_writes_llms_txt(project, deps_factory):
    deps = deps_factory()
    rc = run_selected_steps(project, run_date="2026-05-19",
                            step_names=STEP_NAMES, dry_run=False, deps=deps)
    assert rc == 0
    assert "public/llms.txt" in deps.git.written
    content = deps.git.written["public/llms.txt"]
    assert "Glasgow Research" in content
    assert "Vadim Glazkov" in content
    # the just-published post appears in the map
    status = ArtifactStore(project.runs_dir / "2026-05-19").read_json(A.PUBLISHER)
    assert status["slug"] in content
