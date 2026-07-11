---
description: Search arXiv and the web for recent research papers, then offer a verified candidate list for import.
argument-hint: <topic / keywords>
---

Run the paper-wiki `wiki-search-latest` action. This file adapts the action to a
Claude Code slash command.

1. Read `WIKI.md` first, then `research.md` and the existing paper IDs under
   `wiki/papers/`.
2. Stop for a course wiki; outward paper search is research-only.
3. Respect `lifecycle_state`: stop when `FROZEN`; when `ACTIVE`, note that this
   explicit request is an on-demand expansion.
4. Resolve the search topic from the argument or current research focus. Ask if the
   intent is genuinely ambiguous.
5. Run the verified paper-search capability using the current scope fence and a
   24-month default window unless the user supplied another range.
6. Return at most ten candidates with verified identity/URL, relevance, date, and
   a `[FENCE]` marker for excluded areas. Deduplicate against compiled papers.
7. Ask the user which candidates to import. A recommendation is never permission
   to download or write into `raw/`.
8. After approval, fetch only the selected sources, verify identity again, and
   explain whether each is ready for the `wiki-compile` action or first needs GPU
   OCR.

`WIKI.md` is the only authoritative project rules file. Do not derive behavior
from `CLAUDE.md` or `AGENTS.md`. Do not let Claude Code and Codex write this
workspace concurrently.
