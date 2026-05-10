# 5. Publisher Agent

## Trigger

Запускается после valid edited draft и publish-ready handoff from editing.

## Inputs

- `work-items/<article-id>/intake.yaml`
- `work-items/<article-id>/status.yaml`
- `work-items/<article-id>/4-editing/draft-edited-v1.md`
- publish rules from [`thematic-workspace/workflow-config.yaml`](/Users/vadegg/playground/seo/thematic-workspace/workflow-config.yaml)

## Actions

- assembles publish metadata and final article package;
- prepares `frontmatter.yaml`;
- prepares the final markdown article;
- runs a pre-publish public-content audit on `final-article.md`:
  - fail if the article contains local or repo paths such as `Research for seo/`, `work-items/`, `archive/`, `evidence-bank/`, or unpublished artifact filenames
  - fail if a public `Sources` section lists internal documents instead of public URLs
  - for tool-list articles, fail if visible named tools in bullets or tables are left unlinked without an explicit editorial reason
- writes into `blog/` only if config allows and approvals are satisfied;
- hands the article back to `0-orchestrator-agent` for cleanup

## Outputs

- `publish-package.md`
- `frontmatter.yaml`
- `final-article.md`
- updated `status.yaml`

## Output Path

- `work-items/<article-id>/5-publish/publish-package.md`
- `work-items/<article-id>/5-publish/frontmatter.yaml`
- `work-items/<article-id>/5-publish/final-article.md`
- optional final write to `blog/src/content/blog/`
- `work-items/<article-id>/status.yaml`

## Writable Scope

- may write only inside `work-items/<article-id>/5-publish/`
- may update handoff fields in `work-items/<article-id>/status.yaml`
- may write to `blog/src/content/blog/` only if config explicitly allows it

## Stop Conditions

- publish package is incomplete;
- final human approval is required and missing;
- config forbids writing to `blog/`;
- metadata is incomplete or contradictory;
- final article leaks internal workflow artifacts or fails the public-content audit

## Next Consumer

- human reviewer when final approval is required
- otherwise `0-orchestrator-agent` for cleanup

## Human Checkpoint

- required when `require_final_human_approval: true`
- required whenever publish config or metadata are ambiguous
