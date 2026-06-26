---
description: Research ideation -- discover untried method-problem combinations from wiki knowledge, or analyze a specific gap deeply.
argument-hint: [gap file path, or blank for exploratory mode]
---

Spawn the `wiki-ideator` sub-agent via the Agent tool.

Arguments: `$ARGUMENTS`

## Your task

1. **Variant check** — read `CLAUDE.md` to detect the project variant. If this is a **course** project, stop and tell the user: "Ideation is research-only. Course wikis don't have gaps or novelty analysis." Do not spawn the agent.

2. Resolve `$ARGUMENTS`:
   - File path (e.g. `wiki/gaps/xxx.md`) → validate the file exists, then spawn in **gap-focused** mode
   - Gap name (no path) → Glob `wiki/gaps/` for a match; if ambiguous, list matches and ask user to pick
   - Empty → spawn in **exploratory** mode (scan all concepts and gaps)

2.5. **Coverage check** — Count compiled papers in `wiki/papers/`. If fewer than 3, warn the user that ideation quality depends on wiki coverage and suggest `/wiki-compile` or `/wiki-search-latest` first. Do not spawn the agent unless the user explicitly confirms.

3. Invoke `wiki-ideator` with:
   - The gap file content (gap-focused) or the keyword `exploratory`
   - Background context from `research.md` (research direction + scope fence)
   - List of existing files in `wiki/papers/`, `wiki/concepts/`, `wiki/gaps/` (so the agent knows what the wiki contains)

4. When the agent returns its report:
   - Relay the full structured output to the user
   - Offer three follow-up actions:
     - **Refine an existing gap:** "Want me to update `<gap file>` with the sharper claim from the refinement section?"
     - **Create a new gap:** "Any of the untried combinations worth writing up as a new `wiki/gaps/` entry?"
     - **Import missing papers:** "The ideator identified coverage holes. Want me to run `/wiki-search-latest` to find those papers?"
   - If the user is satisfied with a gap's defensibility after this analysis, remind them: "You can set `novelty_verified: true` in the gap frontmatter whenever you're confident."

5. Only after explicit user confirmation, make any changes.

## Hard rules
- The ideator maps the landscape — it does not pass judgment. Do not summarize its output as "the gap is confirmed/refuted."
- Never set `novelty_verified: true` on behalf of the user. That's their call, after their own reading.

