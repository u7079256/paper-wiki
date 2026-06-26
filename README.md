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

## Install as a skill (optional)
From **inside the cloned repo** (the GitHub repo is named `paper-wiki` — don't
hardcode that; these copy the repo's *contents*), copy it into your Claude Code
skills dir as `wiki-builder` (the `name:` in `SKILL.md`):
```
# macOS / Linux:
mkdir -p ~/.claude/skills/wiki-builder && cp -r ./. ~/.claude/skills/wiki-builder/
# Windows PowerShell:
New-Item -Type Directory -Force $HOME\.claude\skills\wiki-builder | Out-Null
Copy-Item .\* $HOME\.claude\skills\wiki-builder\ -Recurse -Force
```
Claude then discovers it via `SKILL.md`. (Per-project instead: copy into
`<project>/.claude/skills/wiki-builder/`.) **Installing the skill is optional** —
you can clone and run the bootstrap below directly without it.

## Bootstrap a new wiki project
**Windows PowerShell:**
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research      # or -Variant course
```
> First run of a downloaded `.ps1` blocked? Do once, in this shell:
> `Unblock-File .\scripts\*.ps1` — or run via
> `powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_new_wiki.ps1 ...`

**macOS / Linux:**
```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
    --name "My Wiki" --variant research            # or --variant course
```

This creates `D:\my-wiki` with `.claude/{commands,agents}`, `scripts/`, the
`raw/` + `wiki/` two-layer skeleton, and `CLAUDE.md` / `research.md` / `README.md`
rendered for the variant. Then start Claude Code **in that folder** and run
`/wiki-init`.

## Try it without a GPU (out-of-box example)
`examples/QUICKSTART.md` is a ~5-minute walkthrough that needs only Claude Code +
internet — it ingests one arXiv paper via the **no-OCR path** (Claude WebFetches +
reads the HTML), compiles a note, and queries it. `examples/sample-research-wiki/`
shows a finished (illustrative) wiki — paper notes ↔ concept ↔ gap, reverse-linked —
so you can see the output shape without running anything.

## Where the slash commands live (project vs global — read this)
Claude Code resolves slash commands and sub-agents from **two scopes**:
- **Project** — `<project>/.claude/commands/` + `agents/`: available **only inside that project folder**.
- **Global (personal)** — `~/.claude/commands/` + `~/.claude/agents/`: available in **every session, any path**.

`bootstrap_new_wiki.ps1` installs the commands **per project** (into the new
project's `.claude/`). So `/wiki-*` work **inside a bootstrapped wiki project and
nowhere else** — by design (each wiki is self-contained, and the `course` variant
deliberately ships fewer commands).

To use `/wiki-*` **everywhere** without re-bootstrapping, copy them into your
personal scope once:
```
cp commands/*.md ~/.claude/commands/    # /wiki-* in every session, any path
cp agents/*.md   ~/.claude/agents/
```
Caveat: these commands assume a wiki project layout (`raw/`, `wiki/`, `CLAUDE.md`);
run them in a non-wiki folder and they have nothing to act on.

**Skill ≠ slash commands.** Installing the *skill* (`~/.claude/skills/wiki-builder/`)
makes Claude aware of the methodology + bootstrap, but does **not** register the
`/wiki-*` slash commands — those only come from `.claude/commands/` (project or
global) as above.

## Daily use (slash commands, inside a wiki project)
| command | what it does |
|---|---|
| `/wiki-init` | one-time: fill topic + seeds (research) / unpack + inventory (course) |
| `/wiki-ask <q>` | read-only query: answers only from the compiled wiki, cites sources, says "not in wiki" when absent |
| `/wiki-compile` | read new `raw/` material → write paper/lecture notes → synthesize concepts/topics |
| `/wiki-search-latest <topic>` | (research) find recent papers to import |
| `/wiki-critique <file>` | adversarial review: holes, overclaims, wrong formulas |
| `/wiki-verify-novelty <gap>` | (research) check a claimed gap against prior work |

> Full command-by-command walkthrough (both variants): **[docs/TUTORIAL.md](docs/TUTORIAL.md)**.

## OCR — local or remote GPU (for scanned / figure-heavy PDFs)
OCR runs on a **GPU (local or remote), never CPU**. Born-digital papers can skip OCR
(the no-OCR WebFetch path). Full idiot-proof guide: **[docs/OCR-SETUP.md](docs/OCR-SETUP.md)**.
- **Local GPU:** `conda activate mineru; python scripts/mineru_local_ocr.py`
- **Remote GPU (your own SSH box):** credentials via env vars, never in the repo:
```
$env:MINERU_REMOTE_HOST = "<your gpu host>"
$env:MINERU_REMOTE_USER = "<ssh user>"
$env:MINERU_REMOTE_PASS = "<password>"   # keep in local memory, never commit
python scripts/mineru_remote_ocr.py
```
PPTX isn't read by mineru — convert to PDF first (`soffice --headless --convert-to
pdf`) or use `scripts/extract_pptx.py` (lossy).

## Security
- **No credentials in this repo.** Host/user are placeholders; the password is read
  from an env var and should live only in your local Claude Code memory.
- `templates/memory/remote-ocr-gpu-server.md.tmpl` is a **placeholder** — fill it in
  locally and never commit the filled copy. `.gitignore` guards common secret paths.

## What's inside
```
SKILL.md                    the skill entry (how Claude operates it)
scripts/                    bootstrap (.ps1 + .sh) + local/remote OCR + pptx + requirements.txt
commands/  agents/          slash commands + sub-agents
templates/{research,course} CLAUDE.md / research.md / README.md per variant
templates/memory/           placeholder memory files
docs/TUTORIAL.md            command-by-command tutorial (research + course)
docs/OCR-SETUP.md           local + remote GPU OCR, idiot-proof
docs/METHODOLOGY.md         the why/how in depth
docs/GOTCHAS.md             hard-won pitfalls (read before editing scripts)
docs/llm-wiki.protocol.yaml machine contract (authoritative behavior spec for the LLM)
examples/QUICKSTART.md      no-GPU out-of-box walkthrough
examples/sample-research-wiki/  a finished illustrative wiki (see the output shape)
```

## License
MIT — edit the holder name in `LICENSE`.

## Credits
Distilled from real builds: a World-Action-Model research wiki and an Advanced ML
course exam-revision wiki. The methodology and the gotchas come from those.
