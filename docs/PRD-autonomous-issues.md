# PRD — autonomously-resolvable issues

Scope: GitHub issues that can be implemented **entirely inside `seo-ops`** with
no blog-repo access, no external outreach, and no human-supplied secret
(image-API key, lead magnet, LinkedIn URL outreach). One PR.

Out of scope (need you / blog repo): #1 (evidence corpus is NDA, local-only),
#10 (image-API provider + key + budget), #18–#23 (blog Astro repo / external
outreach), #24/#26 (publish or edit live posts), #25-content-post body.

A hard constraint shapes several of these: **the blog's Zod content schema lives
in the blog repo and a non-conforming post fails `astro build` and blocks the
whole site deploy** (CLAUDE.md). Therefore every enrichment below is emitted
into the post **body** (Markdown / embedded `<script>`/HTML), never as a new
frontmatter key whose acceptance we cannot verify here. Frontmatter-level work
(`updated`, `readingTime` keys; sitemap; RSS; headers) is deferred to the blog
repo by design.

---

## Group 1 — persistent data (no code)

### #27 Quick-win keys → backlog
Add 5 low-competition, content-aligned keywords to `backlog/keyword_backlog.json`
with valid Researcher-schema fields (`keyword`, `score`, `date`). The Researcher
reads the backlog first as a cheap reserve.
**Done:** 5 new entries present, schema-valid, above the 0.4 floor.

### #25 Five priority missing posts → seed + map
Add `JTBD interviews guide`, `win-loss analysis for B2B SaaS`, `recruiting B2B
interview participants`, `pricing research`, and a `product-research` pillar hub
to `backlog/seed_topics.md` and `themes/content_map.md` with `[ ]` clusters.
**Done:** 5 topics present in seed list and content map with clusters.

---

## Group 2 — Assembler body enrichment (deterministic, schema-safe)

All four write into the post body, are idempotent (no duplication on re-assembly),
and never raise (must not block the publish guarantee).

### #11 Agency footer + CTA
Append a standard Glasgow Research footer block (1–2 sentences + CTA link) to the
end of the body. Text/URL from `config` (`cta_url`, `cta_text`, defaults to the
agency site). Idempotent via a stable marker comment.
**Done:** every assembled post ends with exactly one footer block containing a
working link; test asserts presence + uniqueness.

### #16 Paid-tools disclosure
If the brief is flagged `has_tool_recommendations` (Outliner sets it) **or** an
affiliate/tool heuristic matches, insert a short FTC disclosure block near the
top of the body. Text from `config`. Flagged → present; unflagged → absent.
**Done:** test covers both branches.

### #15 ToC + read-time (body only)
For posts ≥1200 words: prepend a "Reading time: N min" line and an auto-generated
Table of Contents from `##` headings with GitHub-style anchors. Below 1200 words:
neither. (`updated`/`readingTime` *frontmatter* deferred — blog-schema dependency.)
**Done:** long post has ToC + read-time, short post has neither; test.

### #12 Richer JSON-LD
`_jsonld`: author as `Person {name, url, sameAs:[…]}` (was Organization);
add `dateModified` (already), `image` (ImageObject 1200×630 when a hero/OG URL is
available), and `Organization.sameAs`. Social URLs come from `config`
(`author_url`, `author_same_as`, `org_same_as`, `default_og_image`) — empty by
default so nothing is fabricated.
**Done:** JSON-LD contains a Person author; `sameAs`/`image` appear only when
configured; test.

---

## Group 3 — Agent prompt contracts (#13, #14) + schema gate (#6)

### #6 Validate agent output against a schema, retry once
Add `agents/validation.py` (dependency-free): per-agent shape/type/range checks.
`runner.run_json(...)` wraps `run`+`extract_json`+`validate`; on invalid output it
re-prompts once with the error text, and on a second failure raises `ClientError`
(so the ladder/alerts engage). Researcher/Strategist/Outliner/Editor switch to it.
**Done:** test — fake runner returns broken JSON → one retry → valid; broken
twice → `ClientError`. Existing tests stay green.

### #13 Contextual internal linking
Outliner receives a **cluster-relevant subset** of the internal-links map (not the
whole list) and is told to plan ≥3 links when the map is large enough. The
Assembler counts in-body internal links and emits a WARN (never fails) when a post
of sufficient corpus has fewer than the floor.
**Done:** with a non-empty map the brief requests ≥3 links; a thin post logs WARN;
test.

### #14 First-hand evidence block
Outliner brief carries a `first_hand_example` section requirement; Writer is told
to include ≥1 anonymised case **only when evidence passages exist** (never invent);
Editor checklist gains `first_hand_present`. Empty corpus → skipped + WARN, no
fabrication.
**Done:** prompt-contract tests assert the instruction is present iff evidence is
non-empty.

---

## Group 4 — Run telemetry & alerting

### #5 Token + cost accounting
`SDKAgentRunner` accumulates per-turn `usage` (input/output tokens) per agent;
`pipeline/usage.py:summarize(records, prices)` maps to USD via a model price
table (`config`). `run_pipeline` writes `runs/<date>/usage.json` and folds totals
into the digest.
**Done:** fake runner exposing usage → `usage.json` with per-agent + total
tokens/$; pure-function test for `summarize`.

### #3 Degradation summary in the log
A run-scoped logging handler accumulates every WARN/ERROR. At the end of
`run_pipeline` a "degradations" block is written to `run.log` (ERROR lines marked).
Empty when the run was clean.
**Done:** a run with an injected API failure ends with a summary block listing the
WARN/ERROR; unit test on the accumulator.

### #4 Empty-evidence signal
`ensure_evidence` logs the passage count (WARN at 0) and distinguishes "corpus/
index empty" from "query matched nothing". The count surfaces in the digest.
**Done:** empty `EVIDENCE_DIR` → WARN "evidence empty" + digest note; test.

### #8 One Telegram digest per run
Replace the scattered per-event sends with a single end-of-run digest (status,
escalation stage, degradations from #3, leak flag, tokens/$ from #5, next
candidates). Only a fatal crash keeps its immediate hard alert.
**Done:** a typical degraded run sends exactly one digest (+ crash alert only on
fatal); test counts `telegram.send`.

---

## Group 5 — Ops

### #17 llms.txt generation
`pipeline/llms.py:render(...)` builds an `llms.txt` (site overview, author, key
articles from `topic_history`/`internal_links`); the Publisher writes+commits it
beside the post on a real publish.
**Done:** after a real publish the blog repo has an up-to-date `llms.txt`; test.

### #9 Researcher rerun behaviour — lock in + document
Test: existing `01` → researcher skipped; `--force` → exactly one fresh `01`
(overwrite, not append). Add a CLAUDE.md paragraph fixing the resume/force
invariant.
**Done:** test green; CLAUDE.md updated.

### #7 CI (GitHub Actions)
`.github/workflows/ci.yml`: on push/PR → checkout → setup-python (3.11+3.12) →
pip install → `pytest -q`, with pip cache.
**Done:** workflow green on `main`; red on a deliberately broken test.
