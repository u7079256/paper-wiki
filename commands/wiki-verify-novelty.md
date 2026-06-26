---
description: Verify a research gap's novelty by searching web, arXiv, Google Scholar. Run before investing research time on a proposed idea.
argument-hint: <gap file path, e.g. wiki/gaps/<gap-id>.md>
---

Spawn the `wiki-novelty-verifier` sub-agent via the Agent tool.

Arguments: `$ARGUMENTS`

## Your task

1. Resolve `$ARGUMENTS`:
   - File path → use directly
   - Gap name → Glob `wiki/gaps/` for match
   - Free-form description → pass directly to agent as the claim
   - Empty → list `wiki/gaps/` files and ask user to pick

2. Invoke `wiki-novelty-verifier` with:
   - The gap file content (or free-form claim)
   - Background context from `research.md`
   - Existing papers in `wiki/papers/` (agent should check if overlap already in wiki before web-searching)

3. When agent returns verdict:
   - Relay the full structured output to user
   - If verdict is **confirmed** → offer: "Update the gap file to set `novelty_verified: true` with today's date?"
   - If verdict is **partial** → offer: "Narrow the gap claim to the remaining open angles. Want me to propose a rewrite?"
   - If verdict is **refuted** → offer: "Move this gap file to `wiki/gaps/archived/`? Or keep as a record of explored directions?"

4. Only after explicit user confirmation, make the change.

## Hard rule
- Novelty verification is advisory. Even "confirmed" doesn't mean 100% — tell user to still do one manual Google Scholar sanity check before committing research time.
