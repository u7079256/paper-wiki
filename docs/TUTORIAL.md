# Tutorial — operate one wiki from Claude Code or Codex

This guide covers both `research` and `course` projects. Start with the
[5-minute quickstart](../examples/QUICKSTART.md) if you are new. OCR setup is in
[OCR-SETUP.md](OCR-SETUP.md); the design rationale is in
[METHODOLOGY.md](METHODOLOGY.md).

## Runtime contract

Every bootstrapped project carries both entry points:

| Runtime | Start in project root | Project action form |
|---|---|---|
| Claude Code | `claude` | `/wiki-*` |
| Codex | `codex` | `$paper-wiki-project wiki-*` or a natural-language request naming the action |

Both resolve to the same action contracts. `WIKI.md` is the **only authoritative
project rules file**; `CLAUDE.md` and `AGENTS.md` are thin runtime adapters.

> [!IMPORTANT]
> A workspace must have one writer. Never run Claude Code and Codex write actions
> concurrently. Before switching, finish the active task and inspect the working
> tree for incomplete changes.

The shared loop is:

```text
research: wiki-init → import → wiki-compile → wiki-critique → wiki-ideate
                       ↘ wiki-search-latest → wiki-compile → wiki-teach
course:   wiki-init → OCR → wiki-compile → wiki-critique → wiki-teach
```

## Action mapping

| Action | Claude Code | Codex project skill |
|---|---|---|
| Initialize | `/wiki-init` | `$paper-wiki-project wiki-init` |
| Compile | `/wiki-compile` | `$paper-wiki-project wiki-compile` |
| Search recent work | `/wiki-search-latest <topic>` | `$paper-wiki-project wiki-search-latest <topic>` |
| Critique a note | `/wiki-critique <file>` | `$paper-wiki-project wiki-critique <file>` |
| Ideate from gaps | `/wiki-ideate <gap>` | `$paper-wiki-project wiki-ideate <gap>` |
| Query or learn | `/wiki-teach <question>` | `$paper-wiki-project wiki-teach <question>` |

`wiki-teach` belongs to paper-wiki. It does not assume an external `/teach` skill
exists in either runtime.

---

# Part A — Research wiki

## A0. Bootstrap and start

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic mytopic `
    -ProjectName "My Wiki" -Variant research
cd D:\my-wiki
```

Start exactly one runtime:

```powershell
claude  # then /wiki-init
# or
codex   # then $paper-wiki-project wiki-init
```

Bootstrap creates `WIKI.md`, thin `CLAUDE.md`/`AGENTS.md` adapters, both runtime
entry points, project-local protocol docs, OCR scripts, and the `raw/` + `wiki/`
layers.

## A1. `wiki-init` — fill project identity once

The action reads the TODO banner and variant from `WIKI.md`, asks for your research
focus, target, real seed papers, datasets, and optional scope fence, then proposes
edits. After approval it fills **`WIKI.md` and `research.md`**. It does not put
business rules into `CLAUDE.md` or `AGENTS.md`.

Run this exactly once:

```text
# Claude Code
/wiki-init

# Codex
$paper-wiki-project wiki-init
```

## A2. Import sources

For a born-digital paper, ask the active runtime to verify the abstract identity,
fetch faithful HTML/LaTeX full text, and save it under `raw/<topic>/`. For scanned
or figure-heavy PDFs, put the PDF under `raw/<topic>/` and use the local or remote
GPU OCR path in [OCR-SETUP.md](OCR-SETUP.md).

`raw/` is append-only ground truth. Import may add a source; later actions never
hand-edit or delete it.

## A3. `wiki-compile` — write notes and synthesis

```text
# Claude Code
/wiki-compile

# Codex
$paper-wiki-project wiki-compile
```

The action reads `WIKI.md`, diffs `raw/` against the compiled layer, reports what
is new, then reads every eligible new source in full. OCR-derived sources are
eligible only when `_paper-wiki-ocr-complete.json` names a safe sibling
`.committed.json` batch marker that is a regular non-link/non-reparse JSON record
with schema `paper-wiki/ocr-batch/v1` and resolution `committed`. Its batch id and
exactly one source record must match the manifest's source name, PDF basename,
size, and SHA-256; pending, aborted, invalid,
linked, mismatched, or markerless OCR output is reported as blocked. The action writes `wiki/papers/` notes using
the canonical schema, cites source locators, and adds `[[links]]`. Once at least
three notes share a theme, it synthesizes a method-organized concept. Recurring
gaps are written with `novelty_verified: false`.

Workers may handle independent sources in parallel under one coordinator, but a
second Claude/Codex runtime must not write the workspace.

## A4. `wiki-search-latest` — expand deliberately

```text
# Claude Code
/wiki-search-latest on-policy distillation for robots

# Codex
$paper-wiki-project wiki-search-latest on-policy distillation for robots
```

The search action verifies identities and URLs, deduplicates against existing
papers, applies the scope fence, and returns at most ten candidates. It never
imports a recommendation until you select it. After approved import, run
`wiki-compile` again.

## A5. `wiki-critique` — attack a claim before trusting it

```text
# Claude Code
/wiki-critique wiki/gaps/my-core-gap.md

# Codex
$paper-wiki-project wiki-critique wiki/gaps/my-core-gap.md
```

The critic checks the note and underlying evidence for unsupported claims,
omissions, contradictions, and formula errors. It returns severity-tagged findings
with exact file/section evidence. Critique is read-only; repairs are a separate,
user-approved action.

## A6. `wiki-ideate` — explore grounded combinations

```text
# Claude Code
/wiki-ideate wiki/gaps/my-core-gap.md

# Codex
$paper-wiki-project wiki-ideate wiki/gaps/my-core-gap.md
```

The ideator maps constraints and method families, proposes combinations, checks
coverage holes, and separates wiki evidence, outward verification, and hypotheses.
It never sets `novelty_verified: true`; that remains the researcher's decision.

## A7. `wiki-teach` — use the compiled knowledge

```text
# Claude Code
/wiki-teach What distinguishes my approach from the nearest baseline?

# Codex
$paper-wiki-project wiki-teach What distinguishes my approach from the nearest baseline?
```

The built-in query action reads `research.md`, finds relevant wiki notes, follows
useful links, and cites wiki paths, sections, and source locators. If coverage is
missing, it says `not in wiki` and suggests what to ingest or compile; it does not
answer from general model knowledge.

## A8. Lifecycle and audits

- `BUILDING`: actively expand.
- `ACTIVE`: use the wiki and expand only on demand.
- `FROZEN`: no additions until the user reopens it.

After a large import, ask the active runtime to audit dangling links, orphan notes,
gap claims against paper notes, contradictions, and terminology drift. Delegate
independent audit dimensions when useful, then integrate under one coordinator.

---

# Part B — Course wiki

## B0. Bootstrap and start

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-course -Topic mycourse `
    -ProjectName "My Course" -Variant course
cd D:\my-course
claude  # or codex, but not both concurrently
```

Run `/wiki-init` in Claude Code or `$paper-wiki-project wiki-init` in Codex. The
action asks for the course, intended use, material location, and optional scope
document; after approval it inventories the material in `research.md` and may
draft `wiki/exam-scope.md`.

## B1. OCR lectures

PDF slides use GPU OCR. Convert PPTX to PDF with
`soffice --headless --convert-to pdf` first, or use
`scripts/extract_pptx.py` as a lossy fallback. OCR scans direct child PDFs only, so
stage nested decks in a flat temporary input. See [OCR-SETUP.md](OCR-SETUP.md).

## B2. Compile course notes

Use `/wiki-compile` or `$paper-wiki-project wiki-compile`. The course schema in
`WIKI.md` produces `wiki/lectures/`, `wiki/practice/`, and `wiki/topics/`. Material
outside `wiki/exam-scope.md` is not compiled. If an assignment has no official
answer, record only the question; never invent a solution.

## B3. Critique and learn

```text
# Claude Code
/wiki-critique wiki/lectures/diffusion-basics.md
/wiki-teach Walk through the ELBO derivation and cite the slide.

# Codex
$paper-wiki-project wiki-critique wiki/lectures/diffusion-basics.md
$paper-wiki-project wiki-teach Walk through the ELBO derivation and cite the slide.
```

`wiki-search-latest` and `wiki-ideate` are research-only and are not installed in
course projects.

---

# Updating a bootstrapped project

Windows:

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Update
```

macOS/Linux:

```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --update
```

Update mode refreshes managed Claude commands/agents, the Codex project skill,
thin adapters, manifest metadata, and vendored protocol docs. It preserves
`WIKI.md`, `research.md`, the project `README.md`, `raw/`, and `wiki/`. A legacy
Claude-only project is migrated by copying its full `CLAUDE.md` to `WIKI.md` once,
then replacing `CLAUDE.md` with a thin adapter. Conflicting variant evidence stops
the update for user review.

# Cross-cutting rules

- Read `WIKI.md` first; it is the only authority for project behavior.
- Every claim traces to a source read in full; absent means `— 原文未涉及` or
  `not in wiki`.
- `raw/` is read-only after import; `wiki/` is the compiled, rewritable layer.
- Use reverse links and lint orphans, dangling links, and contradictions.
- OCR runs on GPU, never CPU; credentials stay outside the repository.
- Keep exactly one runtime writer per workspace and verify state before handoff.

The machine-readable contract is
[llm-wiki.protocol.yaml](llm-wiki.protocol.yaml), version `llm-wiki/1.1`.

# Troubleshooting

| Symptom | Fix |
|---|---|
| Claude `/wiki-*` is missing | Start Claude Code in the bootstrapped project or install the global plugin |
| Codex cannot find `$paper-wiki-project` | Start Codex in the project root and confirm `.agents/skills/paper-wiki-project/SKILL.md` exists |
| `wiki-teach` says `not in wiki` | Import and compile the missing source; do not ask it to guess |
| OCR exits 2, 3, 4, or 5 | See [OCR-SETUP.md](OCR-SETUP.md) §7; exit 5 means processing, SSH/host-key, transfer, timeout, validation, staging, or recoverable publication failed. Any moved source without its committed batch marker is incomplete and must not be compiled |
| Two OCR jobs collide | Stop one; the projects share a GPU even when temp namespaces differ |
| Search/ideate is missing | It is intentionally unavailable in course projects |
| Runtime switch shows unexpected changes | Stop, inspect the working tree, and resolve the prior task before writing |
