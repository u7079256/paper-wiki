# OCR setup — turn PDFs into Markdown (local or remote GPU)

Idiot-proof guide. Two ways to OCR your PDFs: a **local** GPU or a **remote** GPU
(your own SSH box). Plus when you can skip OCR entirely.

## 0. Do you even need OCR?
- **Born-digital papers** (most arXiv) → you can **skip OCR**: use the no-OCR path in
  [`examples/QUICKSTART.md`](../examples/QUICKSTART.md) (Claude WebFetches + reads the
  HTML/source directly). Fastest, zero setup.
- **Scanned PDFs / slide decks / figure-heavy docs** → OCR gives you clean text +
  cropped figures. Use a GPU (below).

## 1. The one rule
OCR runs on a **GPU — local or remote. Never CPU.** CPU OCR is 10–30× slower and its
quality drifts, which pollutes the wiki. No GPU anywhere? Use the no-OCR path above.

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
> `pip install paramiko` for remote OCR orchestration; `pip install python-pptx`
> only if you use the PPTX fallback.

## 4. Option A — Local GPU (傻瓜式)
```
conda activate mineru
cd <your wiki project>
python scripts/mineru_local_ocr.py
```
- Defaults to `raw/<topic>/`. Processes `*.pdf` **directly under** it. PDFs in
  subfolders (`lecture-slides/`, …)? Stage them flat into one temp dir first — the
  script tells you if it finds none.
- Output → `raw/<topic>/mineru/<name>/auto/<name>.md` (+ `images/`). Then `/wiki-compile`.
- **Refuses to run without a GPU** (it will not silently use CPU).

## 5. Option B — Remote GPU (傻瓜式; YOUR OWN server)
You bring your **own** SSH host + user + password. Nothing is shared — credentials
live in **env vars + local memory**, never in the repo.

1. **One-time:** install MinerU on the server (the conda block in §3).
2. **Set credentials** (PowerShell):
   ```powershell
   $env:MINERU_REMOTE_HOST = "your.server.or.ip"
   $env:MINERU_REMOTE_USER = "youruser"
   $env:MINERU_REMOTE_PASS = "yourpassword"
   # optional — only if your conda isn't ~/miniconda3 or your env isn't named 'mineru':
   $env:MINERU_REMOTE_ACTIVATE = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate mineru"
   ```
   bash/zsh: use `export VAR=...` instead of `$env:`.
3. **Run:**
   ```
   python scripts/mineru_remote_ocr.py
   ```
   Uploads your PDFs → runs OCR serially on the server → downloads markdown to
   `raw/<topic>/mineru/`. Then `/wiki-compile`.

- Keep the password in your **local Claude Code memory** (fill in
  [`templates/memory/remote-ocr-gpu-server.md.tmpl`](../templates/memory/remote-ocr-gpu-server.md.tmpl)),
  **never in git**.
- Namespace `mineru_<ns>_*` isolates `/tmp` so multiple projects don't clobber each
  other — but they still share the GPU, so **don't run two OCRs at once**.

## 6. Out-of-box example (start to finish)
```powershell
# 1. make a project (research variant)
.\scripts\bootstrap_new_wiki.ps1 -NewPath D:\ocr-demo -Topic demo -ProjectName "Demo"
# 2. drop a scanned PDF into raw/demo/  (e.g. copy some paper.pdf there)
# 3a. LOCAL:
conda activate mineru
python D:\ocr-demo\scripts\mineru_local_ocr.py D:\ocr-demo\raw\demo
# 3b. or REMOTE (your server):
$env:MINERU_REMOTE_HOST="your.ip"; $env:MINERU_REMOTE_USER="you"; $env:MINERU_REMOTE_PASS="pw"
python D:\ocr-demo\scripts\mineru_remote_ocr.py D:\ocr-demo\raw\demo
# 4. you now have raw/demo/mineru/paper/auto/paper.md  -> open Claude Code in D:\ocr-demo, run /wiki-compile
```

## 7. Troubleshooting (both paths)
| symptom | meaning / fix |
|---|---|
| exit **2** | missing env/config, or input dir not found |
| exit **3** | a PDF is truncated (no `%%EOF`) — re-download it |
| exit **4** | GPU unavailable / occupied / driver fault — fix it; **do not CPU-fallback** |
| cuDNN error | the cu13 pollution (§3 warning) — purge non-cu12 `nvidia-*`, reinstall torch |
| `mineru` not found | `conda activate mineru` first (local), or install it |
| `.pptx` won't OCR | convert first: `soffice --headless --convert-to pdf deck.pptx` (on the GPU machine), then OCR; local fallback `scripts/extract_pptx.py` |

Full pitfalls + the *why* behind every fix: [`GOTCHAS.md`](GOTCHAS.md).
