# claude-wiki-builder

A reusable **Claude Code skill** for building an **LLM Wiki** — a structured,
reverse-linked knowledge base that Claude *actively compiles* from your source
PDFs / slides, rather than retrieving chunks at query time (RAG).

It packages a battle-tested workflow proven across a multi-round research-paper
wiki and a course exam-revision wiki: **read every source page-by-page (remote-GPU
OCR) → write faithful, cited, no-hallucination notes (one sub-agent per source) →
synthesize cross-source concepts → adversarially review → keep the whole graph
reverse-linked and consistent.**

Two variants out of the box:
- **research** — papers → `papers/` → `concepts/` → `gaps/` (novelty analysis,
  arXiv search, novelty verification).
- **course** — lecture slides / labs / assignments → `lectures/` + `practice/` →
  `topics/` → optional `exam-scope.md` spine (exam revision).

## Install as a skill
Drop this repo into your Claude Code skills dir:
```
# personal (all projects):
cp -r claude-wiki-builder ~/.claude/skills/wiki-builder
# or per-project:
cp -r claude-wiki-builder <project>/.claude/skills/wiki-builder
```
Claude picks it up via `SKILL.md`. (On Windows, copy the folder into the same
location.)

## Bootstrap a new wiki project
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research      # or -Variant course
```
This creates `D:\my-wiki` with `.claude/{commands,agents}`, `scripts/`, the
`raw/` + `wiki/` two-layer skeleton, and `CLAUDE.md` / `research.md` / `README.md`
rendered for the variant. Then start Claude Code **in that folder** and run
`/wiki-init`.

## Daily use (slash commands, inside a wiki project)
| command | what it does |
|---|---|
| `/wiki-init` | one-time: fill topic + seeds (research) / unpack + inventory (course) |
| `/wiki-ask <q>` | read-only query: answers only from the compiled wiki, cites sources, says "not in wiki" when absent |
| `/wiki-compile` | read new `raw/` material → write paper/lecture notes → synthesize concepts/topics |
| `/wiki-search-latest <topic>` | (research) find recent papers to import |
| `/wiki-critique <file>` | adversarial review: holes, overclaims, wrong formulas |
| `/wiki-verify-novelty <gap>` | (research) check a claimed gap against prior work |

## Remote OCR (required for PDFs)
OCR runs on a remote GPU (local CPU OCR is banned for speed + consistency).
Config is **environment-variable driven so no credentials live in the repo**:
```
$env:MINERU_REMOTE_HOST = "<your gpu host>"
$env:MINERU_REMOTE_USER = "<ssh user>"
$env:MINERU_REMOTE_PASS = "<password>"   # keep in local memory, never commit
python scripts/mineru_remote_ocr.py [input_dir]
```
PPTX is not read by mineru — convert to PDF on the server
(`soffice --headless --convert-to pdf`) or use `scripts/extract_pptx.py` (lossy).

## Security
- **No credentials in this repo.** Host/user are placeholders; the password is read
  from an env var and should live only in your local Claude Code memory.
- `templates/memory/remote-ocr-gpu-server.md.tmpl` is a **placeholder** — fill it in
  locally and never commit the filled copy. `.gitignore` guards common secret paths.

## What's inside
```
SKILL.md                    the skill entry (how Claude operates it)
scripts/                    bootstrap + remote OCR + pptx fallback
commands/  agents/          slash commands + sub-agents
templates/{research,course} CLAUDE.md / research.md / README.md per variant
templates/memory/           placeholder memory files
docs/METHODOLOGY.md         the why/how in depth
docs/GOTCHAS.md             hard-won pitfalls (read before editing scripts)
```

## License
MIT — edit the holder name in `LICENSE`.

## Credits
Distilled from real builds: a World-Action-Model research wiki and an Advanced ML
course exam-revision wiki. The methodology and the gotchas come from those.
