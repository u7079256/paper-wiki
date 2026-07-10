# Methodology — how an LLM Wiki is built

This is the "why" behind the scaffolding. The scripts and templates just make it
repeatable.

## 1. LLM Wiki ≠ RAG
RAG retrieves chunks at query time and re-derives an answer each time. An **LLM
Wiki** does the opposite: the active coding agent (Claude Code or Codex) **reads
each source once, deeply, and writes
durable structured knowledge** — summaries, cross-source syntheses, and (for
research) novelty gaps. The compiled artifacts are the product; they outlive any
single chat and are what you query later. You pay the reading cost up front, once,
and get a navigable knowledge graph forever.

## 2. Two layers: `raw/` (read-only) → `wiki/` (compiled)
- `raw/<topic>/` holds the source material and its OCR output. **Append-only, never
  edited.** It's the ground truth.
- `wiki/` holds the compiled notes. **Rewritable**, but every claim must trace back
  to `raw/`.
This separation is what keeps the wiki from rotting into a "dirty data drawer":
sources change on their own cadence; the compiled layer is curated.

## 3. Faithful, no-hallucination compilation
The single most important rule. A note is only trustworthy if you can trust every
sentence:
- **One sub-agent per source**, read the *whole* thing (including the appendix).
- **Cite** — slide page numbers, OCR line numbers, paper sections.
- If something isn't in `raw/`, **do not** write it from background knowledge.
- Verify identity (does the downloaded PDF's title actually match what you think it
  is?) before writing a word.
- Preserve formulas as LaTeX; copy numbers and baseline names verbatim.
- For course labs/assignments: if there's no official solution, only transcribe the
  question — never invent a solution.

## 4. Divide and conquer
Sources are independent, so fan out: launch one agent per paper/lecture in
parallel, each producing its note. Then a second wave synthesizes concepts/topics
across the notes; then an adversarial pass (`wiki-critic`) hunts for holes. This is
how a 90-paper research wiki or a 25-deck course wiki gets built in one sitting.

Parallel workers remain under one coordinating runtime and own non-overlapping
files. This does not permit two independent runtimes to write the same workspace:
Claude Code and Codex must never update one wiki concurrently. Finish the active
task and inspect the working tree before handing the workspace to the other runtime.

## 5. Remote-GPU OCR pipeline
PDFs → Markdown via MinerU on a remote GPU. Local CPU OCR is banned: it's 10–30×
slower and PaddleOCR output drifts in quality, which pollutes wiki consistency.
The pipeline is one-shot (`mineru_remote_ocr.py`): upload → serial OCR → download to
`raw/<topic>/mineru/`. Credentials are env-driven; the password never touches the
repo. (For arXiv specifically, reading the HTML/LaTeX *source* can beat OCR for
math and tables — a worthwhile optimization, but OCR is the robust default and the
only option for slide decks.)

## 6. The reverse-link graph
Every note uses Obsidian-style `[[id]]` links; papers/lectures list their backlinks,
concepts/topics list `related_*` in frontmatter. This turns the wiki into a graph
(open it in Obsidian). Lint continuously for:
- **dangling links** (`[[x]]` with no `x.md`),
- **orphans** (files nothing links to),
- **contradictions** (two sources disagree → record it explicitly in the concept's
  "open problems" section, don't silently pick one).

## 7. The research shape
`papers/ → concepts/ → gaps/`. The loop:
```
wiki-search-latest  → confirm imports → remote OCR → wiki-compile
   → wiki-critique (adversarial)  → wiki-ideate (recombine methods x constraints)
   → refine the gap; user sets novelty_verified: true when confident
```
As you add rounds, the novelty boundary **narrows** — each new neighbor paper
compresses the claim. Keep the gap's "what makes this novel" honest and current;
track novelty *threats* (e.g., a same-venue accepted paper) explicitly.
`wiki-search-latest` marks excluded candidates `[FENCE]`; `wiki-compile` pauses
before concepts that cross an exclusion boundary (see section 9). After big
expansions, run a **cross-file consistency audit** (links, gap-table facts vs the
papers, concept integration, terminology/stance) — fan out auditors by dimension.

## 8. The course shape
`lectures/ + practice/ → topics/`, optionally anchored by an `exam-scope.md` spine
distilled from a review/syllabus doc that defines what's in/out of scope. Priorities:
lecture slides > labs > assignments. Optional enhancements proven useful:
- **scope discipline** — don't compile out-of-scope material into `wiki/`.
- **transcript enhancement** — if you have lecture audio/ASR, add an "in-class
  notes" section per lecture for intuition + emphasis, but **never extract formulas
  from ASR** (math gets garbled); formulas come from the slides only.
- **deep-dives** — for high-value topics, go back to the raw OCR and write full
  derivations + worked examples + exam traps.

## 9. Scope management: why wikis drift and how the fence works

Research wikis drift along two axes:

- **Task proximity.** A paper on motion generation may share baselines with your
  avatar project, but it solves a different problem. Without a boundary you end up
  compiling tangential work that dilutes the concept layer.
- **Temporal relevance.** NeRF-based methods for a task now dominated by 3DGS are
  not wrong — they're superseded. Keeping them in scope inflates the gap analysis
  with stale comparisons.

The **scope fence** in `research.md` addresses both. It has three tiers:

1. **Core focus** — 1-2 sentences anchoring what the wiki IS about. Every concept
   should trace back to this.
2. **Adjacent OK** — areas that look off-topic but ARE relevant (e.g., "head-only
   avatar: subproblem, shared baselines"). These are never flagged by any agent.
3. **Exclusions** — hard boundaries, each with a reason that distinguishes temporal
   supersession ("NeRF: superseded by 3DGS for this task") from categorical
   exclusion ("motion generation: different output modality"). `wiki-searcher`
   marks candidates from excluded areas as `[FENCE]`; `wiki-compile` pauses before
   creating concepts that cross an exclusion boundary.

The design is **exclusion-first**: anything not explicitly excluded is allowed.
This keeps the fence small and avoids false negatives from an over-specified
inclusion list. An empty fence means no filtering — backward compatible with older
projects.

The fence pairs with a **lifecycle state** (`BUILDING` → `ACTIVE` → `FROZEN`):

- **BUILDING** — actively expanding; `wiki-search-latest` runs freely.
- **ACTIVE** — using the wiki; add papers only on demand. A compile round that
  produces zero new concepts and zero new gaps suggests this transition.
- **FROZEN** — no additions unless explicitly reopened. Useful for camera-ready
  periods or archived projects.

## 10. Iterate, then audit
Build in rounds (seed → expand → synthesize). After each substantial round, audit
for consistency rather than trusting the fan-out blindly. The wiki is only as good
as its weakest unverified claim — so verify, cite, and link relentlessly.

## 11. One canonical project contract, two runtime adapters

`WIKI.md` is the only authority for the project variant, schemas, compilation
rules, scope, and prohibitions. `CLAUDE.md` and `AGENTS.md` are intentionally thin
adapters for Claude Code and Codex. Keeping business rules out of the adapters lets
both runtimes read and update the same durable wiki without semantic drift.

The query/teaching behavior is likewise part of paper-wiki (`wiki-teach`), not an
assumed external `/teach` installation.
