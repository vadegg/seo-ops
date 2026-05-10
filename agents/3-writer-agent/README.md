# 3. Writer Agent

## Trigger

Запускается только после approved strategy handoff и valid `content-brief.md`.

## Inputs

- `work-items/<article-id>/intake.yaml`
- `work-items/<article-id>/status.yaml`
- `work-items/<article-id>/2-strategy/content-brief.md`
- `work-items/<article-id>/2-strategy/serp-intent-brief.md`
- optional approved evidence notes from prior stages

## Actions

- writes `outline.md` first;
- waits for outline approval when the workflow requires it;
- writes the first draft strictly from the approved brief and outline;
- versions subsequent drafts as `draft-v2.md`, `draft-v3.md`, and so on
- keeps research provenance out of the public article body: internal filenames, repo paths, work-item paths, archive paths, evidence-bank references, and phrases like `source pack` belong in notes or publish-package only, never in reader-facing copy
- when the article contains a `Sources`, `Methodology`, `Data`, or `Further reading` section, includes only public-facing source names and public URLs; never list local files such as `.md`, `.yaml`, `.csv`, `.xlsx`, or repo directories
- for tool roundup or comparison articles, links every named product shown in bullets, tables, or shortlist sections to its official public page, preferably via clean markdown links or reference links

## Outputs

- `outline.md`
- `draft-v1.md`
- later draft versions if needed
- updated `status.yaml`

## Output Path

- `work-items/<article-id>/3-writing/outline.md`
- `work-items/<article-id>/3-writing/draft-v1.md`
- `work-items/<article-id>/3-writing/draft-vN.md`
- `work-items/<article-id>/status.yaml`

## Writable Scope

- may write only inside `work-items/<article-id>/3-writing/`
- may update handoff fields in `work-items/<article-id>/status.yaml`
- must not edit strategy artifacts or editing artifacts

## Stop Conditions

- `content-brief.md` is missing or not approved;
- outline approval is required and not present;
- evidence is too weak for the claims implied by the draft;
- draft contains internal-only references that would leak implementation details or source-workflow artifacts into the public article;
- article is blocked or awaiting human approval

## Next Consumer

- human reviewer after `outline.md` when required
- otherwise `4-editor-agent`

## Human Checkpoint

- required for outline approval in manual mode
- required for flagged claims, risky topics or `Process C`
