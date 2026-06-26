"""Remote MinerU OCR for an LLM-Wiki project -- one-shot pipeline.

Uploads local PDFs to a remote GPU server, runs MinerU OCR serially (one PDF at
a time), then downloads the Markdown back to raw/<topic>/mineru/ .

CONFIG -- via environment variables (NOTHING SENSITIVE IS HARD-CODED).
You bring YOUR OWN server; nothing here is shared:
    MINERU_REMOTE_HOST     ssh host / IP of your GPU box        (required)
    MINERU_REMOTE_USER     ssh user                              (required)
    MINERU_REMOTE_PASS     ssh password                          (required; keep in
                           local Claude Code memory only -- NEVER commit it)
    MINERU_NS              /tmp namespace, default baked at bootstrap (mineru_<ns>_*)
    MINERU_REMOTE_ACTIVATE shell line to enter the mineru env    (optional; override
                           if your conda path / env name differ)

Usage (PowerShell):
    $env:MINERU_REMOTE_HOST = "1.2.3.4"
    $env:MINERU_REMOTE_USER = "you"
    $env:MINERU_REMOTE_PASS = "<password>"
    python scripts/mineru_remote_ocr.py [input_dir]

`input_dir` defaults to raw/<topic>/ . Every *.pdf DIRECTLY under it is uploaded
(non-recursive -- see GOTCHA below). After the run, OCR'd Markdown lands in
raw/<topic>/mineru/ and you can run /wiki-compile .

WHY A GPU (local or remote), NEVER CPU
    CPU OCR is banned: 5-15 min/PDF vs ~30 s on GPU, plus PaddleOCR quality drift
    that pollutes wiki consistency. This script uses a REMOTE GPU over SSH (your own
    box). Have a LOCAL NVIDIA GPU instead? use scripts/mineru_local_ocr.py. Build the
    `mineru` conda env once on the server and reuse it across projects -- do NOT
    reinstall per project. Full setup: docs/OCR-SETUP.md.

HARD-WON FIXES BAKED IN (do not remove -- each cost hours)
    1. `-b pipeline` backend -- the default hybrid auto-engine routes through
       Qwen2VL and crashes on an mRoPE dimension mismatch.
    2. Serial loop, one PDF at a time -- concurrent mineru spawns competing
       FastAPI/cuDNN inits and fails the whole batch.
    3. pkill pattern is narrowed to THIS project's driver and uses a [m] bracket so
       it never matches its own command line (a broad `pkill -f mineru` SIGKILLs the
       very shell running it, before mkdir -- and would also kill other projects).
    4. Per-project /tmp namespace (mineru_<ns>_*) so two wiki projects sharing one
       GPU box do not rm -rf each other's upload dir. STILL: do not run two projects'
       OCR at the same time (shared GPU + conda env).
    5. UTF-8 stdout reconfigure -- a GBK console crashes on mineru's log glyphs.
    6. PDF %%EOF check -- arxiv downloads silently truncate; a PDF with no %%EOF
       makes pypdfium2 throw mid-OCR.
    7. GPU pre-check guards against a non-numeric nvidia-smi reply (driver fault) and
       exits cleanly (code 4) instead of crashing on int().

NON-RECURSIVE GOTCHA
    Only *.pdf DIRECTLY under input_dir are processed. If your PDFs live in
    subfolders (raw/<topic>/lecture-slides/ ...), stage them flat into one temp dir
    first, or call this script once per subfolder.

PPTX
    mineru does not read .pptx. Preferred: convert on the server with
    `soffice --headless --convert-to pdf` then run this pipeline. Local fallback:
    scripts/extract_pptx.py (text only, layout lost).

EXIT CODES: 0 ok / 2 bad config or input / 3 truncated PDF / 4 GPU unavailable
"""
import os, sys, time, warnings
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass
import paramiko

HOST = os.environ.get("MINERU_REMOTE_HOST", "").strip()
USER = os.environ.get("MINERU_REMOTE_USER", "").strip()
PASS = os.environ.get("MINERU_REMOTE_PASS", "")
# Per-project namespace, baked by bootstrap_new_wiki.ps1; overridable via env.
NS = os.environ.get("MINERU_NS", "__WIKI_NS__")
# How to enter the mineru env on YOUR server (override if conda path / env name differ).
ACTIVATE = os.environ.get(
    "MINERU_REMOTE_ACTIVATE",
    "source ~/miniconda3/etc/profile.d/conda.sh && conda activate mineru")

REMOTE_IN, REMOTE_OUT = f"/tmp/mineru_{NS}_in", f"/tmp/mineru_{NS}_out"
REMOTE_LOG = f"/tmp/mineru_{NS}.log"
REMOTE_PID = f"/tmp/mineru_{NS}.pid"
REMOTE_DONE = f"/tmp/mineru_{NS}.done"
REMOTE_DRIVER = f"/tmp/mineru_{NS}_driver.sh"

WIKI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(WIKI_ROOT, "raw", "__WIKI_TOPIC__")
LOCAL_OUT = os.path.join(DEFAULT_IN, "mineru")

MIN_FREE_GPU_MB = 8000  # abort cleanly below this rather than OOM mid-run


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30)
    return c


def run_cmd(c, cmd, timeout=60):
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def has_eof(path):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 1024))
            return b"%%EOF" in f.read()
    except OSError:
        return False


def download_tree(sftp, remote_root, local_root):
    count = 0
    os.makedirs(local_root, exist_ok=True)
    for entry in sftp.listdir_attr(remote_root):
        r = f"{remote_root}/{entry.filename}"
        l = os.path.join(local_root, entry.filename)
        if (entry.st_mode & 0o170000) == 0o040000:
            count += download_tree(sftp, r, l)
        else:
            sftp.get(r, l)
            count += 1
    return count


def main():
    if not (HOST and USER and PASS):
        log("ERROR: set MINERU_REMOTE_HOST / MINERU_REMOTE_USER / MINERU_REMOTE_PASS "
            "before running (keep the password in local memory, never in the repo).")
        return 2

    in_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_IN
    if not os.path.isdir(in_dir):
        log(f"ERROR: input dir not found: {in_dir}")
        return 2

    pdfs = sorted(
        os.path.join(in_dir, f) for f in os.listdir(in_dir)
        if f.lower().endswith(".pdf")
    )
    if not pdfs:
        log(f"no *.pdf directly under {in_dir} -- nothing to do "
            "(PDFs in subfolders are NOT picked up; stage them flat first)")
        return 0
    log(f"input dir: {in_dir}")
    log(f"PDFs to process: {len(pdfs)}")
    bad = [p for p in pdfs if not has_eof(p)]
    for p in bad:
        log(f"  !! TRUNCATED (no %%EOF): {os.path.basename(p)} -- re-download it")
    if bad:
        log("abort: fix truncated PDFs first, then re-run.")
        return 3

    t0 = time.time()
    c = connect()
    log("connected")

    rc, out, _ = run_cmd(
        c, "nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits", timeout=15)
    head = (out.strip().split("\n")[0] if out.strip() else "").strip()
    if not head.isdigit():
        log(f"GPU pre-check failed -- nvidia-smi did not return a number:\n{out.strip()}")
        log("abort: fix the GPU/driver, do NOT fall back to CPU.")
        c.close()
        return 4
    free_mb = int(head)
    log(f"GPU free memory: {free_mb} MB")
    if free_mb < MIN_FREE_GPU_MB:
        rc, who, _ = run_cmd(
            c, "nvidia-smi --query-compute-apps=pid,used_memory,process_name "
               "--format=csv,noheader", timeout=15)
        log(f"GPU occupied (need >= {MIN_FREE_GPU_MB} MB). Current apps:\n{who.strip()}")
        log("abort: do NOT fall back to CPU. Retry when the GPU frees up.")
        c.close()
        return 4

    # Clean + prepare remote dirs -- three separate commands on purpose.
    # `pkill -f` matches the WHOLE command line; a broad pattern would SIGKILL this
    # very shell before mkdir. Narrow to this project's driver + [m] bracket so the
    # pattern never matches the pkill command itself, and never touches other projects.
    run_cmd(c, f"pkill -9 -f '[m]ineru_{NS}_driver' 2>/dev/null; true")
    run_cmd(c, f"rm -rf {REMOTE_IN} {REMOTE_OUT} {REMOTE_LOG} {REMOTE_PID} {REMOTE_DONE}")
    run_cmd(c, f"mkdir -p {REMOTE_IN} {REMOTE_OUT}")

    sftp = c.open_sftp()
    for i, p in enumerate(pdfs, 1):
        sftp.put(p, f"{REMOTE_IN}/{os.path.basename(p)}")
        log(f"  uploaded [{i}/{len(pdfs)}] {os.path.basename(p)}")

    driver = f"""#!/bin/bash
{ACTIVATE}
echo "=== start $(date) ==="
for pdf in {REMOTE_IN}/*.pdf; do
  name=$(basename "$pdf" .pdf)
  echo "=== [$( date +%H:%M:%S )] PROCESSING $name ==="
  timeout 1200 mineru -p "$pdf" -o {REMOTE_OUT} -b pipeline 2>&1 || echo "=== FAIL $name rc=$? ==="
done
echo "=== all done $(date) ==="
touch {REMOTE_DONE}
"""
    with sftp.open(REMOTE_DRIVER, "w") as f:
        f.write(driver)
    sftp.close()
    run_cmd(c, f"chmod +x {REMOTE_DRIVER}")

    launch = (
        f"cd /tmp && nohup bash {REMOTE_DRIVER} "
        f"> {REMOTE_LOG} 2>&1 < /dev/null & echo $! > {REMOTE_PID}; disown"
    )
    try:
        run_cmd(c, launch, timeout=5)
    except (paramiko.buffered_pipe.PipeTimeout, TimeoutError):
        log("launch dispatched (paramiko PipeTimeout expected -- ignored)")
    c.close()

    time.sleep(3)
    c = connect()
    rc, out, _ = run_cmd(c, f"cat {REMOTE_PID}", timeout=10)
    pid = out.strip()
    log(f"launched pid={pid}")
    c.close()

    poll_n = 0
    while True:
        poll_n += 1
        time.sleep(25)
        try:
            c = connect()
        except Exception as e:
            log(f"poll#{poll_n} reconnect FAIL: {e}")
            continue
        rc, out, _ = run_cmd(
            c,
            f"if [ -f {REMOTE_DONE} ]; then echo DONE; "
            f"elif kill -0 {pid} 2>/dev/null; then echo ALIVE; "
            f"else echo DEAD; fi",
            timeout=15)
        state = out.strip().split("\n")[-1]
        rc, out, _ = run_cmd(c, f"find {REMOTE_OUT} -name '*.md' 2>/dev/null | wc -l", timeout=10)
        md_count = int(out.strip() or 0)
        rc, out, _ = run_cmd(
            c, "nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader",
            timeout=10)
        gpu = out.strip()
        rc, tail_out, _ = run_cmd(
            c, f"grep -E 'PROCESSING|FAIL|all done' {REMOTE_LOG} | tail -5", timeout=10)
        tail_short = tail_out.strip().replace("\n", " || ")[:250]
        log(f"poll#{poll_n} state={state} mds={md_count} gpu=[{gpu}]")
        if tail_short:
            log(f"  milestones: {tail_short}")
        if state in ("DONE", "DEAD"):
            log(f"loop end state={state} -- final log tail:")
            rc, final, _ = run_cmd(c, f"tail -30 {REMOTE_LOG}", timeout=15)
            for line in final.splitlines()[-30:]:
                log(f"  | {line}")
            c.close()
            break
        c.close()
        if poll_n > 200:
            log("SAFETY STOP -- run exceeded ~80 min")
            break

    log(f"downloading to {LOCAL_OUT}")
    c = connect()
    sftp = c.open_sftp()
    try:
        n = download_tree(sftp, REMOTE_OUT, LOCAL_OUT)
        log(f"downloaded {n} files")
    except Exception as e:
        log(f"download FAIL: {e}")
    try:
        sftp.get(REMOTE_LOG, os.path.join(LOCAL_OUT, "_serial_remote.log"))
    except Exception:
        pass
    sftp.close()
    c.close()
    log(f"DONE in {time.time()-t0:.0f}s -- next: run /wiki-compile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
