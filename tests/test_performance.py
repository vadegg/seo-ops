import json
from datetime import date
from types import SimpleNamespace

import pytest

from clients.gsc import GSCClient
from pipeline.performance import collect_report, planning_context, refresh_report


class SearchData:
    def __init__(self):
        self.calls = []
        self.inspected = []

    def analytics(self, start, end, dimensions):
        self.calls.append((start, end, dimensions))
        if not dimensions:
            return [{"clicks": 8 if start == "2026-08-13" else 2,
                     "impressions": 1000, "ctr": .008, "position": 15}]
        if dimensions == ["page"]:
            return [{"keys": ["https://blog.test/blog/one/"],
                     "clicks": 1, "impressions": 100, "ctr": .01, "position": 8}]
        return [{"keys": ["shared query", f"https://blog.test/blog/{slug}/"],
                 "impressions": 20, "position": 8} for slug in ("one", "two")]

    def inspect_url(self, url):
        self.inspected.append(url)
        return {"verdict": "NEUTRAL", "coverageState": "Crawled - currently not indexed"}


POSTS = [{"url": f"https://blog.test/blog/{slug}/", "slug": slug,
          "title": slug, "date": "2026-06-01"} for slug in ("one", "two")]


def test_equal_nonoverlapping_windows_separate_totals_and_actionable_urls():
    gsc = SearchData()
    report = collect_report(gsc, POSTS, today=date(2026, 9, 12), inspect_limit=2)
    assert report["period"] == {"start": "2026-08-13", "end": "2026-09-09"}
    assert report["previous_period"] == {"start": "2026-07-16", "end": "2026-08-12"}
    assert report["current"]["clicks"] == 8  # not the page-row total of 1
    assert report["delta"]["clicks"] == 6
    assert {a["action"] for a in report["actions"]} == {"improve_existing", "review_indexing"}
    assert len(report["query_overlap_review"]) == 1
    context = planning_context(report)
    assert POSTS[0]["url"] in context and "Do not create substitute articles" in context


def test_inspections_rotate_and_keep_their_observation_dates():
    gsc = SearchData()
    old = {"indexing": [{**POSTS[0], "verdict": "PASS", "checked_at": "2026-09-01"}]}
    report = collect_report(gsc, POSTS, today=date(2026, 9, 12),
                            previous_report=old, inspect_limit=1)
    assert gsc.inspected == [POSTS[1]["url"]]
    assert report["indexing"][0]["checked_at"] == "2026-09-01"


def test_failed_inspection_is_not_reported_as_nonindexed():
    gsc = SearchData()
    gsc.inspect_url = lambda _: (_ for _ in ()).throw(RuntimeError("quota"))
    report = collect_report(gsc, POSTS, today=date(2026, 9, 12))
    assert report["indexing"] == []
    assert report["inspected_this_run"] == 0
    assert len(report["inspection_errors"]) == 1
    assert not any(a["action"] == "review_indexing" for a in report["actions"])


def test_weekly_report_cache_and_api_failure_preserve_latest(tmp_path):
    cfg = SimpleNamespace(performance_dir=tmp_path / "reports", blog_base_url="https://blog.test/blog")
    posts_dir = tmp_path / "posts"
    posts_dir.mkdir()
    (posts_dir / "one.md").write_text("---\nslug: one\ntitle: One\npubDate: 2026-06-01\n---\nText")
    gsc = SearchData()
    first = refresh_report(cfg, gsc, posts_dir, today=date(2026, 9, 12))
    calls = len(gsc.calls)
    assert refresh_report(cfg, gsc, posts_dir, today=date(2026, 9, 13)) == first
    assert len(gsc.calls) == calls
    gsc.analytics = lambda *a: (_ for _ in ()).throw(RuntimeError("offline"))
    with pytest.raises(RuntimeError, match="offline"):
        refresh_report(cfg, gsc, posts_dir, today=date(2026, 9, 20))
    assert json.loads((cfg.performance_dir / "latest.json").read_text()) == first


def test_gsc_paginates_before_filtering_near_top_queries(tmp_path):
    client = GSCClient(tmp_path / "unused", "https://blog.test/")
    requests = []
    batches = [[{"keys": ["popular"], "position": 1, "impressions": 1000}],
               [{"keys": ["opportunity"], "position": 9, "impressions": 100}], []]

    def query(**kw):
        requests.append(kw["body"].copy())
        return SimpleNamespace(execute=lambda: {"rows": batches.pop(0)})

    client._svc = SimpleNamespace(searchanalytics=lambda: SimpleNamespace(query=query))
    rows = client.near_top_queries(row_limit=1)
    assert rows[0]["query"] == "opportunity"
    assert [r["startRow"] for r in requests] == [0, 1, 2]
    assert all(r["dataState"] == "final" for r in requests)


def test_redirected_search_rows_keep_clicks_and_do_not_create_self_overlap():
    from pipeline.performance import canonical_rows
    known = {"https://blog.test/blog/one/"}
    aliases = {"https://blog.test/old": "https://blog.test/blog/one/"}
    rows = [{"keys": ["question", "https://blog.test/old"], "clicks": 2, "impressions": 20, "position": 5},
            {"keys": ["question", "https://blog.test/blog/one"], "clicks": 1, "impressions": 10, "position": 20}]
    result = canonical_rows(rows, 1, known, aliases)
    assert len(result) == 1
    assert result[0]["clicks"] == 3
    assert result[0]["impressions"] == 30
    assert result[0]["position"] == 10
    assert result[0]["ctr"] == .1
    assert rows[0]["keys"][1] == "https://blog.test/old"  # preserve raw observations


def test_editorial_queue_targets_existing_unsourced_article():
    posts = [{**POSTS[0], "needs_source_review": True}]
    report = collect_report(SearchData(), posts, today=date(2026, 9, 12), inspect_limit=0)
    item = report["editorial_updates"][0]
    assert item["url"] == POSTS[0]["url"]
    assert item["action"] == "update_existing_url"
    assert any("primary evidence" in reason for reason in item["reasons"])
