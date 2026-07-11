# Gotchas — read before editing the scripts

Every item here cost real debugging time across two production builds. The
scripts already encode the fixes; this is the explanation so you don't undo them.

## Windows: GBK vs UTF-8 (the #1 footgun)
On a Chinese (or other non-UTF-8) Windows console, PowerShell defaults to GBK
(cp936). This silently corrupts UTF-8 content with non-ASCII characters.

- **`Get-Content` / `Set-Content` on UTF-8 files → corruption.** A "link
  normalization" pass once destroyed 14 Chinese wiki files this way. **Fix:** use
  your runtime's structured file tools, or `[System.IO.File]::ReadAllText/WriteAllText`
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

## Do not reintroduce `pkill -f mineru` cleanup
`pkill -f` matches the *whole* command line and can kill its own shell. Older
versions needed a narrow `[m]ineru_<ns>_driver` workaround. The current script uses
one random private root per run, a PID inside that root, a remote signal/EXIT trap,
and client-side `finally` cleanup; it does not scan or kill unrelated command lines.

## Shared GPU: namespace isolates /tmp, not the GPU
Each remote run uses a random `mktemp` root under `/tmp/mineru_<ns>_*`, verifies
that it is a real directory owned by the SSH user, and forces mode `0700`. A remote
EXIT/signal trap plus client-side `finally` cleanup replaces deterministic shared
paths. This isolates temporary files; it does **not** isolate the GPU or conda env.
**Do not run two projects' OCR at the same time.** The script also pre-checks for
≥8 GB free VRAM and exits cleanly (code 4) rather than OOM-ing or falling back to
CPU.

`MINERU_NS` is part of several remote shell paths. It therefore fails closed unless
it matches `^[a-z0-9_]+$`; do not loosen that validation. Every other dynamic remote
path or ordinary argument must pass through `shlex.quote`. The sole exception is
`MINERU_REMOTE_ACTIVATE`: it is deliberately a shell snippet, runs verbatim, and
must come only from a trusted server administrator. Never derive it from repository
content, filenames, runtime output, or chat text.

## SSH host keys are pinned, never learned on first use
Remote OCR loads the system `~/.ssh/known_hosts` and installs Paramiko
`RejectPolicy`. `AutoAddPolicy` is forbidden: silently learning a key would expose
PDFs and credentials to a machine-in-the-middle. For a new server, verify its
fingerprint with the administrator over a separate channel before adding it to
`known_hosts`. Treat a changed key as an incident; do not delete the old entry just
to make the warning disappear. `ssh-keyscan` alone does not establish identity.

## OCR output uses a recoverable batch marker
MinerU must never write directly into `raw/<topic>/mineru/`. Local rendering and
remote downloads first land under the project's `.paper-wiki/ocr-staging/` on the
same filesystem as `raw/`. The scripts require all PDF commands to succeed and at
least one Markdown file under every expected source directory. Each source manifest
names one `_paper-wiki-ocr-batch-<id>.committed.json` marker.

Moving several source directories cannot be one atomic filesystem operation. The
scripts therefore append a pending journal first, then append a separate committed
marker only after every no-replace move succeeds. The pending journal is retained
as audit history, so nothing already written under `raw/` is renamed or deleted.
If rollback fails or the process dies, an unresolved pending marker makes the batch
visibly incomplete; the next run either commits a fully moved batch or quarantines
a partial one under `.paper-wiki/ocr-staging/recovery-*` and appends an aborted
marker. Never compile a source whose named committed marker is absent.

An existing destination source is a hard conflict (exit 2), not a directory to
merge or overwrite. `download_tree` rejects symlinks, traversal names, devices,
sockets, control characters, Windows reserved/ADS names, trailing dots/spaces, and
Unicode/case normalization collisions.

## Local path and platform preflight happens before OCR
The scripts resolve every existing ancestor from the project root through
`raw/<topic>/mineru/` and staging, rejecting symlinks, junctions, and Windows reparse
points. They exercise an actual no-replace rename on the target filesystem before
GPU work. Windows and Linux are supported; macOS/BSD currently fail closed before
OCR rather than discovering unsupported publication semantics at commit time.

Local MinerU defaults to 1200 seconds per PDF; set
`MINERU_LOCAL_TIMEOUT_SECONDS` to an integer from 1 through 86400. Timeout
termination covers the whole process group/tree, not only the launcher.

## nvidia-smi can return prose, not a number
If the driver is broken, `nvidia-smi` prints an error string. Parsing it as an int
crashes. The pre-check now verifies the reply is numeric and exits 4 with the
message if not — never CPU-fallback.

## arXiv PDFs truncate silently
Direct arXiv PDF downloads sometimes cut off mid-file. The scripts require `%PDF-`
at byte zero and `%%EOF` at the end after stripping only space, tab, CR, LF, and
form-feed bytes.
They record byte size and SHA-256, then recheck both before publication. Any
framing/integrity failure aborts with code 3. Re-download (try the explicit `vN`
version URL).

## PPTX is not OCR'd by mineru
mineru reads PDF only. **Preferred:** convert on the server,
`soffice --headless --convert-to pdf deck.pptx`, then run the normal pipeline (full
fidelity). **Local fallback:** `extract_pptx.py` (python-pptx) — text + embedded
images only, **layout lost**. Note python-pptx's math→LaTeX path contains a
`print()` that crashes on a GBK console; the script reconfigures stdout to UTF-8 and
you should also set `$env:PYTHONIOENCODING = 'utf-8'`.

## Reading PDFs without a renderer
In some environments the active runtime cannot rasterize a PDF (no
`pdftoppm`/poppler). Agents then fall back to PyMuPDF/`pdftotext` text extraction — fine for text, but it
loses figures. For figure-heavy slides, the remote mineru OCR (which crops figures
to an `images/` folder) is the better source.

## Don't paste secrets into the repo
Credentials are env-driven (`MINERU_REMOTE_HOST/USER/PASS`); the password lives only
in a local, uncommitted secret store. The skill ships placeholder server details. Before any
`git push`, grep for your real host/password to be sure nothing leaked.
