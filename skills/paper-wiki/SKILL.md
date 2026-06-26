---
name: paper-wiki
description: >-
  Build and operate an "LLM Wiki" — a structured, reverse-linked knowledge base
  that Claude actively COMPILES from source PDFs / slides (research papers OR
  course materials) via remote-GPU OCR + divide-and-conquer sub-agents, writing
  faithful, no-hallucination notes with cited sources. Use when the user wants to
  turn a pile of papers or course material into a navigable wiki, bootstrap a new
  wiki project, ingest/compile sources, synthesize cross-source concepts, or run
  research novelty-gap analysis. Two variants: "research" (papers → concepts →
  novelty gaps) and "course" (lectures → topics → practice / exam revision).
---

# paper-wiki

An **LLM Wiki** is the opposite of RAG: instead of retrieving chunks at query
time, Claude proactively **reads each source page-by-page and writes** structured,
cross-linked knowledge that stays useful across sessions. This skill packages a
battle-tested workflow + scaffolding to build and run one.

## When to use
- "Turn these papers / this course into a wiki / knowledge base."
- "Bootstrap a new wiki project for <topic>."
- Running the loop: ingest → OCR → compile → concept/topic synthesis →
  (research) novelty-gap + verification, or (course) exam-scope + practice.
- Querying an existing wiki interactively (`/teach` — reads wiki, cites sources, teaches concepts).

## Two variants
| | **research** | **course** |
|---|---|---|
| sources | papers (arXiv / web) | lecture slides / labs / assignments |
| main layer | `wiki/papers/` | `wiki/lectures/` + `wiki/practice/` |
| synthesis | `wiki/concepts/` | `wiki/topics/` |
| extra | `wiki/gaps/` (novelty) | `wiki/exam-scope.md` (spine) |
| outward search | yes (`/wiki-search-latest`, `/wiki-verify-novelty`) | no (retired) |

## Start a new wiki project
Run the bootstrap — Windows `.ps1` or macOS/Linux `scripts/bootstrap_new_wiki.sh`:
```
scripts/bootstrap_new_wiki.ps1 -NewPath <abs path> -Topic <kebab-id> `
    -ProjectName "<Name>" -Variant research|course
```
It creates the project dir, copies the variant's commands + sub-agents + OCR
scripts, and renders `CLAUDE.md` / `research.md` / `README.md` from
`templates/<variant>/`. Then, in a Claude Code session **rooted at the new
project**, run `/wiki-init` to fill in the topic + seeds (research) or unpack +
inventory the materials (course).

## The core discipline (non-negotiable — this is what makes it trustworthy)
1. **`raw/` is read-only, append-only.** `wiki/` is the compiled, rewritable layer.
2. **No hallucination.** Every sentence in a note must trace to something actually
   read (cite slide page / OCR line). If a source isn't in `raw/`, don't write it
   from memory. One agent per source; read the whole thing incl. appendix.
3. **OCR runs on a GPU — local or remote, never CPU** (remote: config via env vars,
   password stays in local memory, never in the repo). Guide: `docs/OCR-SETUP.md`.
4. **Reverse links** (`[[id]]`, Obsidian-style) connect every note; lint for
   orphans, dangling links, and cross-source contradictions.
5. **Divide and conquer.** Fan out one sub-agent per source to read + write in
   parallel; then synthesize concepts/topics; then adversarially `/wiki-critique`.

## What's in this skill
(Paths are relative to the plugin root, `${CLAUDE_PLUGIN_ROOT}/`.)
- `scripts/` — `bootstrap_new_wiki.ps1`, `mineru_local_ocr.py` (local GPU),
  `mineru_remote_ocr.py` (your own SSH GPU box; env-driven, namespaced),
  `extract_pptx.py` (PPTX fallback). OCR setup: `docs/OCR-SETUP.md`.
- `commands/` — the slash commands (`/wiki-init`, `/wiki-compile`,
  `/wiki-search-latest`, `/wiki-critique`, `/wiki-verify-novelty`).
  Read-only querying is handled by `/teach` (integrated, not a separate command file).
- `agents/` — sub-agents (`wiki-searcher`, `wiki-critic`, `wiki-novelty-verifier`).
- `templates/{research,course}/` — `CLAUDE.md` / `research.md` / `README.md`.
- `templates/memory/` — placeholder memory files (GPU server, user profile, style).
- **Scope fence** — `research.md` § Scope fence defines core focus, adjacent-OK
  areas, and hard exclusions (temporal supersession or categorical). Agents respect
  it: `wiki-searcher` marks excluded candidates `[FENCE]`, `wiki-compile` pauses
  before creating concepts that cross exclusion boundaries. Paired with a lifecycle
  state (`BUILDING` / `ACTIVE` / `FROZEN`) that tracks wiki maturity.
- `docs/METHODOLOGY.md` — the why/how in depth.
- `docs/GOTCHAS.md` — hard-won pitfalls (Windows UTF-8/GBK, OCR non-recursive glob,
  pkill self-kill, PPTX, %%EOF truncation, …). **Read before editing scripts.**
- `docs/llm-wiki.protocol.yaml` — **machine contract**; authoritative for behavior.
  Read it to operate this skill precisely (invariants, schemas, state machine, rules).
- `examples/QUICKSTART.md` + `examples/sample-research-wiki/` — out-of-box walkthrough
  (no GPU) and a finished illustrative wiki showing the output shape.
- `docs/TUTORIAL.md` — command-by-command tutorial (research + course).
  `docs/OCR-SETUP.md` — local + remote GPU OCR setup.

## Command scope (two ways the commands appear)
- **Install the plugin** (`/plugin marketplace add u7079256/paper-wiki` →
  `/plugin install paper-wiki@paper-wiki`) → commands are global + namespaced as
  `/paper-wiki:wiki-*`, managed by Claude Code (no manual `~/.claude/` copying).
- **Bootstrap a project** → commands are project-local + un-namespaced `/wiki-*`
  (from the project's own `.claude/`). The tutorial/docs use this `/wiki-*` form.
Same commands either way. Details in README → "Where the slash commands live".

## Security
Never commit credentials. The OCR script reads host/user/password from env vars;
the real password lives only in local Claude Code memory. Server host/user are
placeholders in this skill — fill your own locally.
