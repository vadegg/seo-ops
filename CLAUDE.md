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

# Tests (no network, no API — all faked)
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_assembler.py -q   # single test file
```

## Architecture

A deterministic Python orchestrator drives subagents over the Anthropic Messages API (`anthropic` library); artifacts live on disk between steps so any step can be resumed.

**Pipeline (7 steps):** `Researcher → Strategist → Outliner → Writer → Editor → Assembler → Publisher`. Evidence is **not** a step — it's an on-demand BM25 support artifact (`03b-evidence.json`) gathered in Python by `ensure_evidence()` and fed into the Writer and Editor steps.

The five LLM agents (Researcher, Strategist, Outliner, Writer, Editor) live in `agents/` and follow the same contract: they receive structured input, emit JSON (or markdown), and know nothing about retries or model choice — that's the orchestrator's job (`pipeline/orchestrator.py`). The Assembler and Publisher are deterministic code in `pipeline/` (`assembler.py`, `publisher.py`), not LLM agents.

**Steps as units** (`pipeline/steps.py`): each of the 7 steps is a `Step(name, inputs, output, fn)` in the `STEPS` registry. A step function reads its inputs from disk (`StepContext.store`) and writes one artifact. `run_steps()` is the generic driver (resume if output exists, `StepInputError` if a required input is missing). Both entry points reuse these functions: `run_pipeline()` (full run, owns the escalation loop for steps 1–2) and `run_selected_steps()` (isolated subset, single pass at `start_stage`, no auto-escalation). Researcher and Strategist share `researcher_pass`/`strategist_pass` between the isolated steps and the escalation loop. The Assembler writes a `06-assembler.meta.json` sidecar (slug) so the Publisher is fully decoupled from it.

**Isolation point:** `agents/runner.py:SDKAgentRunner` is the only place that touches the `anthropic` SDK (direct Anthropic Messages API). Tests inject a fake runner — no API or network calls in tests.

**Artifacts** (`pipeline/artifacts.py`): Each step writes a numbered file to `runs/<YYYY-MM-DD>/` (e.g. `01-researcher.candidates.json`). A step whose output artifact already exists is skipped (resume). A completed `07-publisher.status.json` with `status=published` makes the whole run a no-op.

**Resume vs. `--force` (rerun invariant).** Re-running a step is idempotent: if its output artifact is already on disk it is *skipped* (`run_steps`, `pipeline/steps.py`). `--force` re-runs the step and **overwrites** its single artifact via `write_json`/`write_text` — it never appends or accumulates. So re-running the Researcher with `--force` replaces `01-researcher.candidates.json` with one fresh object; it does not grow the candidate list. Growth of the reusable keyword reserve is a *separate* concern owned by the Publisher (`_reconcile_keyword_backlog`, floor+cap). Covered by `test_force_overwrites_researcher_not_appends`.

**Run telemetry** (`logging_setup.RunLogAccumulator`, `pipeline/usage.py`): every run collects WARN/ERROR into a degradation summary (written to `run.log`) and per-agent token/USD usage (written to `runs/<date>/usage.json`). `run_pipeline` sends **one** consolidated Telegram digest at the end (publish summary + degradations + tokens/$); only a fatal crash keeps its own immediate hard alert. Per-event alerts (escalation, forced-final editor) are surfaced as WARN/ERROR log records that feed both the summary and the digest.

**Escalation** (`pipeline/escalation.py`): 4-stage ladder. Strategist scores the topic 0..1; below threshold → escalate to next stage (Sonnet → Opus, looser data sources, then evergreen guarantee). Every transition is logged to `runs/<date>/escalation.log`. Stage 4 is API-independent and always succeeds.

**Dependency injection:** `run_pipeline(..., deps=PipelineDeps)` — all external clients (GSC, DataForSEO, Telegram, Git, Evidence) are passed in. Tests swap them for fakes in `tests/conftest.py`.

**Clients layer** (`clients/`): one module per external dependency — `gsc.py`, `dataforseo.py`, `telegram.py`, `git_client.py`, `evidence.py` (local BM25), `indexnow.py` (post-publish indexation ping), `websearch.py` (`tools_for_stage()` gates which web-search tools each escalation stage may use). `retry.py` wraps API calls in backoff and raises `ClientError`; a dead API retries, then the escalation ladder falls through to a stage that does not need it.

**Blog contract (publish target).** `blog.glasgow.works` is an Astro site deployed by **Cloudflare Pages on every push** to `glasgow-blog` (no local build step — the Publisher only commits + pushes; CF runs `astro build`). A post's live URL is `<BLOG_BASE_URL>/<slug>/` where `BLOG_BASE_URL` ends in `/blog`. The content collection is validated by a Zod schema (`blog/src/content/config.ts`): a non-conforming post **fails the build and blocks the whole site's deploy**. The Assembler (`pipeline/assembler.py`) therefore enforces the schema and raises `AssemblyError` rather than emit a bad post — required frontmatter `title, description, slug, author, authorSlug` (+ `category`), with `description` normalized to **150–160 chars** (mirrors `blog/src/lib/metadata.ts`). The Assembler also emits the schema's optional `updatedDate` (= run date) and `readingTime` (words/200); the blog template renders a per-post reading time and shows "Updated" only when `updatedDate` differs from `pubDate`. After a real push the Publisher fires an IndexNow ping (`clients/indexnow.py`) — best-effort, never fatal. `author`/`authorSlug` must match an author in `blog/src/data/site.ts`.

## Persistent state (committed to repo)

- `backlog/keyword_backlog.json` — unused strong candidates
- `backlog/topic_history.json` — published topics (dedupe guard)
- `backlog/seed_topics.md` — evergreen list (stage-4 guarantee)
- `themes/content_map.md` — pillars/clusters (`[x]` = covered)
- `themes/internal_links.json` — cluster → URL map (updated by Publisher)
- `style_guide.md` — Glasgow Research voice/tone for agents

## Maintenance tools (`tools/`)

One-off / manual repair scripts, run by hand (no argparse — positional args via `sys.argv`):

- `tools/fix_post_frontmatter.py <post.md> [<post.md> …]` — brings already-published posts into line with the Astro content schema. Reuses the Assembler's `_fit_meta_description` so the result matches new posts (author/authorSlug/category + 150–160 char description). Idempotent: skips fields already present.
- `tools/reconcile_topic_history.py <blog_content_dir> <topic_history.json> [base_url]` — rebuilds `topic_history.json`'s `published` list from the live blog content collection (the source of truth), preserving per-entry fields (keyword, escalation_stage). Run it when the dedupe guard drifts from what's actually on the site, or the pipeline may re-propose covered topics.

## Configuration

Copy `.env.example` → `.env`. `config.py` validates all secrets at startup and fails with a list of every missing variable before any agent runs. Required vars: `ANTHROPIC_API_KEY`, `GSC_SERVICE_ACCOUNT_JSON`, `GSC_SITE_URL`, `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BLOG_REPO_URL`, `GIT_DEPLOY_KEY`, `EVIDENCE_DIR`.

`EVIDENCE_DIR` is a local BM25 corpus directory synced separately (never committed here).

## Confidential, local-only files — DO NOT COMMIT

Two directories hold confidential material. They **are** listed in `.gitignore`, so a plain `git add -A` will not stage them — but never defeat that guard with `git add -f`, and prefer staging files by explicit path. Treat leakage as a correctness property, not just a convenience.

- `secrets/` — live credentials (e.g. the GSC service-account private key JSON pointed to by `GSC_SERVICE_ACCOUNT_JSON`).
- `evidence/` — the BM25 corpus: client interview transcripts (Glasgow Research / TripleTen and others). This is the in-repo `EVIDENCE_DIR`. Any NDA/PII must be stripped at the data level before a transcript is added to the corpus — the pipeline no longer scrubs confidential material at publish time.

## Models

Default: `claude-sonnet-4-6` (stages 1–2), `claude-opus-4-7` (stages 3–4 and forced-final editor rewrite). Overridable via `MODEL_SONNET` / `MODEL_OPUS` env vars or `--max-stage` CLI flag.

## GitHub Issues

Before running `gh issue create`, first write a detailed implementation spec
(problem with `file:line` refs, why it matters, what to implement, affected
files, acceptance criteria, dependencies) — never a one-line title. Only the
priority labels (`prio:high`/`prio:medium`) are used; no role/area/source
labels. Full convention: `.github/ISSUE_GUIDELINES.md`.

## Deployment

- host: `clawd@167.235.134.6`        # SSH target (key on laptop)
- path: `/home/clawd/seo/seo-autoblog`  # project dir on the server
- branch: `main`                     # branch the server tracks
- rebuild: none (cron picks up new code next run; reinstall deps only if `requirements.txt` changed)

Runs on a VPS via a **user crontab** entry (NOT the systemd units in `deploy/` — those are an unused artifact and are not installed on the host):

```
0 1 * * *  cd /home/clawd/seo/seo-autoblog && .venv/bin/python run.py >> runs/cron-daily.log 2>&1
```

Fires `python run.py` once daily at **01:00 UTC** (server TZ is UTC = 02:00 Europe/Lisbon in summer / 01:00 in winter). Orchestrator idempotency makes a manual rerun safe — a day already `published` is a no-op. To deploy a push: `ssh <host> 'cd <path> && git fetch origin main && git merge --ff-only origin/main'`.
