# 4. Editor Agent

## Trigger

Запускается после того, как существует draft и `status.yaml` передает статью в editing.

## Inputs

- `work-items/<article-id>/intake.yaml`
- `work-items/<article-id>/status.yaml`
- latest draft from `work-items/<article-id>/3-writing/`
- `work-items/<article-id>/2-strategy/content-brief.md`
- `work-items/<article-id>/2-strategy/serp-intent-brief.md`
- approved sources and evidence constraints

## Actions

- performs structural edit and clarity pass;
- checks factual claims and evidence boundaries;
- performs a public-surface audit before handoff:
  - remove local filesystem paths, repo-relative paths, unpublished filenames, evidence IDs, work-item references, and `source pack` wording from article body
  - if a public `Sources` section exists, ensure it contains only human-readable source names and public URLs
  - if the piece is a tools article, ensure the named tools in lists and tables are link-complete enough for a reader to click through
- marks unresolved risks;
- produces an edited draft and a review memo with readiness verdict

## Outputs

- `editorial-review-v1.md`
- `draft-edited-v1.md`
- updated `status.yaml`

## Output Path

- `work-items/<article-id>/4-editing/editorial-review-v1.md`
- `work-items/<article-id>/4-editing/draft-edited-v1.md`
- `work-items/<article-id>/status.yaml`

## Writable Scope

- may write only inside `work-items/<article-id>/4-editing/`
- may update handoff fields in `work-items/<article-id>/status.yaml`
- must not alter writing drafts in place

## Stop Conditions

- no approved draft is available;
- factual risk is too high to continue without human review;
- unresolved claims violate policy or evidence rules;
- the edited draft still contains internal-only references or incomplete public-source formatting;
- article is blocked or awaiting human approval

## Next Consumer

- human reviewer for risk resolution when required
- otherwise `5-publisher-agent`

## Human Checkpoint

- required for factual-risk edits
- required for `Process C`
- required when unresolved claims remain
