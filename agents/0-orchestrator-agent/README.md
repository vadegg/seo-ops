# 0. Orchestrator Agent

## Trigger

Запускается первым для каждого нового article lifecycle и последним для cleanup.

Триггеры:

- человек дает свободные вводные по статье;
- появляется новый кандидат из `keywords-planner/runs/`;
- статья достигла terminal state и готова к архивации;
- нужно перевести статью на следующий stage без ручного поиска файлов

## Inputs

- human free-form brief, notes or transcript;
- `keywords-planner/runs/`;
- `evidence-bank/evidence-index.yaml`;
- source materials under `evidence-bank/`;
- [`thematic-workspace/workflow-config.yaml`](/Users/vadegg/playground/seo/thematic-workspace/workflow-config.yaml);
- [`thematic-workspace/work-queue.yaml`](/Users/vadegg/playground/seo/thematic-workspace/work-queue.yaml);
- existing `work-items/` and `archive/` directories

## Actions

- normalizes raw input into `intake.yaml`;
- scans `evidence-bank/` for topic-relevant material before asking the human for more evidence;
- treats published blog posts as outputs, not as default evidence-bank inputs;
- writes `evidence_bank_refs` and `evidence_search_notes` into `intake.yaml`;
- auto-selects candidate `first_party_evidence` from indexed materials when possible;
- generates `article-id` using `YYMMDD-<topic-slug>-N`;
- creates `work-items/<article-id>/`;
- writes `intake-notes.md`, `intake.yaml`, `status.yaml`;
- registers the article in `work-queue.yaml`;
- assigns `next_actor`;
- enforces concurrency limits from `workflow-config.yaml`;
- marks human checkpoints;
- archives published and dropped articles;
- removes inactive work-items from the active zone

## Outputs

- `intake-notes.md`
- `intake.yaml`
- `status.yaml`
- updated `work-queue.yaml`
- archive summary files during cleanup

## Output Path

- `work-items/<article-id>/intake-notes.md`
- `work-items/<article-id>/intake.yaml`
- `work-items/<article-id>/status.yaml`
- `archive/published/<article-id>/`
- `archive/dropped/<article-id>/`

## Writable Scope

- may create and update `work-items/<article-id>/`
- may update [`thematic-workspace/work-queue.yaml`](/Users/vadegg/playground/seo/thematic-workspace/work-queue.yaml)
- may create archive folders under `archive/`
- must not edit stage outputs created by other agents except `status.yaml`
- must not write to `blog/` directly

## Stop Conditions

- required input is too ambiguous to normalize into `intake.yaml`;
- `evidence-bank/` was scanned but did not yield enough safe material for the topic;
- no free worker slot is available under configured concurrency;
- article is waiting for human approval;
- `latest_approved_artifact` is missing for the next handoff;
- cleanup preconditions are not met

## Next Consumer

- `1-research-agent` after intake creation
- any next stage agent after a valid handoff
- no next consumer after terminal archive

## Human Checkpoint

- override article priority or scheduling;
- resolve ambiguous intake after evidence-bank scan failed or confidentiality is unclear;
- approve exception paths;
- decide whether to resume dropped items;
- change publish autonomy via `workflow-config.yaml`
