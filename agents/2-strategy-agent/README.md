# 2. Strategy Agent

## Trigger

Запускается после valid `research-report.md` и handoff from research.

## Inputs

- `work-items/<article-id>/intake.yaml`
- `work-items/<article-id>/status.yaml`
- `work-items/<article-id>/1-research/research-report.md`
- evidence refs already selected from raw/pre-publication materials in `evidence-bank/` inside `intake.yaml`
- [`thematic-workspace/strategy/article-pipeline.md`](/Users/vadegg/playground/seo/thematic-workspace/strategy/article-pipeline.md)
- [`thematic-workspace/strategy/market-research-keyword-base-2026-03-24.md`](/Users/vadegg/playground/seo/thematic-workspace/strategy/market-research-keyword-base-2026-03-24.md)
- [`thematic-workspace/strategy/seo-glossary.md`](/Users/vadegg/playground/seo/thematic-workspace/strategy/seo-glossary.md)
- [`/Users/vadegg/playground/seo/blog/CONTENT_POLICY.md`](/Users/vadegg/playground/seo/blog/CONTENT_POLICY.md)

## Actions

- runs topic gate;
- checks whether selected evidence-bank material is strong enough for the proposed thesis;
- assumes published articles are support outputs, not canonical evidence inputs;
- decides `Process B` or `Process C`;
- defines thesis, angle and editorial boundary;
- creates SERP and intent brief;
- produces a content brief that can be handed to writing

## Outputs

- `topic-gate.md`
- `serp-intent-brief.md`
- `content-brief.md`
- updated `status.yaml`

## Output Path

- `work-items/<article-id>/2-strategy/topic-gate.md`
- `work-items/<article-id>/2-strategy/serp-intent-brief.md`
- `work-items/<article-id>/2-strategy/content-brief.md`
- `work-items/<article-id>/status.yaml`

## Writable Scope

- may write only inside `work-items/<article-id>/2-strategy/`
- may update handoff fields in `work-items/<article-id>/status.yaml`
- must not rewrite research outputs or writing outputs

## Stop Conditions

- topic gate returns `reject`;
- evidence is weak for the promised angle even after evidence-bank scan and research refinement;
- required policy constraints are not met;
- article requires a human checkpoint before writing

## Next Consumer

- human reviewer for `Process C`, reject/uncertain cases, or flagged evidence
- otherwise `3-writer-agent`

## Human Checkpoint

- required for `Process C`
- required for reject or uncertain verdicts
- required when evidence is weak, missing or risky
