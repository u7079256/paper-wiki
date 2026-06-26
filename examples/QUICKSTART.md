# Quickstart — build a tiny wiki in ~5 minutes (no GPU needed)

This walkthrough proves the skill works **out of the box**. It uses the **no-OCR
ingest path** (Claude WebFetches a paper's HTML and reads it directly), so you need
only Claude Code + internet — **no remote GPU, no credentials**. Set up remote OCR
later, only when you have scanned/large PDFs (see `../docs/GOTCHAS.md`).

> Windows PowerShell shown. On macOS/Linux, swap the bootstrap for the equivalent
> `mkdir`/`cp` or port `scripts/bootstrap_new_wiki.ps1` (it's ~80 lines).

## 0. Install the skill (optional but recommended)
```powershell
Copy-Item -Recurse .\  $HOME\.claude\skills\wiki-builder   # makes Claude aware of the methodology
```

## 1. Bootstrap a throwaway research wiki
```powershell
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\demo-wiki -Topic demo -ProjectName "Demo" -Variant research
```
You now have `D:\demo-wiki\` with `raw/demo/`, `wiki/{papers,concepts,gaps,...}`,
`.claude/{commands,agents}`, and a templated `CLAUDE.md` / `research.md`.

## 2. Start Claude Code in the new folder
```powershell
cd D:\demo-wiki
claude            # or open this folder in your Claude Code IDE
```
`CLAUDE.md` + the `/wiki-*` commands load automatically.

## 3. Ingest one paper WITHOUT OCR (the out-of-box path)
Paste this to Claude:
```
Import arXiv:1706.03762 the no-OCR way:
1. WebFetch https://arxiv.org/abs/1706.03762 to confirm the title/authors.
2. WebFetch the HTML full text (try https://ar5iv.org/abs/1706.03762) and save the
   extracted text to raw/demo/attention-is-all-you-need.md (raw is append-only).
3. Then run /wiki-compile.
Follow CLAUDE.md: read the whole thing, cite, never invent.
```
Claude reads the real source (so the note is faithful, not from memory) and writes
`wiki/papers/attention-is-all-you-need.md` following the paper schema.

## 4. Query it (read-only)
```
/wiki-ask What is the core contribution and what are the key components?
```
You get an answer grounded **only** in the compiled note, with citations; anything
not in the wiki is reported as "not in wiki".

## 5. What you should see
```
D:\demo-wiki\
├── raw\demo\attention-is-all-you-need.md          # the fetched source (read-only)
└── wiki\papers\attention-is-all-you-need.md       # the compiled note (frontmatter + sections + [[links]])
```
Add 2–3 more papers the same way, then ask Claude to `/wiki-compile` again — once
≥3 share a theme it will synthesize a `wiki/concepts/<theme>.md`. That's the loop.

## See a finished example without running anything
`examples/sample-research-wiki/` is a small **illustrative** wiki (synthetic content,
clearly labeled) showing the final shape: paper notes ↔ concept ↔ gap, all
reverse-linked. Open it in Obsidian to see the graph.

## Going further
- Real corpora / scanned PDFs → set up remote OCR (`CLAUDE.md` → "远程 OCR 入库管线";
  credentials via env vars, never in the repo).
- Course materials instead of papers → bootstrap with `-Variant course`.
- The full machine-readable contract Claude follows: `docs/llm-wiki.protocol.yaml`.
