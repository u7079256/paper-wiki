---
description: Search arXiv and web for recent papers on a topic. Returns a candidate list to import into raw/.
argument-hint: <topic / keywords>
---

Spawn the `wiki-searcher` sub-agent via the Agent tool.

Arguments: `$ARGUMENTS`

## Your task

### Step 0 — variant + lifecycle check
1. Read CLAUDE.md to detect variant. If course: stop and say "Paper search is research-only. Course wikis don't expand outward."
2. Read research.md lifecycle_state. If FROZEN: stop and say "The wiki is frozen. Set lifecycle_state to ACTIVE in research.md to re-enable search." If ACTIVE: warn "The wiki is in ACTIVE state (expand on demand only). Proceeding with your explicit request."

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
   - If the agent returns an ambiguity error about the topic, relay it to the user and ask for clarification before re-invoking.

4. After downloading PDFs, guide the user:
   - For born-digital arXiv papers: re-fetch as HTML via ar5iv (WebFetch the HTML full text and save as `.md` to `raw/<topic>/`) — compile sees `.md` files directly.
   - For scanned/figure-heavy PDFs: run OCR first (see `docs/OCR-SETUP.md`) so output lands in `raw/<topic>/mineru/`.
   - Then run `/wiki-compile`.

## Hard rule
- Never import a paper the searcher just recommended without user confirmation. Recommendation ≠ permission to download.
