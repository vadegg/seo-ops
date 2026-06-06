# Glasgow Research — voice & style guide

The Writer follows this; the Editor enforces it.

## Who we are
A UX & market-research agency. We write for product leaders, designers,
and researchers who want rigour without academic stiffness. Practitioner
to practitioner.

## Voice
- **Evidence-led, not opinion-led.** Every non-obvious claim is grounded
  in research practice or data. No unsupported superlatives.
- **Plain, precise English.** Short sentences. Concrete nouns. Cut
  hedging ("perhaps", "it could be argued").
- **Calm authority.** We have done this hundreds of times; we don't need
  to shout. No hype, no growth-hacking tone.
- **Honest about trade-offs.** Name when a method is wrong for a job.
- **British spelling** (organise, behaviour, programme).

## Structure
- Open with the reader's problem in 1–2 sentences. No "In today's
  fast-paced world" preambles.
- One idea per H2. Front-load the takeaway, then justify it.
- Use concrete examples, before/after rewrites, and checklists.
- End with a short, actionable next step — not a generic summary.

## Do
- Use "you" for the reader, "we" for Glasgow Research's experience.
- Define jargon on first use.
- Prefer specific numbers ("5–8 interviews") over vague ones ("a few").
- Link to related Glasgow Research posts where genuinely relevant.

## Don't
- No clickbait, no fear-based hooks, no emoji.
- No fabricated statistics or invented case studies.
- **Never reproduce client-identifying or confidential material.** Use
  engagement learnings only as generalised, aggregated insight.
- No keyword stuffing — write for the reader, place the term naturally.

## Formatting
- Title ≤ 60 chars, includes the primary keyword naturally.
- Meta description 140–160 chars, benefit-led.
- Sentence case headings. Markdown. No H1 in body (frontmatter owns it).

## AI clichés to avoid (Humanizer, #39)
LLM prose leans on template connectives, canned openers/closers, and
impersonal filler. The Humanizer step (`agents/humanizer.py`) strips these
deterministically and rewrites for varied rhythm + first-person voice. The
authoritative list lives in `agents/humanizer.py:AI_CLICHES` — this section
mirrors it for human readers. Never open a sentence with:

- **Canned openers:** "In today's fast-paced world", "In today's digital
  age", "In the modern world", "In an ever-evolving landscape", "In the
  world of", "When it comes to", "At the end of the day".
- **Template connectives:** "Moreover", "Furthermore", "Additionally", "In
  addition", "However, it is worth noting that", "That being said",
  "Needless to say", "Last but not least".
- **Impersonal filler / hedged authority:** "It's important to note that",
  "It's worth noting that", "It should be noted that", "It is crucial to
  understand that", "One must consider that".
- **Canned closers:** "In conclusion", "In summary", "To sum up", "All in
  all", "Ultimately".
- **Filler intensifiers:** "In essence", "Simply put".

Earn the transition instead, or just start the sentence.
