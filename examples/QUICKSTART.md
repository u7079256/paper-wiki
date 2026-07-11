# Quickstart — build a tiny wiki in about 5 minutes

This example uses a born-digital arXiv paper, so it needs internet access but no
GPU or OCR credentials. The generated project works in both Claude Code and Codex.

> Commands below use Windows PowerShell. On macOS/Linux, use
> `scripts/bootstrap_new_wiki.sh` with `--path --topic --name --variant`.

## 0. Optional global plugin install

Global installation is convenient but not required for a bootstrapped project.

Claude Code:

```text
/plugin marketplace add u7079256/paper-wiki
/plugin install paper-wiki@paper-wiki
```

Codex terminal:

```powershell
codex plugin marketplace add u7079256/paper-wiki
codex plugin add paper-wiki@paper-wiki
```

## 1. Bootstrap a self-contained project

From the cloned paper-wiki repository:

```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\demo-wiki -Topic demo `
    -ProjectName "Demo" -Variant research
```

The new project includes both runtime adapters:

```text
D:\demo-wiki\
├── WIKI.md
├── CLAUDE.md
├── AGENTS.md
├── research.md
├── .claude\commands\
├── .agents\skills\paper-wiki-project\SKILL.md
├── .paper-wiki\
├── raw\demo\
└── wiki\
```

`WIKI.md` is the only authoritative project rules file. `CLAUDE.md` and
`AGENTS.md` are thin adapters.

## 2. Start one runtime

Choose **one** of these; do not run both against this workspace at the same time.

Claude Code:

```powershell
cd D:\demo-wiki
claude
```

Then invoke:

```text
/wiki-init
```

Codex:

```powershell
cd D:\demo-wiki
codex
```

Then invoke either form:

```text
$paper-wiki-project wiki-init
```

```text
Initialize this project with the paper-wiki wiki-init action.
```

Answer the initialization questions with the demo topic and arXiv seed
`1706.03762` (Attention Is All You Need).

## 3. Import without OCR and compile

Ask the active runtime:

```text
Import arXiv:1706.03762 through the born-digital no-OCR path. Verify the title and
authors from the abstract, fetch a faithful HTML/LaTeX full-text source, and save it
under raw/demo/. Then run the paper-wiki wiki-compile action. Follow WIKI.md: read
the complete source, cite it, never invent, and never edit raw/ after import.
```

Explicit action forms are:

```text
# Claude Code
/wiki-compile

# Codex
$paper-wiki-project wiki-compile
```

The result should include a compiled note under `wiki/papers/` with frontmatter,
required sections, source locators, and `[[links]]`.

## 4. Query it with paper-wiki's built-in action

Claude Code:

```text
/wiki-teach What is the core contribution and what are the key components?
```

Codex:

```text
$paper-wiki-project wiki-teach What is the core contribution and what are the key components?
```

You should get an answer grounded only in the compiled wiki, with wiki paths,
section names, and source locators. Missing information is reported as
`not in wiki`. This action does not depend on a separately installed `/teach`.

## 5. Switch runtimes safely

Before moving from Claude Code to Codex or the reverse:

1. Wait for the active action and all of its workers to finish.
2. Exit or stop that runtime's task.
3. Inspect the project working tree and resolve or record incomplete changes.
4. Start the other runtime only after the workspace has a single writer again.

The second runtime reads the same `WIKI.md`, `research.md`, `raw/`, and `wiki/`, so
no export or conversion is needed.

## 6. Next actions

| Purpose | Claude Code | Codex |
|---|---|---|
| Compile new sources | `/wiki-compile` | `$paper-wiki-project wiki-compile` |
| Find recent papers | `/wiki-search-latest <topic>` | `$paper-wiki-project wiki-search-latest <topic>` |
| Adversarial review | `/wiki-critique <file>` | `$paper-wiki-project wiki-critique <file>` |
| Research ideation | `/wiki-ideate <gap>` | `$paper-wiki-project wiki-ideate <gap>` |
| Query or learn | `/wiki-teach <question>` | `$paper-wiki-project wiki-teach <question>` |

See [the full tutorial](../docs/TUTORIAL.md),
[the machine contract](../docs/llm-wiki.protocol.yaml), and
[the sample research wiki](sample-research-wiki/).
