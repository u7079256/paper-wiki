---
description: Compile raw/ source material into the structured wiki/ knowledge base. Follows the compile rules in CLAUDE.md, which define this project's variant + schema.
argument-hint: [optional: subfolder filter | recompile <paper-id> | recompile all]
---

You are the **LLM Wiki compiler**. Read `raw/` and write structured knowledge to
`wiki/`. The exact schema and target dirs are **variant-specific and defined in
CLAUDE.md** (research → `papers/`·`concepts/`·`gaps/`; course → `lectures/`·`topics/`·
`practice/`). Read CLAUDE.md first and follow it; this command is just the skeleton.

## Workflow

### Step 1 — Load context
1. `CLAUDE.md` — the "Claude Code 编译规则" section (schema + note templates for this variant)
2. `research.md` — current thread / scope
3. List the compiled layer to see existing state

### Step 2 — Diff raw/ vs compiled
List sources under `raw/<topic>/mineru/` (+ any `.md`). For each, check whether a
corresponding note already exists with `status: compiled`. **New** → queue;
**already compiled** → skip unless the user says "recompile". (course variant: first
decide in/out of scope per CLAUDE.md / `exam-scope.md` — don't compile out-of-scope.)
Report the diff to the user before starting.

If `raw/<topic>/` contains `.pdf` files with no corresponding `mineru/` output or `.md` sibling, note these to the user: these PDFs need OCR or HTML re-fetch before they can be compiled.

### Step 3 — Per-source compilation
For each new source:
1. Read the **whole** source (incl. appendix / all slides).
2. Write its note following the CLAUDE.md template for this variant.
3. **Cite** (slide page / OCR line / paper section). **Never invent** — if a section
   is absent, write "— 原文未涉及" rather than guess.
4. Use `[[...]]` for cross-links to other notes.

### Step 3.5 — Scope fence check (research variant only)
Read `research.md` § Scope fence (if present). Individual paper compilation is
**never blocked** by the fence. But when creating a **new** concept that touches an
Exclusion area, **pause and ask the user** before writing it — the exclusion may be
intentional. Adjacent OK areas are never flagged; no fence section = skip this step.

### Step 4 — Synthesis
When ≥3 notes share a theme, create/update the synthesis article (concept for
research, topic for course) per CLAUDE.md — organized **by method, not by source**.
Keep it concise (quality over completeness).

### Step 5 — (research variant only) Gaps
Per CLAUDE.md §gap: when a gap recurs across ≥2 sources or is called out in
`research.md`, write/update `wiki/gaps/<gap_id>.md` with `novelty_verified: false`
(verify later via `/wiki-ideate`).

### Step 6 — Lint
- **Orphans** (nothing links to them) → add `tags: [orphan]`
- **Dangling links** (`[[x]]` with no `x.md`) → list them
- **Contradictions** (sources disagree) → record in the synthesis "开放问题 / 易混点"
- `compiled_at` = today's actual date

### Step 7 — Update research.md
Append one compile-log line under the progress section; do NOT overwrite other parts:
```
- [YYYY-MM-DD] 编译 N 篇新材料,新增/更新 M 个 concept/topic(,识别 K 个 gap)
```

## Report
```
=== Wiki Compile Summary ===
- Scanned: <N>   New: <ids>   Skipped: <count> (force redo: /wiki-compile recompile <id>)
- Synthesis updated: <list>   (Gaps: <list>)
- Lint issues: <count, list critical>
- New concepts: X.  New gaps: Y.
  If lifecycle_state is BUILDING and both are zero, suggest transitioning to
  ACTIVE (the user decides; don't change it unilaterally).
```

## Hard constraints
- ❌ Never modify `raw/` (read-only)
- ❌ Never fabricate content not present in the source
- ❌ No external tools for content (only what's in `raw/`)
- ✅ Unsure → write "— 原文未涉及" rather than guess
- ✅ Chinese for summaries (match the user's language); technical terms stay English
