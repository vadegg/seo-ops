# keywords-planner

Standalone workspace for the keyword-agent pipeline.

This tool lives outside `blog/` on purpose. It is a separate SEO research utility and should not be mixed into the Astro app runtime.

## Structure

- `src/` - source code for the agent and its modules
- `config/` - site profile, modifier libraries, provider settings
- `input/` - manual seed keywords for `v1`
- `runs/` - generated run outputs, review files, reports
- `artifacts/` - browser automation artifacts and raw captures
- `templates/` - sample review/report/config templates
- `tests/` - unit, integration, and smoke test fixtures

## v1 note

`Module 0` (`seed-generator`) is intentionally not implemented in the first version.

The expected starting point for `v1` is:

1. Put seed keywords into `input/seeds.yaml`
2. Run `npm run keyword:agent:start`
3. Use `runs/` and `artifacts/` as non-versioned working directories

## Commands

- `npm run keyword:agent:start`
- `npm run keyword:agent:resume -- --run-id <id>`
- `npm run keyword:agent:resume -- --run-id <id> --skip-volume`
- `npm run keyword:agent:volume -- --run-id <id>`

## Practical v1 flow

1. Edit `input/seeds.yaml`
2. Run `keyword:agent:start`
3. Open `runs/<run-id>/keyword_review.csv`
4. Set `include=true` for the keywords you want and add manual keywords if needed
5. Run `keyword:agent:resume`
6. If Google Ads automation needs help, use the opened browser once, then let the tool parse the visible Keyword Planner table
