# seo-autoblog

Autonomous daily SEO post for the Glasgow Research Astro blog. A
deterministic Python orchestrator drives Claude Agent SDK subagents,
passes artifacts on disk, owns retries / model choice / escalation, and
publishes by git commit → autodeploy. **A post ships every day** — weak
candidates or dead APIs trigger escalation, never a skip.

## Pipeline

```
1 Researcher → 2 Strategist → 3 Outliner → 4 Writer → 5 Editor
            → 6 Assembler (code) → 7 Publisher (code)
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
is **resumed** (skipped). A successful `07` with `status=published` for
today makes a re-run a **no-op** (idempotent).

## Escalation ladder (escalate, never skip)

| Stage | Approach | Model |
|---|---|---|
| 1 | GSC near-top (pos 5–20) + DataForSEO expansion | Sonnet |
| 2 | Loosen GSC + SERP-gap via web search + competitors | Sonnet |
| 3 | Full web-search gap analysis + competitors, reframe intent | Opus |
| 4 | Evergreen seed list minus topic_history (guarantee) | Opus |

Strategist scores the topic 0..1; below `SCORE_THRESHOLD` → next stage,
with a Telegram "degraded to level N" alert. Stage 4 is API-independent
and always yields a publishable topic. A dead API retries with backoff,
then falls through to a stage that does not need it. Editor failing the
checklist twice → forced final Opus rewrite + hard alert (never abandon).
Every transition is in `runs/<date>/escalation.log`.

## Persistent stores

- `backlog/keyword_backlog.json` — strong unused candidates (cheap reserve).
- `backlog/topic_history.json` — published topics (dedupe guard).
- `backlog/seed_topics.md` — curated evergreen list (stage 4 guarantee).
- `themes/content_map.md` — pillars/clusters, `[x]` when covered.
- `themes/internal_links.json` — cluster/topic → URL map.
- `style_guide.md` — Glasgow Research voice.
- `runs/<date>/` — artifacts + `run.log` + `escalation.log`.

## Environment

Copy `.env.example` → `.env` (on the VPS, outside git). `config.py`
validates **all** secrets at startup — a missing key fails before any
agent runs, listing every problem at once:

`ANTHROPIC_API_KEY`, `GSC_SERVICE_ACCOUNT_JSON`, `GSC_SITE_URL`,
`DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `BLOG_REPO_URL`, `GIT_DEPLOY_KEY`, `EVIDENCE_DIR`
(+ optional tunables, see `.env.example`).

`EVIDENCE_DIR` is synced privately (rsync over SSH or a private repo),
**never** in this repo.

## Run

Local:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --dry-run            # everything except git push
.venv/bin/python run.py                       # real publish
.venv/bin/python run.py --date 2026-05-19     # re-run a day (resume)
.venv/bin/python -m pytest -q                 # tests (no network/SDK)
```

VPS (`/opt/seo-autoblog`):

```bash
sudo cp deploy/seo-autoblog.service /etc/systemd/system/
sudo cp deploy/seo-autoblog.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seo-autoblog.timer
# First real run supervised (timer off), verify the post on the site,
# then enable the timer.
sudo systemctl start seo-autoblog.service && journalctl -u seo-autoblog -f
```

## Cost

1 post/day. Stages 1–2 Sonnet, 3–4 Opus. Normal: a few $/day; higher on
degraded days. `MAX_STAGE` / `AGENT_MAX_TOKENS` cap it via config.

## Tests

`pytest` (40 tests, no network/SDK — all clients & the agent runner are
dependency-injected and faked):

- config validation (all missing secrets reported at once)
- artifact resume + idempotency
- escalation to the stage-4 guarantee + Telegram alerts
- API-unavailable fall-through to an independent stage
- editor forced-final Opus + hard alert
- confidentiality scrub (CONFIDENTIAL/NDA never reach the post)
- assembler frontmatter / JSON-LD / image alts
- evidence BM25 ranking, retry/backoff
- end-to-end dry-run + resume skipping completed steps
