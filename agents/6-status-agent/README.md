# 6. Status Agent

## Trigger

Запускается по запросу человека или оркестратора, когда нужен program-level status вместо статуса одной статьи.

Типовые триггеры:

- "какой у нас сейчас статус?"
- "сколько циклов уже запущено?"
- "сколько статей опубликовано?"
- "какие циклы остались?"
- "на чём сейчас висим?"
- "что делать дальше?"
- "есть ли ещё потенциал по текущим ключам?"

## Inputs

- [`thematic-workspace/workflow-config.yaml`](/Users/vadegg/playground/seo/thematic-workspace/workflow-config.yaml)
- [`thematic-workspace/work-queue.yaml`](/Users/vadegg/playground/seo/thematic-workspace/work-queue.yaml)
- strategy backlogs and cycle maps under `strategy/`
- `archive/published/**/archive-summary.yaml`
- `archive/dropped/**/archive-summary.yaml`
- published posts under `blog/src/content/blog/`
- optional `evidence-bank/evidence-index.yaml` when article potential depends on available raw material

## Actions

- scans strategy backlog files to discover:
  - defined cycles
  - candidate articles
  - parked topics
  - primary keywords
- normalizes active items from `work-queue.yaml`
- normalizes published and dropped items from archive summaries
- cross-maps published slugs and active slugs back to strategy candidates
- computes program-level metrics:
  - total cycles defined
  - cycles launched
  - cycles completed
  - cycles not started
  - total published articles
  - total active articles
  - total blocked / stuck articles
- determines cycle status:
  - `not_started`
  - `in_progress`
  - `completed`
  - `parked`
- identifies where articles are currently stuck by:
  - current stage
  - next actor
  - blocking issues
- recommends the next best step:
  - continue an active item if one exists
  - otherwise start the next remaining article from the highest-priority incomplete cycle
- estimates remaining keyword potential from:
  - unpublished candidate articles
  - parked topics
  - still-unlaunched support topics inside current strategy backlogs
- writes a concise human-readable report and a machine-readable snapshot

## Outputs

- `latest-program-status.md`
- `latest-program-status.yaml`

## Output Path

- `reports/status/latest-program-status.md`
- `reports/status/latest-program-status.yaml`

## Writable Scope

- may write only inside `reports/status/`
- must not modify `work-queue.yaml`, `archive/`, `blog/`, or strategy files

## Status Logic

### Cycle launch rule

A cycle counts as `launched` if at least one mapped article from that cycle is either:

- active in `work-queue.yaml`, or
- published in `archive/published/`, or
- dropped in `archive/dropped/`

### Cycle completion rule

A cycle counts as `completed` when all non-parked candidate articles mapped to that cycle are published and there are no active items left for that cycle.

### Stuck article rule

An article counts as `stuck` when:

- it exists in `work-queue.yaml`, and
- its `blocking_issues` are non-empty, or
- its `next_actor` is `human-reviewer`, or
- it has remained in a non-terminal state without a clean handoff

### Potential rule

`keyword potential` is the sum of:

- remaining candidate articles in defined cycles
- parked topics not yet launched
- optional evidence-backed opportunities surfaced from `evidence-bank/` when the request explicitly asks for deeper expansion potential

## Stop Conditions

- no readable strategy backlog exists;
- cycle/article mapping is too ambiguous to report safely;
- archive or queue state is corrupted enough that counts would be misleading

## Next Consumer

- human
- optional `0-orchestrator-agent` if the user wants to act on the report immediately

## Human Checkpoint

- only when cycle mapping is ambiguous or the user wants to override the priority order for the next cycle
