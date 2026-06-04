# SEO Ops

`seo-ops` is the operational core of the SEO system.

This module owns:

- article-production agents
- backlog audit logic
- daily and stage orchestration
- LLM integration and logging
- supporting research tooling such as `keywords-planner/`

It is designed to work with:

- `../thematic-workspace/` as the data root
- `../blog/` as the publication target
- `../daily-agent-runner/` as a thin launch layer

## Boundaries

- No canonical theme data should live in `seo-ops/`.
- No site runtime or frontend logic should live in `seo-ops/`.
- Daily execution wrappers should stay outside this folder.

## Commands

- `npm run backlog`
- `npm run daily`
- `npm run stage`

## IndexNow

`seo-ops` can notify IndexNow automatically after a successful publish.

- The repo now includes a default key and matching public verification file at [`blog/public/129ebf08-3db2-4d2f-bf33-9ea41ef4cc90.txt`](/Users/vadegg/playground/seo/blog/public/129ebf08-3db2-4d2f-bf33-9ea41ef4cc90.txt).
- By default, submissions target `https://blog.glasgow.works` and POST to `https://api.indexnow.org/indexnow`.
- If you ever rotate the key, update both the public `.txt` file and `INDEXNOW_KEY` in your real `.env` or `.env.local`.

## Workspace Root

By default, pass a thematic workspace explicitly:

```bash
npm run daily -- --workspace-root ../thematic-workspace
```
