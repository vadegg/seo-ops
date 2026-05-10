# 1. Research Agent

## Trigger

Запускается после того, как `0-orchestrator-agent` создал `intake.yaml` и выставил `next_actor: 1-research-agent`.

## Inputs

- `work-items/<article-id>/intake.yaml`
- `work-items/<article-id>/status.yaml`
- `evidence-bank/evidence-index.yaml`
- source materials under `evidence-bank/`
- `research/raw/`
- `research/analysis/`
- `strategy/`
- optional `keywords-planner/runs/`

## Actions

- interprets topic, cluster and keyword inputs;
- validates and strengthens evidence selected from `evidence-bank/`;
- opens raw evidence-bank materials when indexed notes are not enough;
- uses raw/pre-publication materials as the source of truth and does not substitute published posts for missing evidence;
- expands the keyword picture enough for editorial work;
- identifies dominant intent, adjacent terms, noise and weak directions;
- highlights useful SERP/use-case patterns;
- prepares a decision-ready research report for strategy

## Outputs

- `research-report.md`
- updated `status.yaml`

## Output Path

- `work-items/<article-id>/1-research/research-report.md`
- `work-items/<article-id>/status.yaml`

## Writable Scope

- may write only inside `work-items/<article-id>/1-research/`
- may update handoff fields in `work-items/<article-id>/status.yaml`
- must not modify strategy, writing, editing or publish artifacts

## Stop Conditions

- `intake.yaml` is missing or incomplete;
- first-party evidence is empty only after evidence-bank scan and source review failed to produce usable material;
- source material is too weak to produce a useful research report;
- article is blocked or awaiting human approval

## Next Consumer

- `2-strategy-agent`

## Human Checkpoint

- only if input is materially incomplete, confidentiality is unclear, or evidence remains too weak after evidence-bank scan
