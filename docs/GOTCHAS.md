# Gotchas — read before editing the scripts

Every item here cost real debugging time across two production builds. The
scripts already encode the fixes; this is the explanation so you don't undo them.

## Windows: GBK vs UTF-8 (the #1 footgun)
On a Chinese (or other non-UTF-8) Windows console, PowerShell defaults to GBK
(cp936). This silently corrupts UTF-8 content with non-ASCII characters.

- **`Get-Content` / `Set-Content` on UTF-8 files → corruption.** A "link
  normalization" pass once destroyed 14 Chinese wiki files this way. **Fix:** use
  Claude's Read/Edit/Write tools, or `[System.IO.File]::ReadAllText/WriteAllText`
  with `[System.Text.UTF8Encoding]`. `bootstrap_new_wiki.ps1` does all template I/O
  via .NET UTF-8 for exactly this reason.
- **Running a `.ps1` that contains non-ASCII text → parse error** ("string is
  missing the terminator") because PS reads the file as GBK. **Fix (chosen here):**
  keep `.ps1` bodies **ASCII-only**; put all non-ASCII text in `.tmpl` *data* files
  read via .NET UTF-8. (Alternative: save the `.ps1` as UTF-8 **with BOM**.)

## BOM duality: scripts want it, frontmatter forbids it
- A `.ps1` with non-ASCII needs a UTF-8 **BOM** to parse under Windows PowerShell 5.1.
- But a Claude Code command/agent **`.md` with a BOM breaks its YAML frontmatter** —
  the `---` is no longer at byte 0, so the slash-command **description renders as
  `---`** in the menu. **Fix:** command/agent `.md` files must be **BOM-less** UTF-8.
  If you ever bulk-add BOMs, exclude `.claude/commands` and `.claude/agents`.

## OCR script only globs the input dir's *direct* children
`mineru_remote_ocr.py` processes `*.pdf` **directly under** `input_dir`, not
recursively. Course materials live in subfolders (`lecture-slides/`, `lab/`, …), so
**stage all PDFs flat into one temp dir** (filenames are usually unique) and point
the script there, or run it once per subfolder.

## `pkill -f mineru` kills its own shell
`pkill -f` matches the *whole* command line. A cleanup line like
`pkill -9 -f mineru; ...; mkdir ...` SIGKILLs the very shell running it (its command
line contains "mineru"), so `mkdir` never runs and the upload dir is missing
(`sftp.put` → ENOENT). **Fix:** narrow the pattern to this project's driver and use a
`[m]` bracket so it can't match the pkill command itself:
`pkill -9 -f '[m]ineru_<ns>_driver'`. Also split it off from `rm`/`mkdir`.

## Shared GPU: namespace isolates /tmp, not the GPU
The `mineru_<ns>_*` namespace stops two projects from `rm -rf`-ing each other's
`/tmp` upload dir. It does **not** isolate the GPU or conda env. **Do not run two
projects' OCR at the same time** — one build had its uploaded batch wiped when a
second session started the same script and `rm -rf`'d the shared namespace before
the fix. The script also pre-checks for ≥8 GB free VRAM and exits cleanly (code 4)
rather than OOM-ing or falling back to CPU.

## nvidia-smi can return prose, not a number
If the driver is broken, `nvidia-smi` prints an error string. Parsing it as an int
crashes. The pre-check now verifies the reply is numeric and exits 4 with the
message if not — never CPU-fallback.

## arXiv PDFs truncate silently
Direct arXiv PDF downloads sometimes cut off mid-file. A valid PDF ends with a
`%%EOF` marker; the script checks for it and aborts (code 3) on a truncated file so
OCR doesn't throw mid-run. Re-download (try the explicit `vN` version URL).

## PPTX is not OCR'd by mineru
mineru reads PDF only. **Preferred:** convert on the server,
`soffice --headless --convert-to pdf deck.pptx`, then run the normal pipeline (full
fidelity). **Local fallback:** `extract_pptx.py` (python-pptx) — text + embedded
images only, **layout lost**. Note python-pptx's math→LaTeX path contains a
`print()` that crashes on a GBK console; the script reconfigures stdout to UTF-8 and
you should also set `$env:PYTHONIOENCODING = 'utf-8'`.

## Reading PDFs without a renderer
In some environments Claude's Read tool can't rasterize a PDF (no `pdftoppm`/poppler).
Agents then fall back to PyMuPDF/`pdftotext` text extraction — fine for text, but it
loses figures. For figure-heavy slides, the remote mineru OCR (which crops figures
to an `images/` folder) is the better source.

## Don't paste secrets into the repo
Credentials are env-driven (`MINERU_REMOTE_HOST/USER/PASS`); the password lives only
in local Claude Code memory. The skill ships placeholder server details. Before any
`git push`, grep for your real host/password to be sure nothing leaked.
