# seo-autoblog

Autonomous daily SEO post for the Glasgow Research Astro blog. A
deterministic Python orchestrator drives subagents via `codex exec`
(using the cron user's ChatGPT/Codex subscription), passes artifacts on disk, owns retries / model
choice / escalation, and publishes through a validated site build → git push → live-page verification. A daily attempt may stop with retained drafts when no topic or article passes the quality checks.

## Pipeline

```
1 Researcher → 2 Strategist → 3 Outliner → 4 Writer → 5 Editor
            → Uniqueness → Humanizer → Assembler → Publisher
```

| Step | Owner | Reads | Writes |
|---|---|---|---|
| 1 | Researcher (agent) | keyword_backlog, topic_history, GSC, DataForSEO, web search | `01-researcher.candidates.json` |
| 2 | Strategist (agent) | 01, topic_history, content_map | `02-strategist.topic.json` |
| 3 | Outliner (agent) | 02, content_map, internal_links, web search | `03-outliner.brief.json` |
| – | Evidence (code) | brief keywords, EVIDENCE_DIR (BM25) | `03b-evidence.json` |
| 4 | Writer (agent) | 03, style_guide, evidence | `04-writer.draft.md` |
| 5 | Editor/Critic (agent) | 04, 03, evidence | `05-editor.edited.md`, `05-editor.critique.json` |
| 6 | Assembler (code) | 05, 03, internal_links | `06-assembler.post.md` |
| 7 | Publisher (code) | 06 | git commit+push, `07-publisher.status.json`; updates internal_links, content_map, topic_history |

**Artifact naming:** `NN-owner.name.ext` — numeric prefix = pipeline
order, name = owning step. A step whose output artifact already exists
is **resumed** only when its approval/state remains valid. Forcing an upstream step invalidates all dependent outputs. A successful `07` with `status=published` for
today makes a re-run a **no-op** (idempotent).

## Escalation ladder and quality checks

| Stage | Approach | Model |
|---|---|---|
| 1 | GSC near-top (pos 5–20) + DataForSEO expansion | Sonnet |
| 2 | Loosen GSC + SERP-gap via web search + competitors | Sonnet |
| 3 | Full web-search gap analysis + competitors, reframe intent | Opus |
| 4 | Evergreen seed list minus topic_history (final attempt) | Opus |

Strategist scores the topic 0..1; below `SCORE_THRESHOLD` → next stage,
logged as a "degraded to level N" WARN (surfaced in the run report). Stage
4 selects topics independently of GSC/DataForSEO but still requires a score ≥ 0.62 and a distinct topic.
Billing/authentication failures stop retries immediately; usable GSC data survives a
DataForSEO outage. Transient CLI failures preserve their JSONL diagnostics and retry.
The editor gets three attempts; a failed final checklist blocks publication.
Missing or failed uniqueness checks also block assembly/publication. The humanizer
must preserve links, numbers and headings, and changed text gets a final editorial
review; otherwise the approved draft is retained. Every escalation is in
`runs/<date>/escalation.log`.

Every Outliner pass has live web access for primary-source verification,
independent of topic-selection stage. Its brief must include source URLs,
publishers, check dates, supported claims and short supporting excerpts, plus
a usable original deliverable and the specific difference from an existing
article. The Editor checks those claims and the deliverable. Final publication
requires the verified citations to remain in the body; resumed old briefs
must meet this contract too. This is a traceability gate, not automated proof
that every statement is true. A rejected brief/draft remains available for
revision; forcing the Outliner invalidates dependent outputs.

Descriptions must be complete text of 80–200 characters. The assembler
normalises whitespace and rejects lengths outside that range; it never pads
or truncates a sentence. It emits one of five `hub` values so future posts
automatically receive an incoming link from their topic guide. Reviewed intent
aliases in `pipeline/dedupe.py` block known thematic-analysis, buyer-research
and B2B-recruitment duplicates while preserving distinct specialist questions.

## Persistent stores

- `backlog/keyword_backlog.json` — strong unused candidates (cheap reserve).
- `backlog/topic_history.json` — published topics (dedupe guard).
- `backlog/seed_topics.md` — curated evergreen list (stage 4 fallback).
- `themes/content_map.md` — pillars/clusters, `[x]` when covered.
- `themes/internal_links.json` — cluster/topic → URL map.
- `style_guide.md` — Glasgow Research voice.
- `runs/<date>/` — artifacts + `run.log` + `escalation.log`.

## Environment

Copy `.env.example` → `.env` (on the VPS, outside git). `config.py`
validates **all** secrets at startup — a missing key fails before any
agent runs, listing every problem at once:

`GSC_SERVICE_ACCOUNT_JSON`, `GSC_SITE_URL`,
`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `BLOG_REPO_URL`, `GIT_DEPLOY_KEY`, `EVIDENCE_DIR`
DataForSEO is optional: set both `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` to enable it.
See `.env.example` for optional tunables. No `OPENAI_API_KEY` — the
`codex` CLI runs on its own cached `codex login` subscription session.

`EVIDENCE_DIR` is synced privately (rsync over SSH or a private repo),
**never** in this repo.

## Run

Local:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --dry-run            # everything except git push
.venv/bin/python run.py                       # real publish
.venv/bin/python run.py --date 2026-05-19     # re-run a day (resume)
.venv/bin/python -m pytest -q                 # tests (no network, no CLI)
```

## Publication and recovery

The current VPS uses a user cron at **19:00 UTC** in `/home/clawd/seo/seo-autoblog`.
The systemd files in `deploy/` are examples, not the active scheduler. A filesystem
lock prevents concurrent runs from resetting the shared managed checkout.

The publisher refreshes its clone, checks slug uniqueness, installs dependencies
when the lockfile changes, then runs `npm run build` before committing. In the blog,
that command also validates internal pages/assets/fragments, canonical URLs, H1s,
indexability and one BlogPosting schema per article. Node 22.16+ (within 22.x) and
npm must be reachable; set `NODE_BIN` to an absolute path for cron.

A dry-run commits locally and leaves persistent catalogs untouched. A subsequent
real run on the same date still pushes. Real publication persists `status=pushed`
after push, then checks the expected HTTP 200 page, H1, canonical and robots state.
Only verified deployment becomes `status=published`. A timeout leaves a recoverable
`pushed` state: rerun the same `--date` to verify again without regenerating or
pushing another article. Catalog updates are atomic, idempotent upserts. IndexNow
runs only after live verification. Dry-runs send no fleet or Telegram messages.

Astro owns the rendered table of contents, BlogPosting JSON-LD and `/llms.txt`.
The Python assembler emits frontmatter and article content; do not recreate
`public/llms.txt` or embed a second BlogPosting in Markdown.

## Search performance feedback

Once per week before topic selection, a real daily run refreshes a read-only GSC
report in `WORKSPACE_ROOT/reports/seo-performance/` (default: sibling
`thematic-workspace/`). It compares equal 28-day periods, excludes the most recent
three days, inspects up to 20 URLs in rotation, and lists existing-page improvements,
indexing issues and shared-query review candidates. Dated observations are passed
to Researcher/Strategist to discourage substitute articles for existing URLs.
A reporting outage preserves the last successful report; it does not mean zero traffic.

To refresh independently, with no agents, publication or messages:

```bash
.venv/bin/python run.py --report-only --blog-dir ../blog --inspect-limit 100
```

Search clicks are not leads or revenue. Shared queries are not proof of harmful
cannibalization; the review queue requires editorial judgment before consolidation.

## Models and cost

The legacy Sonnet/Opus tier names map to `MODEL_SONNET` / `MODEL_OPUS` (defaults:
`gpt-5.6-terra` / `gpt-5.6-sol`). Codex subscription calls record token usage and
`usd=0`; that is not an estimate of the subscription or third-party API cost.

## Tests

`pytest` runs offline with injected model/API clients and temporary local Git remotes:

- config validation (all missing secrets reported at once)
- artifact resume + idempotency
- rejection of low-scoring or duplicate topics at the escalation ceiling
- API-unavailable fall-through to an independent stage
- failed final editorial checks block publication
- fleet reporting: run report built + submitted (fail-soft), crash → `fail`,
  no-op → `skipped`, `--steps` → internal `report.json` only
- assembler frontmatter / Astro ownership of JSON-LD / image alts
- evidence BM25 ranking, retry/backoff
- end-to-end dry-run + resume skipping completed steps
- deployment failures and same-day recovery without duplicate state
- evidence relevance, Unicode, complete editor input and strict review schemas
- paginated GSC data, equal reporting windows, rotating inspection and failure-safe cache
