# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Full run (no flags = escalation ladder + publish guarantee)
.venv/bin/python run.py --dry-run            # full pipeline, no git push
.venv/bin/python run.py                      # publish (real run)
.venv/bin/python run.py --date 2026-05-19    # resume/re-run a specific day
.venv/bin/python run.py --start-stage 3      # force start from escalation stage 3

# Per-agent runs (single pass at --start-stage, NO auto-escalation)
.venv/bin/python run.py --list-steps                  # researcher..publisher
.venv/bin/python run.py --steps researcher --dry-run  # one step in isolation
.venv/bin/python run.py --steps strategist --dry-run  # needs 01 on disk first
.venv/bin/python run.py --from outliner --dry-run     # outliner..publisher
.venv/bin/python run.py --stop-after writer --dry-run # researcher..writer
.venv/bin/python run.py --steps editor --force --dry-run  # re-run even if 05 exists

# Tests (no network, no SDK — all faked)
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_assembler.py -q   # single test file
```

## Architecture

A deterministic Python orchestrator drives Claude Agent SDK subagents; artifacts live on disk between steps so any step can be resumed.

**Pipeline:** `Researcher → Strategist → Outliner → [Evidence] → Writer → Editor → Assembler → Publisher`

All agents are in `agents/` and follow the same contract: they receive structured input, emit JSON (or markdown), and know nothing about retries or model choice — that's the orchestrator's job (`pipeline/orchestrator.py`).

**Steps as units** (`pipeline/steps.py`): each of the 7 steps is a `Step(name, inputs, output, fn)` in the `STEPS` registry. A step function reads its inputs from disk (`StepContext.store`) and writes one artifact. `run_steps()` is the generic driver (resume if output exists, `StepInputError` if a required input is missing). Both entry points reuse these functions: `run_pipeline()` (full run, owns the escalation loop for steps 1–2) and `run_selected_steps()` (isolated subset, single pass at `start_stage`, no auto-escalation). Researcher and Strategist share `researcher_pass`/`strategist_pass` between the isolated steps and the escalation loop. The Assembler writes a `06-assembler.meta.json` sidecar (slug/leak) so the Publisher is fully decoupled from it.

**Isolation point:** `agents/runner.py:SDKAgentRunner` is the only place that touches `claude_agent_sdk`. Tests inject a fake runner — no SDK or network calls in tests.

**Artifacts** (`pipeline/artifacts.py`): Each step writes a numbered file to `runs/<YYYY-MM-DD>/` (e.g. `01-researcher.candidates.json`). A step whose output artifact already exists is skipped (resume). A completed `07-publisher.status.json` with `status=published` makes the whole run a no-op.

**Escalation** (`pipeline/escalation.py`): 4-stage ladder. Strategist scores the topic 0..1; below threshold → escalate to next stage (Sonnet → Opus, looser data sources, then evergreen guarantee). Every transition is logged to `runs/<date>/escalation.log`. Stage 4 is API-independent and always succeeds.

**Dependency injection:** `run_pipeline(..., deps=PipelineDeps)` — all external clients (GSC, DataForSEO, Telegram, Git, Evidence) are passed in. Tests swap them for fakes in `tests/conftest.py`.

**Clients layer** (`clients/`): one module per external dependency — `gsc.py`, `dataforseo.py`, `telegram.py`, `git_client.py`, `evidence.py` (local BM25), `indexnow.py` (post-publish indexation ping), `websearch.py` (`tools_for_stage()` gates which SDK web-search tools each escalation stage may use). `retry.py` wraps API calls in backoff and raises `ClientError`; a dead API retries, then the escalation ladder falls through to a stage that does not need it.

**Blog contract (publish target).** `blog.glasgow.works` is an Astro site deployed by **Cloudflare Pages on every push** to `glasgow-blog` (no local build step — the Publisher only commits + pushes; CF runs `astro build`). A post's live URL is `<BLOG_BASE_URL>/<slug>/` where `BLOG_BASE_URL` ends in `/blog`. The content collection is validated by a Zod schema (`blog/src/content/config.ts`): a non-conforming post **fails the build and blocks the whole site's deploy**. The Assembler (`pipeline/assembler.py`) therefore enforces the schema and raises `AssemblyError` rather than emit a bad post — required frontmatter `title, description, slug, author, authorSlug` (+ `category`), with `description` normalized to **150–160 chars** (mirrors `blog/src/lib/metadata.ts`). After a real push the Publisher fires an IndexNow ping (`clients/indexnow.py`) — best-effort, never fatal. `author`/`authorSlug` must match an author in `blog/src/data/site.ts`.

## Persistent state (committed to repo)

- `backlog/keyword_backlog.json` — unused strong candidates
- `backlog/topic_history.json` — published topics (dedupe guard)
- `backlog/seed_topics.md` — evergreen list (stage-4 guarantee)
- `themes/content_map.md` — pillars/clusters (`[x]` = covered)
- `themes/internal_links.json` — cluster → URL map (updated by Publisher)
- `style_guide.md` — Glasgow Research voice/tone for agents

## Configuration

Copy `.env.example` → `.env`. `config.py` validates all secrets at startup and fails with a list of every missing variable before any agent runs. Required vars: `ANTHROPIC_API_KEY`, `GSC_SERVICE_ACCOUNT_JSON`, `GSC_SITE_URL`, `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BLOG_REPO_URL`, `GIT_DEPLOY_KEY`, `EVIDENCE_DIR`.

`EVIDENCE_DIR` is a local BM25 corpus directory synced separately (never committed here).

## Confidential, local-only files — DO NOT COMMIT

Two directories hold confidential material and are **not** in `.gitignore` — a blanket `git add -A` would leak them to the public blog repo. Never stage them; stage files by explicit path.

- `secrets/` — live credentials (e.g. the GSC service-account private key JSON pointed to by `GSC_SERVICE_ACCOUNT_JSON`).
- `evidence/` — the BM25 corpus: client interview transcripts under NDA (Glasgow Research / TripleTen and others). This is the in-repo `EVIDENCE_DIR`.

The pipeline treats leakage of this material as a correctness property: the Editor strips NDA/PII and the Assembler records a `leak` flag in `06-assembler.meta.json`; `tests/test_confidentiality.py` guards it. If you touch the Editor, Assembler, or Evidence client, run that test.

## Models

Default: `claude-sonnet-4-6` (stages 1–2), `claude-opus-4-7` (stages 3–4 and forced-final editor rewrite). Overridable via `MODEL_SONNET` / `MODEL_OPUS` env vars or `--max-stage` CLI flag.

## Deployment

Runs on a VPS at `/opt/seo-autoblog` via systemd (`deploy/seo-autoblog.{service,timer}`): a `oneshot` service fires `python run.py` once daily (timer `OnCalendar=07:00`, interpreted in the host TZ — set it to Europe/Lisbon). Orchestrator idempotency makes a manual `systemctl start` or retry safe — a day already `published` is a no-op.
