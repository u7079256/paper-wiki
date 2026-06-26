<p align="center">
  <h1 align="center">📚 paper-wiki</h1>
  <p align="center"><b>Drop papers in. Get a knowledge graph out.</b></p>
  <p align="center">Not RAG — an LLM Wiki. Claude reads every page of every paper, compiles cited notes, and synthesizes cross-source concepts and research gaps.</p>
</p>

<p align="center">
  <a href="https://u7079256.github.io/paper-wiki/#en"><img src="https://img.shields.io/badge/Landing_Page-blue?style=for-the-badge" alt="Landing Page"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="MIT License"></a>
  <a href="https://claude.ai/code"><img src="https://img.shields.io/badge/Claude_Code-Plugin-8A2BE2?style=for-the-badge" alt="Claude Code Plugin"></a>
</p>

<p align="center">
  <a href="README.md">中文</a> · <b>English</b> · <a href="https://u7079256.github.io/paper-wiki/#en">Landing Page</a> · <a href="docs/WALKTHROUGH.md">Walkthroughs</a> · <a href="docs/TUTORIAL.md">Tutorial</a> · <a href="examples/QUICKSTART.md">Quickstart</a>
</p>

---

## ⚡ Install (one command)

```
/plugin marketplace add u7079256/paper-wiki
/plugin install paper-wiki@paper-wiki
```

All `/paper-wiki:wiki-*` commands are now globally available. Update later: `/plugin marketplace update paper-wiki`.

<details>
<summary>💡 SSH error? / Don't want a plugin?</summary>

**SSH host-key error**: run once:
```
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
```

**No plugin**: `git clone` this repo, run the bootstrap script — bootstrapped projects are self-contained with their own `/wiki-*` commands.
</details>

---

## 🧠 Core Concept: LLM Wiki vs RAG

```
RAG:   query → retrieve chunks → stitch answer → quality depends on chunking & recall
Wiki:  sources → read page-by-page → compile notes → synthesize concepts → knowledge graph
```

| | RAG | LLM Wiki |
|---|---|---|
| When it reads | At query time | At compile time (once) |
| Knowledge form | Vector fragments | Structured notes + reverse links |
| Cross-source synthesis | None | Auto-generated concepts + gaps |
| Trustworthiness | May decontextualize | Every claim cites its source |

---

## 🔄 Workflow at a Glance

```
/wiki-init → import papers → /wiki-compile → /wiki-critique → /wiki-ideate
                                   ↓                              ↓
                             /wiki-search-latest ←── coverage gaps ┘
                                   ↓
                             /wiki-compile → /teach (deep understanding)
```

> **Scope fence** guards boundaries: define core focus, adjacent-OK areas, and hard exclusions — agents filter automatically.
> **Lifecycle** controls pace: `BUILDING` → `ACTIVE` → `FROZEN` — the wiki knows when to stop growing.

---

## 📋 Two Variants

| | **research** | **course** |
|---|---|---|
| Sources | Papers (arXiv / web) | Slides / labs / assignments |
| Notes layer | `wiki/papers/` | `wiki/lectures/` + `wiki/practice/` |
| Synthesis | `wiki/concepts/` | `wiki/topics/` |
| Unique | `wiki/gaps/` + `/wiki-ideate` | `wiki/exam-scope.md` |
| Outward search | `/wiki-search-latest` | — |
| Scope fence | ✅ | — |

---

## 🛠️ Command Reference

| Command | What it does |
|---|---|
| `/wiki-init` | One-time setup: topic, seeds, scope fence |
| `/wiki-compile` | Compile `raw/` material → notes → concepts → gaps |
| `/wiki-search-latest <topic>` | Find recent papers (research) |
| `/wiki-critique <file>` | Adversarial review: holes, overclaims, wrong formulas |
| `/wiki-ideate <gap>` | Discover untried method-problem combinations (research) |
| `/teach <question>` | Query + interactive teaching: cross-paper tables, gap dashboards |

---

## 🚀 Quickstart (no GPU needed, ~5 min)

```powershell
# 1. Bootstrap a project
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\demo-wiki -Topic demo `
    -ProjectName "Demo" -Variant research

# 2. Open it
cd D:\demo-wiki && claude

# 3. Import a paper + compile + query (inside Claude Code)
```

Full steps: **[examples/QUICKSTART.md](examples/QUICKSTART.md)**.
Sample wiki output: **[examples/sample-research-wiki/](examples/sample-research-wiki/)**.

---

## 📖 Scenario Walkthroughs

Three researchers, three complete journeys — from init to submission:

| Scenario | Role | Duration | Focus |
|---|---|---|---|
| A | PhD student, new direction | 8 weeks | Deferred scope fence, ideate discovers direction |
| B | Senior researcher | 4 weeks | Fast validation + writing |
| C | Long-term maintainer | 6 weeks | Cross-paper reuse, `--Update` for stale commands |

Details: **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)**.

---

<details>
<summary>🏗️ Bootstrap a wiki project (details)</summary>

The bootstrap script creates a full project skeleton: `raw/` + `wiki/`, `.claude/{commands,agents}`, OCR scripts, variant templates.

**Windows PowerShell:**
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Topic my-topic `
    -ProjectName "My Wiki" -Variant research      # or -Variant course
```

**macOS / Linux:**
```bash
bash scripts/bootstrap_new_wiki.sh --path ~/my-wiki --topic my-topic \
    --name "My Wiki" --variant research            # or --variant course
```

Then start Claude Code **in that folder** and run `/wiki-init`.

**Update commands/agents in an existing project** (does not touch CLAUDE.md or research.md):
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\my-wiki -Update
```

**Command scope**: bootstrap installs project-level commands (`/wiki-*`); the plugin installs global commands (`/paper-wiki:wiki-*`). Same commands, different namespaces.
</details>

<details>
<summary>🔬 OCR Setup (scanned / figure-heavy PDFs)</summary>

OCR runs on a **GPU (local or remote), never CPU**. Born-digital papers can skip OCR (use the WebFetch path).

- **Local GPU:** `conda activate mineru; python scripts/mineru_local_ocr.py`
- **Remote GPU:** credentials via env vars, never in the repo:
  ```
  $env:MINERU_REMOTE_HOST = "<your gpu host>"
  $env:MINERU_REMOTE_USER = "<ssh user>"
  $env:MINERU_REMOTE_PASS = "<password>"
  python scripts/mineru_remote_ocr.py
  ```
- **PPTX**: convert to PDF first (`soffice --headless --convert-to pdf`) or `scripts/extract_pptx.py` (lossy).

Full guide: **[docs/OCR-SETUP.md](docs/OCR-SETUP.md)**.
</details>

<details>
<summary>🔒 Security</summary>

- **No credentials in this repo.** Host/user are placeholders; passwords come from env vars.
- `templates/memory/remote-ocr-gpu-server.md.tmpl` is a template — fill locally, never commit.
</details>

<details>
<summary>📁 What's inside</summary>

```
.claude-plugin/             plugin metadata (one-command install)
skills/paper-wiki/SKILL.md  skill entry (how Claude operates it)
scripts/                    bootstrap (.ps1 + .sh) + OCR + PPTX extraction
commands/                   slash command definitions
agents/                     sub-agents (wiki-critic / wiki-searcher / wiki-ideator)
templates/{research,course} CLAUDE.md / research.md / README.md per variant
docs/                       TUTORIAL / WALKTHROUGH / OCR-SETUP / METHODOLOGY / GOTCHAS
examples/                   QUICKSTART + sample wiki
```
</details>

---

## 📄 License

MIT

## 🙏 Credits

- 📖 [mattpocock/skills — teach](https://github.com/mattpocock/skills/tree/main/skills/productivity/teach): inspired the wiki + teach integration and pedagogical methodology
- Thanks to the early adopters and internal testers whose real-world feedback shaped the methodology and the gotchas
