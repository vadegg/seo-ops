# Humanizer (#39) — review artifact

The Humanizer reduces AI markers in the post body. It has two layers:

1. **Deterministic cliché strip** (`agents/humanizer.py:strip_cliches`) — no
   API. Removes template connectives, canned openers/closers, and impersonal
   filler (the list in `agents/humanizer.py:AI_CLICHES`, mirrored into
   `style_guide.md`). Preserves facts, numbers, and Markdown links verbatim.
   Idempotent.
2. **LLM rewrite** (`agents/humanizer.py:run`) — varies sentence rhythm, adds
   Glasgow Research's first-person voice and concreteness, anchored to
   `style_guide.md`. Non-blocking: on any model failure it passes the edited
   draft through unchanged; on an empty reply it falls back to the
   deterministic strip. It never raises.

This file shows the **deterministic** layer only (the LLM rewrite needs the
API; it is exercised in `tests/test_humanizer.py` via a fake runner).

Reproduce:

```
.venv/bin/python tools/demo_humanizer.py            # built-in sample below
.venv/bin/python tools/demo_humanizer.py post.md    # your own Markdown file
```

## Sample — before

```markdown
## Why sample size matters

In today's fast-paced world, research moves quickly. Moreover, the five-user rule is widely cited across the industry. It's important to note that 5 users find roughly 85% of usability problems. Furthermore, you should read our [research methods](/blog/ux-research-methods) hub for the full picture.

Additionally, task coverage matters more than raw participant count. In conclusion, plan for 5-8 participants per round and adjust as you learn.
```

## Sample — after (deterministic strip)

```markdown
## Why sample size matters

Research moves quickly. The five-user rule is widely cited across the industry. 5 users find roughly 85% of usability problems. You should read our [research methods](/blog/ux-research-methods) hub for the full picture.

Task coverage matters more than raw participant count. Plan for 5-8 participants per round and adjust as you learn.
```

## Result

- clichés removed: **6** ("In today's fast-paced world", "Moreover", "It's
  important to note that", "Furthermore", "Additionally", "In conclusion")
- internal/Markdown links preserved: **1 → 1** (`[research methods](/blog/ux-research-methods)`)
- facts preserved: `85%`, `5 users`, `5-8 participants`, `five-user rule`
- sentence left after each removed connective is re-capitalised cleanly

The LLM rewrite then takes this cleaned body and re-shapes rhythm and voice
(see the fake-runner output asserted in
`tests/test_humanizer.py::test_run_rewrites_and_anchors_to_style_guide`).
