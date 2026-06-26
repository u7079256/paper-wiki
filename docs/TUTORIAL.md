# Tutorial — operating a wiki, command by command

The full workflow after bootstrap, with copy-paste examples, for **both variants**.
New here? Do [`../examples/QUICKSTART.md`](../examples/QUICKSTART.md) first (5 min, no
GPU). OCR install/use: [`OCR-SETUP.md`](OCR-SETUP.md). The why: [`METHODOLOGY.md`](METHODOLOGY.md).

## The loop (one picture)
```
research:  /wiki-init → import → /wiki-compile → /wiki-search-latest → /wiki-compile
                      → /wiki-critique → /wiki-verify-novelty → /teach
course:    /wiki-init (unpack) → OCR → /wiki-compile → /wiki-critique → /teach
```

## Conventions in this tutorial
- **“paste to Claude”** = type it into the Claude Code session **rooted at your wiki
  project folder** (so `CLAUDE.md` + the `/wiki-*` commands load).
- Slash commands resolve from the project's `.claude/` (or global) — see README
  *“Where the slash commands live”* if `/wiki-*` isn't found.
- Every command obeys the same rules: read `CLAUDE.md` first, cite, never invent,
  `raw/` is read-only. The commands are thin triggers; `CLAUDE.md` holds the schema.

---

# Part A — Research wiki (papers → concepts → gaps)

## A0. Bootstrap (once) — recap
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic mytopic -ProjectName "My Wiki" -Variant research
cd D:\my-wiki
claude            # start Claude Code here
```

## A1. `/wiki-init` — turn the template into your project
- **What:** detects the variant, asks you (via questions) for topic / submission
  target / seed papers / dataset, then fills `CLAUDE.md` + `research.md` and deletes
  the 🚧 TODO banner. Optionally kicks off the first ingest.
- **When:** exactly once, right after bootstrap. Don't re-run it.
- **Paste to Claude:** `/wiki-init`
- **You get:** a `CLAUDE.md`/`research.md` describing *your* project; the seed table
  filled in.
- **Tip:** it will **ask** — answer with real paper names + arXiv IDs; it won't
  invent your seeds.

## A2. Import sources — two ways
**(a) Born-digital paper, no GPU** (fastest; most arXiv):
```
Import arXiv:1706.03762 the no-OCR way: WebFetch the abstract to confirm the title,
WebFetch the HTML full text (ar5iv), save it to raw/mytopic/attention.md, then stop.
```
**(b) Scanned PDF / slides → OCR** (needs a GPU):
- Put the PDF(s) in `raw/mytopic/`, then run local or remote OCR — see
  [`OCR-SETUP.md`](OCR-SETUP.md). Output lands in `raw/mytopic/mineru/`.
- **Rule:** `raw/` is append-only; OCR writes there, you never hand-edit it.

## A3. `/wiki-compile` — write paper notes, then synthesize
- **What:** diffs `raw/` vs the compiled layer, reads each *new* source in full, and
  writes `wiki/papers/<id>.md` per the schema. Once ≥3 notes share a theme it writes
  a `wiki/concepts/<theme>.md`; recurring gaps become `wiki/gaps/<id>.md`
  (`novelty_verified: false`). Ends with a lint pass + a one-line log to `research.md`.
- **When:** after importing/OCR-ing new sources.
- **Paste to Claude:** `/wiki-compile`  (or `/wiki-compile mytopic` to filter)
- **You get:** new notes with frontmatter + `[[backlinks]]`; a compile summary
  (scanned / new / skipped / concepts / gaps / lint issues).
- **Tip:** it reports the diff **before** writing — you can confirm scope. It never
  recompiles an existing note unless you say “recompile”.

## A4. `/wiki-search-latest` — grow the corpus
- **What:** spawns the `wiki-searcher` sub-agent to search arXiv/web for recent work,
  dedup against what's already compiled, and return a **ranked candidate table**
  (≤10, arXiv IDs verified).
- **When:** seeds compiled but you need related work / baselines (“core ≠ everything”).
- **Paste to Claude:** `/wiki-search-latest on-policy distillation for robots`
- **You get:** a table of candidates with relevance + why.
- **Tip:** **recommendation ≠ import.** It won't download anything until you pick;
  then it fetches the chosen PDFs to `raw/` and you `/wiki-compile`.

## A5. `/wiki-compile` again — fold in the new papers
Re-run after importing the chosen candidates. Concepts get updated `related_papers`;
new gaps may surface. The wiki grows in rounds.

## A6. `/wiki-critique` — adversarial review
- **What:** spawns `wiki-critic` to attack a wiki file — overclaims, missing
  baselines, unjustified assumptions, gap claims without backing, internal
  contradictions — with severities 🔴 blocking / 🟡 weak / 🔵 suggestive.
- **When:** after writing a gap or an important concept, before you trust it.
- **Paste to Claude:** `/wiki-critique wiki/gaps/my-core-gap.md`
- **You get:** a structured critique (it cites the lines it's attacking).
- **Tip:** the critic **never edits** — it flags, you decide. Ask Claude to apply the
  🔴 fixes afterward.

## A7. `/wiki-verify-novelty` — is the gap actually open?
- **What:** spawns `wiki-novelty-verifier` to search web/arXiv/Scholar for prior work
  overlapping your gap, returning a verdict **confirmed / partial / refuted** + the
  closest neighbors.
- **When:** before investing research time in a gap.
- **Paste to Claude:** `/wiki-verify-novelty wiki/gaps/my-core-gap.md`
- **You get:** a verdict + candidate-overlap table + (if partial) the angles still open.
- **Tip:** it **won't** set `novelty_verified: true` itself — it proposes; you edit.
  It errs toward *finding* overlap, so a “confirmed” verdict is meaningful.

## A8. `/teach` — query the wiki / interactive learning
- **What:** reads `research.md` for context, greps `wiki/` for relevant notes,
  follows `[[links]]` one hop, then answers. Factual queries get a short cited
  answer; conceptual questions trigger interactive teaching. Always cites wiki
  file paths + section names and marks anything absent as “not in wiki”.
- **When:** any time you want to *use* the knowledge base (incl. new sessions).
- **Paste to Claude:** `/teach What distinguishes my approach from the nearest baseline?`
- **Tip:** if it says “not in wiki”, that source isn't compiled yet — import +
  `/wiki-compile` it (don't let it answer from general knowledge).

## A9. Consistency audit (after big expansions)
After adding many papers, ask Claude to audit: dangling `[[links]]`, orphan files,
gap-table claims vs the actual paper notes, terminology drift. Fan out one checker
per dimension. Fix what they find. (This is how a large wiki stays trustworthy.)

---

# Part B — Course wiki (lectures → topics → practice)

Same commands, course schema. `wiki-search-latest` / `wiki-verify-novelty` are **not
installed** for course projects (a course doesn't expand outward).

## B0. Bootstrap (once)
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-course -Topic mycourse -ProjectName "My Course" -Variant course
cd D:\my-course ; claude
```

## B1. `/wiki-init` — unpack + inventory
- **What:** asks what the course is + where the materials are (e.g. a
  `*resources*.zip` in the project root), **unpacks** them into `raw/mycourse/`
  (cleaning macOS junk), inventories them into `research.md`, and optionally drafts a
  `wiki/exam-scope.md` from a review/syllabus doc.
- **Paste to Claude:** `/wiki-init`
- **Tip:** it stops for your OK before mass work — confirm the plan.

## B2. OCR the slides (local or remote GPU)
Lecture PDFs → markdown. **PPTX isn't read by mineru** — convert to PDF first
(`soffice --headless --convert-to pdf`) or use `scripts/extract_pptx.py`. Full guide:
[`OCR-SETUP.md`](OCR-SETUP.md). Reminder: OCR globs `*.pdf` **directly under** the
input dir — stage subfolder PDFs flat first.

## B3. `/wiki-compile` — lecture / practice notes + topics
- **What:** reads each in-scope source and writes `wiki/lectures/<id>.md` (and
  `wiki/practice/<id>.md` for labs/assignments); ≥3 in a theme → `wiki/topics/<t>.md`.
  **Out-of-scope material is not compiled** (per `exam-scope.md`). Lab with no official
  answer → only the question is transcribed, never an invented solution.
- **Paste to Claude:** `/wiki-compile`
- **Tip:** lecture notes carry slide-page citations; formulas are LaTeX. If you have
  lecture transcripts/ASR, you can add an “in-class notes” section for intuition —
  but **never extract formulas from ASR** (use the slides).

## B4. `/wiki-critique` — catch wrong formulas / claims
```
/wiki-critique wiki/lectures/diffusion-basics.md
```
Checks the note against the source for misread formulas or overstated claims.

## B5. `/teach` — revise / look things up
```
/teach Walk me through the ELBO derivation from the diffusion lecture, with the slide it's on.
```
Answers from the compiled notes with citations; conceptual questions trigger
interactive teaching with follow-up questions.

---

# Cross-cutting rules (every command, both variants)
- **Faithful + cited.** Every claim traces to a read source; absent → “— 原文未涉及”,
  never from memory.
- **`raw/` read-only**, `wiki/` rewritable.
- **Reverse links** everywhere (`[[id]]`); lint orphans / dangling / contradictions.
- **OCR on a GPU (local or remote), never CPU**; credentials via env, never in the repo.
- The authoritative machine spec is [`llm-wiki.protocol.yaml`](llm-wiki.protocol.yaml).

# FAQ / troubleshooting
| symptom | fix |
|---|---|
| `/wiki-*` not found in a folder | it's project-scoped — you're not in a wiki project, or install commands globally (README → “Where the slash commands live”) |
| `/teach` says “not in wiki” | that source isn't compiled — import + `/wiki-compile` it |
| OCR errors / exit 2/3/4 | see [`OCR-SETUP.md`](OCR-SETUP.md) §7 |
| two projects' OCR clash | shared GPU — run them one at a time |
| `/wiki-search-latest` / `/wiki-verify-novelty` missing | you're in a **course** project; they're research-only by design |
| compile rewrote nothing | it skips already-compiled notes; say “recompile <id>” to force |
