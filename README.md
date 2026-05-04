# SEO Daily Agent Runner

`seo-daily-agent-runner` is the generic automation repo for running a daily SEO article pipeline.

The repo contains code only. It is designed to work with:

- a separate `thematic workspace` outside git
- a separate `site repo` where published articles are written and pushed

## Architecture

The full system has three layers:

1. `runner repo`
This repo.
Contains:
- CLI commands
- backlog audit logic
- daily runner
- stage runner
- LLM integration
- logging

2. `thematic workspace`
External data folder, usually not kept in git.
Contains:
- themes and topic backlogs
- evidence bank
- work queue
- active work items
- archives
- reports
- project-specific editorial configuration

3. `site repo`
Separate git repo for the actual website.
Contains:
- site code
- published articles
- build and deploy logic

The runner reads from the thematic workspace and writes final published markdown into the site repo.

## Why this split exists

This separation keeps the automation reusable across niches and brands.

The runner repo should not contain:
- topic-specific keywords
- brand-specific tone rules
- evidence bank content
- active work items
- customer or proprietary notes
- site-specific repo history

Those belong in the thematic workspace or the site repo.

## Repo Layout

Inside this repo:

- `src/cli/`
CLI entrypoint for `backlog`, `daily`, and `stage`

- `src/lib/`
Shared helpers for files, env loading, LLM access, logs, and workspace config

- `src/modules/backlog-audit/`
Backlog health and replenishment logic

- `src/modules/daily-runner/`
Daily orchestration logic

- `src/modules/stage-runner/`
Research, strategy, writing, editing, publish, push, and archive pipeline

- `templates/`
Reference config templates

- `examples/`
Sample external thematic workspace

## Thematic Workspace

The thematic workspace is the most important part of the system.

It is the external data root pointed to by:

```env
SEO_WORKSPACE_ROOT=/absolute/path/to/thematic-workspace
```

### What must be in the thematic workspace

At minimum:

- `runner-config.yaml`
- `workflow-config.yaml`
- `work-queue.yaml`
- `strategy/`
- `evidence-bank/`
- `work-items/`
- `archive/`
- `reports/`

See the full example in:
[examples/thematic-workspace](./examples/thematic-workspace)

### Thematic workspace layout

```text
thematic-workspace/
  runner-config.yaml
  workflow-config.yaml
  work-queue.yaml
  strategy/
    topic-backlog.yaml
  evidence-bank/
    evidence-index.yaml
    raw/
  work-items/
  archive/
    published/
    dropped/
  reports/
    status/
```

### `runner-config.yaml`

This is the project-specific config that makes the generic runner theme-aware.

It defines:
- which backlog files to read
- where the evidence index lives
- editorial defaults
- where the site repo is

Example:

```yaml
backlog_files:
  - strategy/topic-backlog.yaml

evidence_index_file: evidence-bank/evidence-index.yaml

editorial:
  brand_name: Example Research
  language: English
  geo_targets:
    - US
    - EU
  audience: Founders and product teams.
  tone: Practical, direct, evidence-led.
  author_name: Jane Founder
  author_slug: jane
  default_category: Research

publishing:
  blog_repo_root: /absolute/path/to/site-repo
  blog_content_dir: /absolute/path/to/site-repo/src/content/blog
```

Template:
[templates/runner-config.example.yaml](./templates/runner-config.example.yaml)

### `workflow-config.yaml`

This defines operational policy:
- autonomy mode
- human approval requirements
- cleanup behavior
- directory defaults

It is execution policy, not theme data.

Example file:
[examples/thematic-workspace/workflow-config.yaml](./examples/thematic-workspace/workflow-config.yaml)

### `work-queue.yaml`

This is the active queue of articles in progress.

The runner uses it to:
- detect active work
- prevent duplicate intake
- choose which work item to advance

Minimal structure:

```yaml
version: 1
updated_at: "2026-01-01T00:00:00Z"
items: []
```

### `strategy/`

This folder contains topic backlogs.

A backlog file should contain:
- `program`
- `cycles`
- `topics`
- per-topic keyword, audience, process mode, and evidence references
- optional `evidence_bank` section for compact reusable evidence

The runner reads these files through `runner-config.yaml -> backlog_files`.

Example:
[examples/thematic-workspace/strategy/topic-backlog.yaml](./examples/thematic-workspace/strategy/topic-backlog.yaml)

### `evidence-bank/`

This folder contains first-party evidence and an index that maps evidence to topics.

Recommended structure:

```text
evidence-bank/
  evidence-index.yaml
  raw/
    founder-notes-2026-01-01.md
```

The runner relies on:
- `evidence-index.yaml` for discoverability
- raw files for detailed prompt grounding

Example index:
[examples/thematic-workspace/evidence-bank/evidence-index.yaml](./examples/thematic-workspace/evidence-bank/evidence-index.yaml)

### `work-items/`

This folder contains active article workspaces only.

Each article gets its own folder:

```text
work-items/<article-id>/
  intake-notes.md
  intake.yaml
  status.yaml
  1-research/
  2-strategy/
  3-writing/
  4-editing/
  5-publish/
```

The runner creates and advances these folders automatically.

### `archive/`

This stores completed outputs and dropped items.

- `archive/published/`
- `archive/dropped/`

After successful publication, the stage runner moves the final package here and removes the active work item.

### `reports/`

This stores operational output:
- backlog audit reports
- stage runner reports
- daily runner reports
- AI run logs

Recommended structure:

```text
reports/
  status/
```

## Setup

1. Copy `.env.example` to `.env`
2. Fill in:
- `SEO_WORKSPACE_ROOT`
- `OPENAI_API_KEY`
- optional `OPENAI_MODEL`
3. Create or copy a thematic workspace
4. Install dependencies:

```bash
npm install
```

## Commands

Backlog audit:

```bash
npm run backlog
```

Create a daily work item:

```bash
npm run daily
```

Create or advance the daily item through stages:

```bash
npm run daily -- --run-stages
```

Full autonomous publish run:

```bash
npm run daily -- --run-stages --force-human-bypass --publish --push
```

Advance a specific work item:

```bash
npm run stage -- --article-id 260502-customer-research-methods-1 --force-human-bypass --publish --push
```

## Example Deployment Model

One good server layout is:

```text
/srv/seo-daily-agent-runner      # this repo
/srv/data/glasgow-seo           # thematic workspace (not in git)
/srv/sites/glasgow-blog         # site repo
```

`.env` inside the runner repo:

```env
SEO_WORKSPACE_ROOT=/srv/data/glasgow-seo
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
```

`runner-config.yaml` inside the thematic workspace:

```yaml
publishing:
  blog_repo_root: /srv/sites/glasgow-blog
  blog_content_dir: /srv/sites/glasgow-blog/src/content/blog
```

## Cron example

```bash
cd /srv/seo-daily-agent-runner
/usr/bin/env npm run daily -- --run-stages --force-human-bypass --publish --push
```

Because the runner loads its own `.env`, cron does not need separate `export` lines.
