# SEO Daily Agent Runner

Standalone automation repo for running the Glasgow-style SEO article pipeline against an external SEO workspace.

## What this repo does

- audits the evidence-qualified backlog
- creates a new daily `work-item`
- advances an active article through:
  - research
  - strategy
  - writing
  - editing
  - publish
  - git push
  - archive

This repo does not contain the blog content workspace itself. Instead, it points to an external workspace via `SEO_WORKSPACE_ROOT`.

## Required workspace

The external workspace must contain the current production structure, including:

- `workflow-config.yaml`
- `work-queue.yaml`
- `strategy/`
- `evidence-bank/`
- `archive/`
- `work-items/`
- `blog/`

## Setup

1. Copy `.env.example` to `.env`
2. Fill in:
   - `SEO_WORKSPACE_ROOT`
   - `OPENAI_API_KEY`
   - optional `OPENAI_MODEL`
3. Install dependencies:

```bash
npm install
```

## Commands

Backlog audit:

```bash
npm run backlog
```

Create or advance the daily item:

```bash
npm run daily -- --run-stages
```

Full autonomous publish run:

```bash
npm run daily -- --run-stages --force-human-bypass --publish --push
```

Advance a specific work item:

```bash
npm run stage -- --article-id 260502-market-research-methods-1 --force-human-bypass --publish --push
```

## Cron example

```bash
cd /path/to/seo-daily-agent-runner
/usr/bin/env npm run daily -- --run-stages --force-human-bypass --publish --push
```

Because the runner loads its own `.env`, cron does not need separate `export` lines.
