---
description: Search arXiv and web for recent papers on a topic. Returns a candidate list to import into raw/.
argument-hint: <topic / keywords>
---

Spawn the `wiki-searcher` sub-agent via the Agent tool.

Arguments: `$ARGUMENTS`

## Your task

1. If `$ARGUMENTS` is empty → read `research.md` and ask user which direction to search, or default to the current active topic
2. Invoke `wiki-searcher` with:
   - Topic / keywords
   - Current research thread summary (from `research.md`)
   - Existing paper IDs in `wiki/papers/` (for dedup)
   - Time filter: last 24 months unless user specified

3. When agent returns the candidate table:
   - Show it to the user verbatim
   - Ask: "Which to import? I can fetch the PDFs to `raw/<topic>/` and run `/wiki-compile` after."
   - On user confirmation, use WebFetch or Bash + `wget/curl` to download PDFs

4. After download, suggest next step: `/wiki-compile`

## Hard rule
- Never import a paper the searcher just recommended without user confirmation. Recommendation ≠ permission to download.
