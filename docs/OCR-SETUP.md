# OCR setup — turn PDFs into Markdown (local or remote GPU)

Idiot-proof guide. Two ways to OCR your PDFs: a **local** GPU or a **remote** GPU
(your own SSH box). Plus when you can skip OCR entirely.

## 0. Do you even need OCR?
- **Born-digital papers** (most arXiv) → you can **skip OCR**: use the no-OCR path in
  [`examples/QUICKSTART.md`](https://github.com/u7079256/paper-wiki/blob/main/examples/QUICKSTART.md) (the active Claude Code or
  Codex runtime fetches and reads a faithful HTML/source representation). Fastest,
  zero setup.
- **Scanned PDFs / slide decks / figure-heavy docs** → OCR gives you clean text +
  cropped figures. Use a GPU (below).

## 1. The one rule
OCR runs on a **GPU — local or remote. Never CPU.** CPU OCR is 10–30× slower and its
quality drifts, which pollutes the wiki. No GPU anywhere? Use the no-OCR path above.

OCR output is also **append-only**. Both scripts render or download into
`.paper-wiki/ocr-staging/`, require Markdown for every expected PDF source, write
`_paper-wiki-ocr-complete.json` inside each source, and publish a batch journal
under `raw/<topic>/mineru/`. This is a **recoverable marker protocol**, not a claim
that several directory moves are one filesystem-atomic operation. A source counts
as committed only when the `.committed.json` marker named by its manifest exists.
If a process stops mid-publish, the `.pending.json` marker prevents those directories
from being treated as a complete batch. The next run either completes a fully moved
batch or moves a partial batch back into `.paper-wiki/ocr-staging/recovery-*` and
appends an aborted marker. The pending journal is retained as append-only audit
history after either resolution.

New output uses completion schema `paper-wiki/ocr-completion/v2` and batch schema
`paper-wiki/ocr-batch/v2`. Both bind the same
`paper-wiki/ocr-content/v1` fingerprint: every regular single-link source file
except the completion manifest itself is listed by portable relative path, byte
size, and lowercase SHA-256, and the canonical list is bound by `tree_sha256`.
Legacy v1 output has no content binding. Recovery safely recognizes and skips an
already-resolved, matching regular pending+committed/aborted v1 audit pair so it
does not block unrelated new OCR, but an unresolved or malformed v1 journal fails
closed. Compilation still blocks every v1 source; rerun OCR or use a separately
reviewed reseal procedure before compiling it.

Before GPU work, every input must be a regular, single-link file that starts with
`%PDF-` and ends with a trailing `%%EOF` (allowing only trailing space, tab, CR,
LF, or form-feed bytes). Before reading its first byte, the scripts bind a no-follow
file handle to the pre/post `lstat` identity and verify every input-directory
ancestor. From that same handle they create a mode-`0600` private staging snapshot;
MinerU and SFTP consume only that snapshot, never the mutable original path. The
scripts record its byte size and SHA-256, then re-open the original only for a
provenance recheck before publication. They reject hard links, symlinks, junctions,
Windows reparse points, or identity changes at every boundary. For a PDF already
under `raw/<topic>/` but outside `mineru/`, the completion
manifest and batch record also store its safe project-relative path as
`source_pdf_project_path`; `wiki-compile` reopens that PDF and verifies its current
basename, size, and SHA-256 before reading OCR text. A PDF supplied from an external
flat staging directory records this field as `null` for backward-compatible staging;
compile reports that its current PDF hash could not be revalidated.

Remote downloads never reopen an inspected path with `get(path)`. Each directory
is `lstat`-bound before and after listing; each file is opened as an SFTP handle,
matched by pre/post `lstat` and handle `fstat`, copied to a mode-`0600` local temp
file, checked again, then atomically published inside staging. Staging cleanup also
binds the parent and run-directory identities; POSIX cleanup uses no-follow
directory handles and relative deletion, while an identity mismatch deliberately
retains data instead of risking an out-of-tree delete.

## 2. Which path?
```
local NVIDIA GPU on this machine? ──► Option A (local)
remote GPU box over SSH?          ──► Option B (remote, your own server)
neither?                          ──► no-OCR WebFetch path (born-digital only)
```

## 3. One-time install of MinerU
For **local**, install on this machine. For **remote**, do this **on the server**.
```
conda create -n mineru python=3.10 -y
conda activate mineru
pip install "torch==2.4.1" --index-url https://download.pytorch.org/whl/cu121
pip install "mineru[core]==3.1.0"
```
> ⚠️ `mineru[core]` may pull a **cu13 cuDNN** that breaks torch. If OCR errors on
> cuDNN, purge the non-cu12 `nvidia-*` wheels and reinstall the torch line above.
> torch is pinned to **2.4.1+cu121** on purpose (2.5.x is blacklisted; 2.11 needs
> driver 580+). Full reasons: [`GOTCHAS.md`](GOTCHAS.md).

> **Script deps** (on the machine that *runs* the scripts, not the GPU server):
> `pip install -r scripts/requirements.txt` — i.e. `paramiko` for remote OCR
> orchestration, plus `python-pptx` if you use the PPTX fallback.

## 4. Option A — Local GPU (傻瓜式)
```
conda activate mineru
cd <your wiki project>
python scripts/mineru_local_ocr.py
```
- Defaults to `raw/<topic>/`. Processes `*.pdf` **directly under** it. PDFs in
  subfolders (`lecture-slides/`, …)? Stage them flat into one temp dir first — the
  script tells you if it finds none.
- Output → `raw/<topic>/mineru/<name>/auto/<name>.md` (+ `images/`). Then run the
  `wiki-compile` action.
- **Refuses to run without a GPU** (it will not silently use CPU).
- Each MinerU process has a 1200-second default timeout. Override it with
  `MINERU_LOCAL_TIMEOUT_SECONDS` (1–86400); timeout kills the whole process
  group/tree and exits **5** for the batch.
- If `raw/<topic>/mineru/<name>/` already exists, the script exits **2** before
  starting MinerU. Existing sources are never merged, replaced, or repaired in place.
- Publication currently supports Windows and Linux. macOS/BSD fail closed during
  the no-replace preflight, before MinerU starts.

## 5. Option B — Remote GPU (傻瓜式; YOUR OWN server)
You bring your **own** SSH host + user + password. Nothing is shared. Inject
credentials through the current shell environment or an OS secret store; never
write the password to the repo or a client memory file.

1. **One-time:** install MinerU on the server (the conda block in §3).
2. **Verify and trust the SSH host key.** The script loads your system
   `~/.ssh/known_hosts` and uses Paramiko `RejectPolicy`; it never auto-adds a key.
   Verify the server fingerprint with its administrator through a separate channel,
   then connect once with your system `ssh` client and accept it only if the verified
   fingerprint matches. An unknown or changed key fails closed with exit **5**.
   `ssh-keyscan` by itself discovers a key but does **not** verify its identity.
3. **Set credentials** (PowerShell):
   ```powershell
   $env:MINERU_REMOTE_HOST = "your.server.or.ip"
   $env:MINERU_REMOTE_USER = "youruser"
   $env:MINERU_REMOTE_PASS = "<password>"
   # optional namespace override; must match ^[a-z0-9_]+$ exactly:
   $env:MINERU_NS = "my_wiki"
   # optional — only if your conda isn't ~/miniconda3 or your env isn't named 'mineru':
   $env:MINERU_REMOTE_ACTIVATE = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate mineru"
   ```
   bash/zsh: use `export VAR=...` instead of `$env:`.
   `MINERU_REMOTE_ACTIVATE` is the one intentional shell-code trust boundary: it is
   executed verbatim on the server. Only a trusted administrator should provide it;
   never copy project content, a PDF filename, chat output, or other untrusted text
   into this variable. All ordinary remote paths and arguments are shell-quoted.
4. **Run:**
   ```
   python scripts/mineru_remote_ocr.py
   ```
   Uploads your PDFs → runs OCR serially on the server → downloads markdown to
   `raw/<topic>/mineru/`. Then run the `wiki-compile` action.

- Keep the password in an **OS secret store** or inject it into the current shell.
  [`templates/memory/remote-ocr-gpu-server.md.tmpl`](https://github.com/u7079256/paper-wiki/blob/main/templates/memory/remote-ocr-gpu-server.md.tmpl)
  is for non-secret host/environment notes only; never put the password in it.
- Every run gets a random `mktemp` root matching `/tmp/mineru_<ns>_*`, owned by the
  SSH user and mode `0700`. Before the first PDF upload, the client starts an
  owner-only guardian in a separate session. If upload is abandoned and cleanup
  cannot reconnect, the guardian removes the workspace after a two-hour WAITING
  TTL. Driver and guardian identities bind a random run token to Linux `boot_id`,
  PID, `/proc` start time, and SID. The handoff is written through a private temp
  file, `fsync`, and atomic rename; every observation and signal revalidates the
  complete identity, so a stale/reused PID is never signalled. After handoff, a
  PDF-count-derived hard lease (capped at 24 hours) still applies. Activation is
  limited to five minutes; each MinerU process uses a 1200-second timeout plus a
  30-second forced-kill grace. The driver has EXIT/signal cleanup, the client
  acknowledges cleanup in `finally`, and completed results retain a two-hour TTL.
  `MINERU_NS` accepts only lowercase ASCII letters,
  digits, and underscores; anything else exits **2** before SSH. Projects still
  share the GPU, so **don't run two OCRs at once**.
- Remote processing is committed only when the driver reports `DONE` and every
  expected source contains Markdown. `FAILED`, `DEAD`, a non-numeric PID, poll
  timeout, download error, symlink, special-file entry, Windows reserved name,
  alternate-data-stream name, or Unicode/case normalization collision exits **5**
  without a committed batch marker.
- Timeout cleanup is session-wide. Local POSIX cleanup proves the dedicated PGID
  has no live members; the remote guardian and reconnect cleanup retain the
  authenticated SID and still TERM/KILL remaining children if the driver leader
  exits first. PID/starttime, boot ID, token, and SID mismatches are never signaled.

## 6. Out-of-box example (start to finish)
```powershell
# 1. make a project (research variant)
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\ocr-demo -Topic demo -ProjectName "Demo"
# 2. drop a scanned PDF into raw/demo/  (e.g. copy some paper.pdf there)
# 3a. LOCAL:
conda activate mineru
python D:\ocr-demo\scripts\mineru_local_ocr.py D:\ocr-demo\raw\demo
# 3b. or REMOTE (your server):
$env:MINERU_REMOTE_HOST="your.ip"; $env:MINERU_REMOTE_USER="you"; $env:MINERU_REMOTE_PASS="<password>"
python D:\ocr-demo\scripts\mineru_remote_ocr.py D:\ocr-demo\raw\demo
# 4. raw/demo/mineru/paper/auto/paper.md is ready -> start one runtime in D:\ocr-demo and run wiki-compile
```

## 7. Troubleshooting (both paths)
| symptom | meaning / fix |
|---|---|
| exit **2** | missing env/config, or input dir not found |
| exit **3** | invalid/unstable PDF: missing `%PDF-`, missing trailing `%%EOF`, link/reparse input, or file changed during hashing |
| exit **4** | GPU unavailable / occupied / driver fault — fix it; **do not CPU-fallback** |
| exit **5** | processing, SSH/host-key, transfer, timeout, path/platform validation, staging, or recoverable batch commit failed; no `.committed.json` marker means the batch is not complete |
| cuDNN error | the cu13 pollution (§3 warning) — purge non-cu12 `nvidia-*`, reinstall torch |
| `mineru` not found | `conda activate mineru` first (local), or install it |
| `.pptx` won't OCR | convert first: `soffice --headless --convert-to pdf deck.pptx` (on the GPU machine), then OCR; local fallback `scripts/extract_pptx.py` |

An existing `raw/<topic>/mineru/<name>/` is an append-only conflict and also exits
**2**. Keep the existing source untouched; process only a new PDF basename. A hard
machine shutdown can leave `.paper-wiki/ocr-staging/` data or a
`_paper-wiki-ocr-batch-*.pending.json` marker. Before it reads a completion manifest
or OCR Markdown, `wiki-compile` rejects a source, ancestor, manifest, listed
Markdown, or marker that is linked, reparsed, non-regular, hard-linked, or resolves
outside its allowed root. It then requires v2 completion/batch records with the
same content fingerprint. Immediately before any OCR body read, it copies every
declared file through an identity-bound no-follow handle into a new owner-only
snapshot, hashes while copying, and accepts only the exact file set, per-file
size/SHA-256, and canonical tree digest; it reads only that snapshot. A
project-relative PDF is rehashed as described above. Rerun the same script after
an interrupted publish: it finalizes a fully moved batch only after revalidating
the pending schema, backend, every typed provenance field, content fingerprint,
current project PDF, and any existing resolution marker. Resolution is published
from the verified in-memory record and the source fingerprint is recomputed at the
commit boundary. Invalid or conflicting committed/aborted markers fail closed. A partial batch
is quarantined in
`.paper-wiki/ocr-staging/recovery-*` and exits **5** so you can inspect it before
retrying.

Full pitfalls + the *why* behind every fix: [`GOTCHAS.md`](GOTCHAS.md).
