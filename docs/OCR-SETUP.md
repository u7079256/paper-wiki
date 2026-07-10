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

Before GPU work, every input must be a regular file that starts with `%PDF-` and
ends with a trailing `%%EOF` (allowing only trailing space, tab, CR, LF, or
form-feed bytes). The scripts
record its byte size and SHA-256, then recheck both before publication. They also
reject any symlink, junction, or Windows reparse point from the project root through
`raw/<topic>/mineru/`.

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
  SSH user and mode `0700`. The remote driver has an EXIT/signal cleanup trap; the
  client also acknowledges cleanup in `finally`, and an abandoned successful driver
  self-cleans after two hours. `MINERU_NS` accepts only lowercase ASCII letters,
  digits, and underscores; anything else exits **2** before SSH. Projects still
  share the GPU, so **don't run two OCRs at once**.
- Remote processing is committed only when the driver reports `DONE` and every
  expected source contains Markdown. `FAILED`, `DEAD`, a non-numeric PID, poll
  timeout, download error, symlink, special-file entry, Windows reserved name,
  alternate-data-stream name, or Unicode/case normalization collision exits **5**
  without a committed batch marker.

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
`_paper-wiki-ocr-batch-*.pending.json` marker. Do not compile a source whose
  manifest names an invalid or missing committed marker. The marker must be a
  regular, non-link JSON file whose schema, committed resolution, batch id, and
  source PDF size/SHA-256 agree with the source manifest. Rerun the same script: it finalizes a
fully moved batch, or quarantines a partial batch in
`.paper-wiki/ocr-staging/recovery-*` and exits **5** so you can inspect it before
retrying.

Full pitfalls + the *why* behind every fix: [`GOTCHAS.md`](GOTCHAS.md).
