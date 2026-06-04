import dataclasses
import json
import logging
from types import SimpleNamespace

import pytest

from clients import indexnow
from clients.indexnow import IndexNowError, submit_url

KEY = "129ebf08-3db2-4d2f-bf33-9ea41ef4cc90"


def _capture():
    calls = []

    def post(url, data, headers, timeout):
        calls.append({"url": url, "data": data, "headers": headers})
        return 200

    return calls, post


# --- client unit tests -------------------------------------------------

def test_submit_builds_expected_body():
    calls, post = _capture()
    status = submit_url("https://blog.glasgow.works/",
                        "https://blog.glasgow.works/blog/foo/",
                        key=KEY, post=post)
    assert status == 200
    body = json.loads(calls[0]["data"])
    assert body["host"] == "blog.glasgow.works"
    assert body["key"] == KEY
    assert body["keyLocation"] == f"https://blog.glasgow.works/{KEY}.txt"
    assert body["urlList"] == ["https://blog.glasgow.works/blog/foo/"]


def test_empty_key_is_disabled():
    calls, post = _capture()
    assert submit_url("https://x.com", "https://x.com/a/", key="", post=post) is None
    assert calls == []


def test_host_mismatch_raises():
    _, post = _capture()
    with pytest.raises(IndexNowError):
        submit_url("https://a.com", "https://b.com/x/", key="k" * 10, post=post)


def test_malformed_key_raises():
    _, post = _capture()
    with pytest.raises(IndexNowError):
        submit_url("https://a.com", "https://a.com/x/", key="bad key!", post=post)


def test_non_2xx_raises():
    with pytest.raises(IndexNowError):
        submit_url("https://a.com", "https://a.com/x/", key="k" * 10,
                   post=lambda *a: 403)


# --- publisher integration --------------------------------------------

def _run_publish(project, *, dry_run, monkeypatch, blow_up=False):
    from pipeline import publisher
    cfg = dataclasses.replace(
        project, indexnow_key=KEY,
        indexnow_site_url="https://blog.glasgow.works")
    calls = []

    def fake_submit(site, page, **kw):
        calls.append((site, page))
        if blow_up:
            raise IndexNowError("boom")
        return 200

    monkeypatch.setattr(indexnow, "submit_url", fake_submit)
    # don't actually back off / sleep in tests
    monkeypatch.setattr(publisher, "with_backoff", lambda fn, **kw: fn())

    assembled = SimpleNamespace(markdown="# x\n", slug="foo")

    class _Git:
        def ensure_clone(self):
            pass

        def write_post(self, rel, md):
            pass

        def commit_and_push(self, paths, msg, *, push=True):
            return "deadbeef"

    publisher.publish(
        cfg=cfg, assembled=assembled,
        brief={"title": "T", "primary_keyword": "kw"},
        topic={"topic": "t", "cluster": ""}, stage=1,
        run_date="2026-05-19", dry_run=dry_run, git_client=_Git(),
        logger=logging.getLogger("test"), candidates=[], surplus=[])
    return calls


def test_real_publish_pings_indexnow(project, monkeypatch):
    calls = _run_publish(project, dry_run=False, monkeypatch=monkeypatch)
    assert len(calls) == 1
    site, page = calls[0]
    assert site == "https://blog.glasgow.works"
    assert page == "https://blog.glasgow.works/blog/foo/"  # trailing slash


def test_dry_run_does_not_ping(project, monkeypatch):
    assert _run_publish(project, dry_run=True, monkeypatch=monkeypatch) == []


def test_indexnow_failure_does_not_break_publish(project, monkeypatch):
    # must not raise even though the ping fails
    calls = _run_publish(project, dry_run=False, monkeypatch=monkeypatch,
                         blow_up=True)
    assert len(calls) == 1
