"""Local GPU MinerU OCR -- run mineru on THIS machine's NVIDIA GPU (no SSH).

For users who have a local NVIDIA GPU + the `mineru` conda env. Mirrors the remote
script's safety: refuses to run without a GPU (never CPU), serial one-PDF-at-a-time,
%%EOF truncation check, and the same output layout.

PREREQ: an NVIDIA GPU, and `mineru` on PATH (run `conda activate mineru` first).
Install once -- see docs/OCR-SETUP.md.

Usage:
    conda activate mineru
    python scripts/mineru_local_ocr.py [input_dir]      # default raw/<topic>/

Output -> raw/<topic>/mineru/<name>/auto/<name>.md (+ images/). Then /wiki-compile.

NON-RECURSIVE: only *.pdf DIRECTLY under input_dir are processed. PDFs in
subfolders? stage them flat into one temp dir first (the script warns you).

PPTX: mineru does not read .pptx. Convert first (`soffice --headless --convert-to
pdf deck.pptx`) or use scripts/extract_pptx.py. See docs/OCR-SETUP.md.

EXIT CODES: 0 ok / 2 bad config or input / 3 truncated PDF / 4 no GPU
"""
import os, sys, glob, shutil, subprocess
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

WIKI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(WIKI_ROOT, "raw", "__WIKI_TOPIC__")
LOCAL_OUT = os.path.join(DEFAULT_IN, "mineru")


def log(m):
    print(f"[local-ocr] {m}", flush=True)


def has_eof(path):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 1024))
            return b"%%EOF" in f.read()
    except OSError:
        return False


def gpu_available():
    """True if an NVIDIA GPU is usable. Never run OCR on CPU."""
    if not shutil.which("nvidia-smi"):
        return False
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=15)
        if r.returncode != 0:
            return False
    except Exception:
        return False
    # If torch is importable, double-check CUDA is actually visible.
    try:
        import torch  # noqa
        return bool(torch.cuda.is_available())
    except Exception:
        return True  # nvidia-smi works; assume mineru's own torch sees the GPU


def main():
    if not shutil.which("mineru"):
        log("ERROR: `mineru` not on PATH. Run `conda activate mineru` first, "
            "or install it (see docs/OCR-SETUP.md).")
        return 2

    if not gpu_available():
        log("ERROR: no usable NVIDIA GPU detected. OCR needs a GPU -- it must NOT run "
            "on CPU (slow + quality drift). Options:")
        log("  - fix your local GPU/driver, or")
        log("  - use a remote GPU box: scripts/mineru_remote_ocr.py, or")
        log("  - skip OCR for born-digital PDFs: the no-OCR WebFetch path in "
            "examples/QUICKSTART.md.")
        return 4

    in_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    if not os.path.isdir(in_dir):
        log(f"ERROR: input dir not found: {in_dir}")
        return 2

    pdfs = sorted(p for p in glob.glob(os.path.join(in_dir, "*.pdf")))
    if not pdfs:
        log(f"no *.pdf directly under {in_dir} -- nothing to do "
            "(PDFs in subfolders are NOT picked up; stage them flat first)")
        return 0

    bad = [p for p in pdfs if not has_eof(p)]
    for p in bad:
        log(f"  !! TRUNCATED (no %%EOF): {os.path.basename(p)} -- re-download it")
    if bad:
        log("abort: fix truncated PDFs first, then re-run.")
        return 3

    os.makedirs(LOCAL_OUT, exist_ok=True)
    log(f"input : {in_dir}")
    log(f"output: {LOCAL_OUT}")
    log(f"PDFs  : {len(pdfs)}")
    fails = []
    for i, p in enumerate(pdfs, 1):
        name = os.path.basename(p)
        log(f"[{i}/{len(pdfs)}] {name}")
        # -b pipeline: the default hybrid auto-engine routes through Qwen2VL and
        # crashes on an mRoPE mismatch. Serial on purpose (concurrent mineru fails).
        r = subprocess.run(["mineru", "-p", p, "-o", LOCAL_OUT, "-b", "pipeline"])
        if r.returncode != 0:
            fails.append(name)
            log(f"  FAIL rc={r.returncode} (continuing)")

    n_md = len(glob.glob(os.path.join(LOCAL_OUT, "*", "auto", "*.md")))
    log(f"DONE: {len(pdfs)-len(fails)}/{len(pdfs)} ok, {n_md} markdown files in mineru/")
    if fails:
        log(f"failed: {', '.join(fails)}")
    log("next: run /wiki-compile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
