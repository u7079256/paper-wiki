---
description: Spawn the wiki-critic sub-agent to adversarially review a wiki file or concept. Finds holes, overclaims, and missing counter-evidence.
argument-hint: <file path or concept name>
---

Spawn the `wiki-critic` sub-agent via the Agent tool.

Arguments: `$ARGUMENTS`

## Your task

1. Resolve `$ARGUMENTS` to a concrete target:
   - If it's a file path (e.g. `wiki/papers/<id>.md` or `wiki/lectures/<id>.md`) → use it directly
   - If it's a concept / topic name → Glob `wiki/` for matching files, pick the best match, confirm with user if ambiguous
   - If empty → ask user what to critique

2. Invoke the `wiki-critic` sub-agent with a self-contained prompt:
   - Target file path
   - The user's overall research direction (summarize from `research.md` in 2-3 lines)
   - Ask for the structured critique output defined in the agent's system prompt

3. When the agent returns:
   - Relay the critique to the user verbatim (it's the agent's direct analysis)
   - Offer to act on 🔴 Blocking issues: "Want me to rewrite section X to fix these?"
   - Do NOT edit wiki files yourself without user approval — critic flagged it, user decides

## Example invocation

```
Target: wiki/gaps/<gap-id>.md
Critique focus: novelty claim and proposed approach
```
