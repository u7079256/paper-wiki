<p align="center">
  <h1 align="center">📚 paper-wiki</h1>
  <p align="center"><b>Drop papers in. Compile a knowledge graph.</b></p>
  <p align="center">Not RAG: Claude Code or Codex reads every page, writes cited notes, and synthesizes cross-source concepts and research gaps.</p>
</p>

<p align="center">
  <a href="https://u7079256.github.io/paper-wiki/#en"><img src="https://img.shields.io/badge/Landing_Page-blue?style=for-the-badge" alt="Landing Page"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/Claude_Code_%2B_Codex-Compatible-8A2BE2?style=for-the-badge" alt="Claude Code and Codex compatible">
</p>

<p align="center">
  <a href="README.md">中文</a> · <b>English</b> · <a href="https://u7079256.github.io/paper-wiki/#en">Landing Page</a> · <a href="docs/WALKTHROUGH.md">Walkthroughs</a> · <a href="docs/TUTORIAL.md">Tutorial</a> · <a href="examples/QUICKSTART.md">Quickstart</a>
</p>

---

## ⚡ Install

### Claude Code

Run inside Claude Code:

```text
/plugin marketplace add u7079256/paper-wiki
/plugin install paper-wiki@paper-wiki
```

Global entry point: `/paper-wiki:wiki-*`. Refresh the marketplace with
`/plugin marketplace update paper-wiki`.

### Codex

Run in a terminal:

```powershell
codex plugin marketplace add u7079256/paper-wiki
codex plugin add paper-wiki@paper-wiki
```

Global entry point: `$paper-wiki <action>`. Refresh the marketplace with
`codex plugin marketplace upgrade paper-wiki`.

You can also clone the repository and run the bootstrap script. The generated
project is self-contained and works in both Claude Code and Codex without a global
plugin installation.

---

## One wiki, two runtimes

Bootstrap creates a dual-runtime project:

- `WIKI.md` is the **single source of truth** for the variant, schemas, compile
  rules, and prohibitions.
- `CLAUDE.md` and `AGENTS.md` are thin runtime adapters that point to `WIKI.md`;
  they do not maintain separate copies of project rules.
- Claude Code uses `.claude/commands/`; Codex uses
  `.agents/skills/paper-wiki-project/SKILL.md`.
- Both runtimes share the same `research.md`, `raw/`, and `wiki/` state.

> [!IMPORTANT]
> **A workspace must have one writer.** Never let Claude Code and Codex modify the
> same wiki concurrently. Before switching runtimes, wait for the active task to
> finish and inspect the working tree for incomplete changes.

### Invocation map

| Action | Claude Code project | Codex project | Global plugin |
|---|---|---|---|
| Initialize | `/wiki-init` | `$paper-wiki-project wiki-init` | Claude `/paper-wiki:wiki-init`; Codex `$paper-wiki init` |
| Compile | `/wiki-compile` | `$paper-wiki-project wiki-compile` | Claude `/paper-wiki:wiki-compile`; Codex `$paper-wiki compile` |
| Search | `/wiki-search-latest <topic>` | `$paper-wiki-project wiki-search-latest <topic>` | Claude `/paper-wiki:wiki-search-latest`; Codex `$paper-wiki search` |
| Critique | `/wiki-critique <file>` | `$paper-wiki-project wiki-critique <file>` | Claude `/paper-wiki:wiki-critique`; Codex `$paper-wiki critique` |
| Ideate | `/wiki-ideate <gap>` | `$paper-wiki-project wiki-ideate <gap>` | Claude `/paper-wiki:wiki-ideate`; Codex `$paper-wiki ideate` |
| Query/teach | `/wiki-teach <question>` | `$paper-wiki-project wiki-teach <question>` | Claude `/paper-wiki:wiki-teach`; Codex `$paper-wiki teach` |

Codex also accepts natural language, for example: “Run the paper-wiki
`wiki-compile` action on the new sources.” `wiki-teach` is built into paper-wiki;
neither runtime needs a separate external `/teach` skill.

---

## 🧠 LLM Wiki vs RAG

```text
RAG:   query → retrieve chunks → stitch an answer → depends on chunking and recall
Wiki:  sources → read every page → compile notes → synthesize → durable knowledge graph
```

| | RAG | LLM Wiki |
|---|---|---|
| Reading time | At query time | During a full-source compile |
| Knowledge form | Vector fragments | Structured notes + reverse links |
| Cross-source synthesis | Limited | Concepts + research gaps |
| Trustworthiness | Can lose context | Every claim carries a source locator |

The core discipline: `raw/` is read-only and append-only; `wiki/` is rewritable;
every claim traces to a source that was actually read. Missing knowledge is reported
as `not in wiki`, never filled from model memory.

---

## 🔄 Workflow

```text
wiki-init → import → wiki-compile → wiki-critique → wiki-ideate
                          ↓                           ↓
                   wiki-search-latest ←── coverage gap
                          ↓
                   wiki-compile → wiki-teach
```

The `research` variant is `papers → concepts → gaps`; the `course` variant is
`lectures + practice → topics`. A scope fence controls boundaries, while
`BUILDING → ACTIVE → FROZEN` controls expansion.

---

## 🚀 Create a dual-runtime project

Windows PowerShell:

```powershell
git clone https://github.com/u7079256/paper-wiki.git
cd paper-wiki
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research   # or course
```

macOS / Linux:

```bash
git clone https://github.com/u7079256/paper-wiki.git
cd paper-wiki
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
    --name "My Wiki" --variant research        # or course
```

Then start either runtime:

```powershell
cd D:\my-wiki
claude   # then /wiki-init
# or
codex    # then $paper-wiki-project wiki-init
```

See [examples/QUICKSTART.md](examples/QUICKSTART.md) for a no-GPU example and
[docs/TUTORIAL.md](docs/TUTORIAL.md) for every action.

### Update an existing project

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Update
```

```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --update
```

Update mode refreshes managed Claude commands/agents, the Codex project skill, thin
adapters, the manifest, and vendored protocol docs. It **preserves** `WIKI.md`,
`research.md`, project `README.md`, `raw/`, and `wiki/`. On the first update of a
legacy Claude-only project, the full legacy `CLAUDE.md` is copied to `WIKI.md`
before `CLAUDE.md` becomes a thin adapter. Variant conflicts fail closed.

---

## 🔬 OCR

Scanned, slide, or figure-heavy PDFs use local or remote GPU OCR; the tools never
silently fall back to CPU. Born-digital papers can use the HTML/LaTeX no-OCR path.
Credentials stay in environment variables and never enter the repository.

See [docs/OCR-SETUP.md](docs/OCR-SETUP.md) and
[docs/GOTCHAS.md](docs/GOTCHAS.md).

---

## 📁 Repository and project layout

```text
paper-wiki/
├── .claude-plugin/              # Claude Code marketplace metadata
├── .codex-plugin/               # Codex plugin manifest
├── .agents/plugins/             # Codex marketplace metadata
├── skills/paper-wiki/           # global plugin skill
├── commands/                    # Claude slash adapters over shared actions
├── agents/                      # reviewer/searcher/ideator workers
├── templates/{research,course}/ # WIKI.md + thin adapters + project skill
├── scripts/                     # bootstrap, OCR, PPTX extraction
├── docs/                        # protocol, tutorials, methodology
└── examples/                    # quickstart and sample wiki

bootstrapped-project/
├── WIKI.md                      # canonical project rules
├── CLAUDE.md                    # thin Claude Code adapter
├── AGENTS.md                    # thin Codex adapter
├── research.md                  # shared state
├── .claude/{commands,agents}/   # Claude Code project entry points
├── .agents/skills/paper-wiki-project/SKILL.md
├── .paper-wiki/                 # manifest + vendored protocol docs
├── raw/                         # read-only source material
└── wiki/                        # maintainable compiled artifacts
```

The machine contract is [docs/llm-wiki.protocol.yaml](docs/llm-wiki.protocol.yaml),
currently `llm-wiki/1.1`.

## 📄 License

MIT

## 🙏 Credits

- [mattpocock/skills — teach](https://github.com/mattpocock/skills/tree/main/skills/productivity/teach) inspired the interactive teaching method; paper-wiki now exposes it through its own `wiki-teach` action.
- Thanks to the early adopters whose real projects shaped the methodology and gotchas.
