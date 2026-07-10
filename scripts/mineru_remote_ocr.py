"""Remote MinerU OCR for a paper-wiki project -- one-shot, append-only pipeline.

Uploads local PDFs to a remote GPU server, runs MinerU OCR serially (one PDF at
a time), downloads into project-local staging, validates every expected source,
then appends source directories and publishes one recoverable batch commit marker
under raw/<topic>/mineru/.

CONFIG -- via environment variables (NOTHING SENSITIVE IS HARD-CODED).
You bring YOUR OWN server; nothing here is shared:
    MINERU_REMOTE_HOST     ssh host / IP of your GPU box        (required)
    MINERU_REMOTE_USER     ssh user                              (required)
    MINERU_REMOTE_PASS     ssh password                          (required; inject from
                           the current shell or an OS secret store -- NEVER commit it)
    MINERU_NS              /tmp namespace; only [a-z0-9_]       (required after bootstrap)
    MINERU_REMOTE_ACTIVATE trusted administrator-provided shell snippet used to enter
                           the MinerU env (optional). It is executed verbatim; never
                           populate it from untrusted project data or user input.

Usage (PowerShell):
    $env:MINERU_REMOTE_HOST = "1.2.3.4"
    $env:MINERU_REMOTE_USER = "you"
    $env:MINERU_REMOTE_PASS = "<password>"
    python scripts/mineru_remote_ocr.py [input_dir]

`input_dir` defaults to raw/<topic>/ . Every *.pdf DIRECTLY under it is uploaded
(non-recursive -- see GOTCHA below). Existing source directories are never
overwritten. After a fully successful run, OCR Markdown lands in
raw/<topic>/mineru/ and you can run the wiki-compile action.

WHY A GPU (local or remote), NEVER CPU
    CPU OCR is banned: 5-15 min/PDF vs ~30 s on GPU, plus PaddleOCR quality drift
    that pollutes wiki consistency. This script uses a REMOTE GPU over SSH (your own
    box). Have a LOCAL NVIDIA GPU instead? use scripts/mineru_local_ocr.py. Build the
    `mineru` conda env once on the server and reuse it across projects -- do NOT
    reinstall per project. Full setup: docs/OCR-SETUP.md.

HARD-WON FIXES BAKED IN (do not remove -- each cost hours)
    1. `-b pipeline` backend avoids the hybrid Qwen2VL mRoPE mismatch.
    2. Serial loop, one PDF at a time, avoids competing FastAPI/cuDNN init.
    3. Random owner-only remote roots avoid deterministic /tmp collisions.
    4. Strict per-project namespace prevents shell injection.
    5. UTF-8 stdout avoids GBK console crashes.
    6. PDF %%EOF check catches silently truncated downloads.
    7. Numeric GPU and PID checks fail closed.
    8. System known_hosts + RejectPolicy rejects unknown or changed SSH host keys.
    9. Staging + batch markers make publication append-only and crash-recoverable.

NON-RECURSIVE GOTCHA
    Only *.pdf DIRECTLY under input_dir are processed. If your PDFs live in
    subfolders, stage them flat into one temp dir first, or invoke this script once
    per subfolder.

PPTX
    MinerU does not read .pptx. Convert with `soffice --headless --convert-to pdf`
    before this pipeline, or use scripts/extract_pptx.py as a text-only fallback.

EXIT CODES: 0 ok / 2 bad config, input, or output conflict / 3 truncated PDF /
4 GPU unavailable / 5 processing, SSH, transfer, validation, timeout, or commit failure

Publication is crash-recoverable, not an all-or-nothing filesystem rename. Each
source manifest names a batch marker; sources are committed only when that
.committed.json marker exists.
"""
import datetime
import ctypes
import errno
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import socket
import stat
import sys
import tempfile
import time
import unicodedata
import uuid
import warnings

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import paramiko  # noqa: E402

HOST = os.environ.get("MINERU_REMOTE_HOST", "").strip()
USER = os.environ.get("MINERU_REMOTE_USER", "").strip()
PASS = os.environ.get("MINERU_REMOTE_PASS", "")
# Per-project namespace, baked by bootstrap_new_wiki; overridable via env.
NS = os.environ.get("MINERU_NS", "__WIKI_NS__")
# TRUST BOUNDARY: this administrator-provided shell snippet is intentionally run
# verbatim. Unlike every path/argument below, it cannot be shell-quoted as one arg.
ACTIVATE = os.environ.get(
    "MINERU_REMOTE_ACTIVATE",
    "source ~/miniconda3/etc/profile.d/conda.sh && conda activate mineru")

WIKI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(WIKI_ROOT, "raw", "__WIKI_TOPIC__")
LOCAL_OUT = os.path.join(DEFAULT_IN, "mineru")
STAGING_PARENT = os.path.join(WIKI_ROOT, ".paper-wiki", "ocr-staging")
COMPLETION_MANIFEST = "_paper-wiki-ocr-complete.json"
BATCH_MARKER_PREFIX = "_paper-wiki-ocr-batch-"

MIN_FREE_GPU_MB = 8000
POLL_INTERVAL_SECONDS = 25
MAX_POLLS = 200
EXIT_PROCESSING = 5
VALID_NS = re.compile(r"^[a-z0-9_]+$")
SSH_TIMEOUT_SECONDS = 30
SFTP_TIMEOUT_SECONDS = 30


class CommitFailure(OSError):
    """A batch commit failed; recovery_required keeps staging for recovery."""

    def __init__(self, message, *, recovery_required=False):
        super().__init__(message)
        self.recovery_required = recovery_required


class HostKeyVerificationError(RuntimeError):
    """Raised when SSH host identity is unknown or does not match known_hosts."""


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def validate_namespace(namespace):
    return bool(VALID_NS.fullmatch(namespace))


def connect():
    """Connect using only pre-verified system known_hosts entries."""
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            HOST, username=USER, password=PASS,
            timeout=SSH_TIMEOUT_SECONDS,
            banner_timeout=SSH_TIMEOUT_SECONDS,
            auth_timeout=SSH_TIMEOUT_SECONDS,
            channel_timeout=SSH_TIMEOUT_SECONDS)
    except paramiko.BadHostKeyException as error:
        client.close()
        raise HostKeyVerificationError(
            f"SSH host key for {HOST!r} changed or mismatched known_hosts. "
            "Stop and verify the server fingerprint with its administrator; do not "
            "remove or replace the known key blindly.") from error
    except (paramiko.SSHException, OSError, socket.timeout, EOFError) as error:
        client.close()
        if "known_hosts" in str(error).lower() or "host key" in str(error).lower():
            raise HostKeyVerificationError(
                f"SSH host key for {HOST!r} is not trusted. Verify the fingerprint "
                "out of band, then add the verified key to your system "
                "~/.ssh/known_hosts. ssh-keyscan alone does not verify identity.") from error
        raise
    return client


def run_cmd(client, command, timeout=60):
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        stdout.channel.settimeout(timeout)
        stderr.channel.settimeout(timeout)
        out = stdout.read()
        err = stderr.read()
        rc = stdout.channel.recv_exit_status()
    except (paramiko.SSHException, paramiko.buffered_pipe.PipeTimeout,
            OSError, socket.timeout, TimeoutError, EOFError) as error:
        raise OSError(f"remote command timed out or failed: {error}") from error
    if isinstance(out, bytes):
        out = out.decode(errors="replace")
    if isinstance(err, bytes):
        err = err.decode(errors="replace")
    return rc, out, err


def open_sftp(client, timeout=SFTP_TIMEOUT_SECONDS):
    """Open SFTP on a channel with bounded setup and I/O waits."""
    channel = None
    try:
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise OSError("SSH transport is not active")
        channel = transport.open_session(timeout=timeout)
        channel.settimeout(timeout)
        channel.invoke_subsystem("sftp")
        return paramiko.SFTPClient(channel)
    except (paramiko.SSHException, paramiko.buffered_pipe.PipeTimeout,
            OSError, socket.timeout, TimeoutError, EOFError) as error:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        raise OSError(f"SFTP initialization timed out or failed: {error}") from error


def _is_reparse_point(path):
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _inside(boundary, candidate):
    try:
        return os.path.commonpath([
            os.path.normcase(os.path.realpath(boundary)),
            os.path.normcase(os.path.realpath(candidate)),
        ]) == os.path.normcase(os.path.realpath(boundary))
    except ValueError:
        return False


def assert_safe_project_path(path, label="path"):
    project = os.path.abspath(WIKI_ROOT)
    candidate = os.path.abspath(path)
    try:
        lexically_inside = os.path.commonpath([
            os.path.normcase(project), os.path.normcase(candidate)
        ]) == os.path.normcase(project)
    except ValueError:
        lexically_inside = False
    if not lexically_inside:
        raise OSError(f"{label} escapes project root: {candidate}")
    if not os.path.isdir(project) or _is_reparse_point(project):
        raise OSError(f"project root must be a real directory, not a link/reparse point: {project}")
    project_real = os.path.realpath(project)
    current = project
    relative = os.path.relpath(candidate, project)
    if relative != ".":
        for component in relative.split(os.sep):
            current = os.path.join(current, component)
            if os.path.lexists(current):
                if _is_reparse_point(current):
                    raise OSError(
                        f"{label} traverses a symlink/junction/reparse point: {current}")
                if not _inside(project_real, current):
                    raise OSError(f"{label} resolves outside project root: {current}")
    existing = _existing_ancestor(candidate)
    if not _inside(project_real, existing):
        raise OSError(f"{label} existing ancestor resolves outside project root: {existing}")
    return candidate


def inspect_pdf(path):
    if _is_reparse_point(path):
        raise ValueError(f"PDF must not be a symlink/junction/reparse point: {path}")
    try:
        with open(path, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"PDF is not a regular file: {path}")
            if stream.read(5) != b"%PDF-":
                raise ValueError(f"missing %PDF- header: {os.path.basename(path)}")
            digest = hashlib.sha256()
            stream.seek(0)
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            size = stream.tell()
            stream.seek(max(0, size - 4096))
            if not stream.read().rstrip(b" \t\r\n\f").endswith(b"%%EOF"):
                raise ValueError(
                    f"missing trailing %%EOF marker: {os.path.basename(path)}")
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise ValueError(f"could not read PDF {path}: {error}") from error
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"PDF changed while it was being hashed: {path}")
    return {"size": size, "sha256": digest.hexdigest()}


def inspect_pdfs(pdfs):
    return {path: inspect_pdf(path) for path in pdfs}


def recheck_pdfs(expected):
    for path, metadata in expected.items():
        current = inspect_pdf(path)
        if current != metadata:
            raise OSError(
                f"PDF changed after preflight: {path} "
                f"(expected {metadata}, got {current})")


def list_pdfs(input_dir):
    return sorted(
        (os.path.join(input_dir, name) for name in os.listdir(input_dir)
         if name.lower().endswith(".pdf")
         and os.path.isfile(os.path.join(input_dir, name))),
        key=lambda path: os.path.basename(path).casefold())


def source_map(pdfs):
    result = {}
    folded = {}
    for pdf in pdfs:
        source = os.path.splitext(os.path.basename(pdf))[0]
        if not _safe_entry_name(source):
            raise ValueError(f"invalid PDF source name: {os.path.basename(pdf)!r}")
        key = _portable_name_key(source)
        if key in folded:
            raise ValueError(
                "PDF names map to the same output source directory: "
                f"{os.path.basename(folded[key])!r} and {os.path.basename(pdf)!r}")
        folded[key] = pdf
        result[source] = pdf
    return result


def output_conflicts(sources):
    assert_safe_project_path(LOCAL_OUT, "OCR output")
    conflicts = []
    if os.path.lexists(LOCAL_OUT):
        if os.path.islink(LOCAL_OUT) or not os.path.isdir(LOCAL_OUT):
            conflicts.append(LOCAL_OUT)
            return conflicts
    for source in sources:
        destination = os.path.join(LOCAL_OUT, source)
        if os.path.lexists(destination):
            conflicts.append(destination)
    return conflicts


def _existing_ancestor(path):
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            raise OSError(f"no existing ancestor for {path}")
        current = parent
    return current


def _linux_renameat2():
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        return None
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    return renameat2


def atomic_move_no_replace(source, destination):
    if os.name == "nt":
        os.rename(source, destination)
        return
    if sys.platform.startswith("linux"):
        renameat2 = _linux_renameat2()
        if renameat2 is None:
            raise OSError("renameat2(RENAME_NOREPLACE) is unavailable")
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), destination)
        return
    raise OSError(
        "atomic no-replace publication is unsupported on this platform; "
        "only Windows and Linux are supported")


def verify_no_replace_support():
    probe = tempfile.mkdtemp(prefix="no-replace-probe-", dir=STAGING_PARENT)
    source = os.path.join(probe, "source")
    destination = os.path.join(probe, "destination")
    os.mkdir(source)
    os.mkdir(destination)
    try:
        try:
            atomic_move_no_replace(source, destination)
        except OSError as error:
            if getattr(error, "errno", None) not in (errno.EEXIST, errno.ENOTEMPTY, None):
                raise
        else:
            raise OSError("no-replace probe unexpectedly replaced an existing directory")
        os.rmdir(destination)
        atomic_move_no_replace(source, destination)
        if not os.path.isdir(destination):
            raise OSError("no-replace probe did not publish the test directory")
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def preflight_publication():
    for label, path in (
            ("raw directory", os.path.join(WIKI_ROOT, "raw")),
            ("topic directory", DEFAULT_IN),
            ("OCR output", LOCAL_OUT),
            ("OCR staging", STAGING_PARENT)):
        assert_safe_project_path(path, label)
    os.makedirs(STAGING_PARENT, exist_ok=True)
    assert_safe_project_path(STAGING_PARENT, "OCR staging")
    output_ancestor = _existing_ancestor(LOCAL_OUT)
    if os.stat(STAGING_PARENT).st_dev != os.stat(output_ancestor).st_dev:
        raise OSError("OCR staging and raw output are on different filesystems")
    verify_no_replace_support()


def create_staging_dir(prefix):
    os.makedirs(STAGING_PARENT, exist_ok=True)
    assert_safe_project_path(STAGING_PARENT, "OCR staging")
    output_ancestor = _existing_ancestor(LOCAL_OUT)
    if os.stat(STAGING_PARENT).st_dev != os.stat(output_ancestor).st_dev:
        raise OSError(".paper-wiki OCR staging and raw output are on different filesystems")
    staging_dir = tempfile.mkdtemp(prefix=prefix, dir=STAGING_PARENT)
    assert_safe_project_path(staging_dir, "OCR run staging")
    return staging_dir


def _safe_entry_name(name):
    if not isinstance(name, str) or name in ("", ".", ".."):
        return False
    if "/" in name or "\\" in name or name.endswith((" ", ".")):
        return False
    if os.path.isabs(name) or os.path.splitdrive(name)[0]:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        return False
    if any(character in '<>:"|?*' for character in name):
        return False
    base = name.split(".", 1)[0].upper()
    if (base in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9]", base)):
        return False
    return True


def _portable_name_key(name):
    return unicodedata.normalize("NFC", name).casefold()


def download_tree(sftp, remote_root, local_root, _local_boundary=None):
    """Download a regular-file tree; reject links, devices, and traversal names."""
    boundary = os.path.realpath(_local_boundary or local_root)
    os.makedirs(local_root, exist_ok=True)
    if _is_reparse_point(local_root) or not _inside(boundary, local_root):
        raise OSError(f"unsafe local staging directory: {local_root}")
    count = 0
    names_seen = {}
    for entry in sftp.listdir_attr(remote_root):
        name = entry.filename
        if not _safe_entry_name(name):
            raise OSError(f"unsafe SFTP directory entry rejected: {name!r}")
        key = _portable_name_key(name)
        if key in names_seen:
            raise OSError(
                "SFTP names collide after Windows/Unicode normalization: "
                f"{names_seen[key]!r} and {name!r}")
        names_seen[key] = name
        remote_path = posixpath.join(remote_root, name)
        local_path = os.path.realpath(os.path.join(local_root, name))
        try:
            inside = os.path.commonpath([boundary, local_path]) == boundary
        except ValueError:
            inside = False
        if not inside:
            raise OSError(f"SFTP entry escapes staging directory: {name!r}")

        mode = entry.st_mode
        if stat.S_ISLNK(mode):
            raise OSError(f"SFTP symlink rejected: {remote_path}")
        if stat.S_ISDIR(mode):
            if os.path.lexists(local_path) and not os.path.isdir(local_path):
                raise OSError(f"local staging collision: {local_path}")
            count += download_tree(
                sftp, remote_path, local_path, _local_boundary=boundary)
        elif stat.S_ISREG(mode):
            if os.path.lexists(local_path):
                raise OSError(f"duplicate local staging entry: {local_path}")
            sftp.get(remote_path, local_path)
            count += 1
        else:
            raise OSError(f"non-regular SFTP entry rejected: {remote_path}")
    return count


def markdown_files(source_dir):
    boundary = os.path.realpath(source_dir)
    found = []
    for root, dirs, files in os.walk(source_dir, followlinks=False):
        if not _inside(boundary, root):
            raise OSError(f"staged OCR output escapes its source directory: {root}")
        for name in dirs + files:
            entry = os.path.join(root, name)
            if _is_reparse_point(entry):
                raise OSError(f"link/reparse point found in staged OCR output: {entry}")
        for name in files:
            if name.lower().endswith(".md"):
                found.append(os.path.join(root, name))
    return sorted(found)


def batch_marker_name(batch_id, state):
    return f"{BATCH_MARKER_PREFIX}{batch_id}.{state}.json"


def add_completion_manifests(staged_out, sources, backend, pdf_metadata, batch_id):
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00", "Z")
    committed_marker = batch_marker_name(batch_id, "committed")
    markdown_by_source = {}
    for source, pdf in sources.items():
        source_dir = os.path.join(staged_out, source)
        if not os.path.isdir(source_dir) or _is_reparse_point(source_dir):
            raise OSError(f"expected OCR source directory missing: {source}")
        markdown = markdown_files(source_dir)
        if not markdown:
            raise OSError(f"no Markdown produced for expected source: {source}")
        relative_markdown = [
            os.path.relpath(path, source_dir).replace(os.sep, "/")
            for path in markdown
        ]
        manifest = {
            "schema": "paper-wiki/ocr-completion/v1",
            "backend": backend,
            "completed_at": completed_at,
            "source": source,
            "source_pdf": os.path.basename(pdf),
            "source_pdf_size": pdf_metadata[pdf]["size"],
            "source_pdf_sha256": pdf_metadata[pdf]["sha256"],
            "batch_id": batch_id,
            "batch_commit_marker": committed_marker,
            "state": "requires-batch-commit",
            "commit_rule": "complete only when the batch_commit_marker exists",
            "markdown": relative_markdown,
        }
        manifest_path = os.path.join(source_dir, COMPLETION_MANIFEST)
        with open(manifest_path, "x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        markdown_by_source[source] = relative_markdown
    return markdown_by_source


def _write_json_exclusive(path, value):
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def pending_batch_markers():
    if not os.path.isdir(LOCAL_OUT):
        return []
    unresolved = []
    for name in os.listdir(LOCAL_OUT):
        if not (name.startswith(BATCH_MARKER_PREFIX)
                and name.endswith(".pending.json")):
            continue
        stem = name[:-len(".pending.json")]
        committed = os.path.join(LOCAL_OUT, stem + ".committed.json")
        aborted = os.path.join(LOCAL_OUT, stem + ".aborted.json")
        if not os.path.lexists(committed) and not os.path.lexists(aborted):
            unresolved.append(os.path.join(LOCAL_OUT, name))
    return sorted(unresolved)


def _read_pending_record(marker):
    if _is_reparse_point(marker):
        raise OSError(f"pending batch marker is a link/reparse point: {marker}")
    with open(marker, "r", encoding="utf-8") as stream:
        record = json.load(stream)
    batch_id = record.get("batch_id")
    expected_name = batch_marker_name(batch_id, "pending") if batch_id else ""
    if (record.get("schema") != "paper-wiki/ocr-batch/v1"
            or not re.fullmatch(r"[0-9a-f]{32}", batch_id or "")
            or os.path.basename(marker) != expected_name):
        raise OSError(f"invalid pending batch record: {marker}")
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        raise OSError(f"pending batch record has no sources: {marker}")
    records = {}
    for item in sources:
        source = item.get("source") if isinstance(item, dict) else None
        if not _safe_entry_name(source):
            raise OSError(f"invalid source in pending batch record: {source!r}")
        key = _portable_name_key(source)
        if key in records:
            raise OSError(f"duplicate source in pending batch record: {source!r}")
        records[key] = item
    return batch_id, records


def _publish_batch_resolution(marker, state):
    """Append a committed/aborted marker while retaining the pending journal."""
    if state not in ("committed", "aborted"):
        raise ValueError(f"invalid batch resolution: {state}")
    with open(marker, "r", encoding="utf-8") as stream:
        record = json.load(stream)
    batch_id = record["batch_id"]
    destination = os.path.join(LOCAL_OUT, batch_marker_name(batch_id, state))
    marker_stage = tempfile.mkdtemp(prefix="batch-marker-", dir=STAGING_PARENT)
    staged_marker = os.path.join(marker_stage, os.path.basename(destination))
    try:
        resolved = dict(record)
        resolved["resolution"] = state
        resolved["resolved_at"] = (
            datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
                "+00:00", "Z"))
        _write_json_exclusive(staged_marker, resolved)
        atomic_move_no_replace(staged_marker, destination)
        return destination
    finally:
        shutil.rmtree(marker_stage, ignore_errors=True)


def _validate_pending_source(source_dir, batch_id, expected):
    assert_safe_project_path(source_dir, "pending OCR source")
    if not os.path.isdir(source_dir) or _is_reparse_point(source_dir):
        raise OSError(f"pending OCR source is not a safe directory: {source_dir}")
    manifest_path = os.path.join(source_dir, COMPLETION_MANIFEST)
    if _is_reparse_point(manifest_path):
        raise OSError(f"pending source manifest is a link/reparse point: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if (manifest.get("batch_id") != batch_id
            or manifest.get("batch_commit_marker")
            != batch_marker_name(batch_id, "committed")
            or manifest.get("source") != expected.get("source")
            or manifest.get("source_pdf") != expected.get("source_pdf")
            or manifest.get("source_pdf_size") != expected.get("source_pdf_size")
            or manifest.get("source_pdf_sha256") != expected.get("source_pdf_sha256")):
        raise OSError(f"pending source manifest does not match batch: {source_dir}")
    actual_markdown = {
        os.path.relpath(path, source_dir).replace(os.sep, "/")
        for path in markdown_files(source_dir)
    }
    listed_markdown = manifest.get("markdown")
    if (not isinstance(listed_markdown, list) or not actual_markdown
            or actual_markdown != set(listed_markdown)):
        raise OSError(f"pending source contains no Markdown: {source_dir}")


def recover_pending_batches():
    for marker in pending_batch_markers():
        batch_id, records = _read_pending_record(marker)
        sources = [item["source"] for item in records.values()]
        present = [
            source for source in sources
            if os.path.lexists(os.path.join(LOCAL_OUT, source))
        ]
        for source in present:
            _validate_pending_source(
                os.path.join(LOCAL_OUT, source), batch_id,
                records[_portable_name_key(source)])
        if len(present) == len(sources):
            committed = _publish_batch_resolution(marker, "committed")
            log(f"recovered fully published OCR batch: {os.path.basename(committed)}")
            continue
        if not present:
            aborted = _publish_batch_resolution(marker, "aborted")
            log(f"recovered empty OCR batch as aborted: {os.path.basename(aborted)}")
            continue

        recovery_dir = create_staging_dir(f"recovery-{batch_id}-")
        recovery_out = os.path.join(recovery_dir, "output")
        os.mkdir(recovery_out)
        moved = []
        try:
            for source in present:
                source_dir = os.path.join(LOCAL_OUT, source)
                destination = os.path.join(recovery_out, source)
                atomic_move_no_replace(source_dir, destination)
                moved.append(source)
            _publish_batch_resolution(marker, "aborted")
        except Exception as error:
            raise CommitFailure(
                f"partial batch recovery failed after moving {moved}: {error}; "
                f"pending marker retained at {marker}; recovery data: {recovery_dir}",
                recovery_required=True) from error
        raise CommitFailure(
            f"partial OCR batch was quarantined at {recovery_dir} and marked aborted; "
            "inspect it, then rerun OCR",
            recovery_required=True)


def commit_sources(staged_out, sources, batch_id, pdf_metadata, backend):
    conflicts = output_conflicts(sources)
    if conflicts:
        raise FileExistsError("output conflict: " + ", ".join(conflicts))

    os.makedirs(LOCAL_OUT, exist_ok=True)
    assert_safe_project_path(LOCAL_OUT, "OCR output")

    pending_name = batch_marker_name(batch_id, "pending")
    committed_name = batch_marker_name(batch_id, "committed")
    aborted_name = batch_marker_name(batch_id, "aborted")
    pending_path = os.path.join(LOCAL_OUT, pending_name)
    committed_path = os.path.join(LOCAL_OUT, committed_name)
    aborted_path = os.path.join(LOCAL_OUT, aborted_name)
    staged_journal = os.path.join(os.path.dirname(staged_out), pending_name)
    record = {
        "schema": "paper-wiki/ocr-batch/v1",
        "batch_id": batch_id,
        "backend": backend,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
            "+00:00", "Z"),
        "commit_rule": "sources are complete only when this record has .committed.json",
        "sources": [
            {
                "source": source,
                "source_pdf": os.path.basename(pdf),
                "source_pdf_size": pdf_metadata[pdf]["size"],
                "source_pdf_sha256": pdf_metadata[pdf]["sha256"],
            }
            for source, pdf in sources.items()
        ],
    }
    _write_json_exclusive(staged_journal, record)
    atomic_move_no_replace(staged_journal, pending_path)

    moved = []
    try:
        for source in sources:
            staged_source = os.path.join(staged_out, source)
            destination = os.path.join(LOCAL_OUT, source)
            if os.path.lexists(destination):
                raise FileExistsError(f"output appeared during commit: {destination}")
            atomic_move_no_replace(staged_source, destination)
            moved.append(source)
        committed_path = _publish_batch_resolution(pending_path, "committed")
        return committed_path
    except Exception as commit_error:
        rollback_errors = []
        for source in reversed(moved):
            destination = os.path.join(LOCAL_OUT, source)
            staged_source = os.path.join(staged_out, source)
            try:
                atomic_move_no_replace(destination, staged_source)
            except OSError as rollback_error:
                rollback_errors.append(f"{source}: {rollback_error}")
        if not rollback_errors and os.path.lexists(pending_path):
            try:
                aborted_path = _publish_batch_resolution(pending_path, "aborted")
            except OSError as marker_error:
                rollback_errors.append(f"batch marker: {marker_error}")
        if rollback_errors:
            raise CommitFailure(
                f"commit failed ({commit_error}); rollback failed: "
                + "; ".join(rollback_errors)
                + f"; recovery marker retained at {pending_path}",
                recovery_required=True) from commit_error
        raise CommitFailure(
            f"commit failed ({commit_error}); moved sources were rolled back and "
            f"the batch was marked aborted at {aborted_path}") from commit_error


def cleanup_staging(staging_dir):
    shutil.rmtree(staging_dir, ignore_errors=True)
    try:
        os.rmdir(STAGING_PARENT)
    except OSError:
        pass


def remote_paths(root):
    return {
        "root": root,
        "in": posixpath.join(root, "input"),
        "out": posixpath.join(root, "output"),
        "log": posixpath.join(root, "driver.log"),
        "pid": posixpath.join(root, "driver.pid"),
        "status": posixpath.join(root, "status"),
        "status_tmp": posixpath.join(root, "status.tmp"),
        "driver": posixpath.join(root, "driver.sh"),
        "cleanup_ack": posixpath.join(root, "cleanup.ack"),
    }


def _valid_remote_root(root):
    prefix = f"/tmp/mineru_{NS}_"
    suffix = root[len(prefix):] if root.startswith(prefix) else ""
    return bool(suffix and re.fullmatch(r"[A-Za-z0-9]+", suffix))


def create_remote_workspace(client):
    """Create and verify a random owner-only remote workspace."""
    template = f"/tmp/mineru_{NS}_XXXXXXXX"
    command = (
        "umask 077; "
        f"root=$(mktemp -d {shlex.quote(template)}) || exit 1; "
        "if [ ! -d \"$root\" ] || [ -L \"$root\" ] || [ ! -O \"$root\" ]; then "
        "rm -rf -- \"$root\"; exit 1; fi; "
        "chmod 700 \"$root\" || { rm -rf -- \"$root\"; exit 1; }; "
        "mkdir -m 700 \"$root/input\" \"$root/output\" || "
        "{ rm -rf -- \"$root\"; exit 1; }; "
        "printf '%s\\n' \"$root\"")
    rc, out, err = run_cmd(client, command, timeout=SSH_TIMEOUT_SECONDS)
    root = out.strip()
    if rc != 0 or not _valid_remote_root(root) or "\n" in root:
        raise OSError(
            f"remote mktemp workspace validation failed: {err.strip() or out.strip()}")
    return remote_paths(root)


def build_driver(paths):
    """Build a trapped driver that retains results until client acknowledgement."""
    remote_root = shlex.quote(paths["root"])
    remote_in = shlex.quote(paths["in"])
    remote_out = shlex.quote(paths["out"])
    status = shlex.quote(paths["status"])
    status_tmp = shlex.quote(paths["status_tmp"])
    cleanup_ack = shlex.quote(paths["cleanup_ack"])
    return f"""#!/bin/bash
set -u
umask 077
cleanup() {{
  trap - EXIT
  rm -rf -- {remote_root}
}}
session_members() {{
  ps -eo pid=,sid= | awk -v me="$$" -v session="$$" \
    '$2 == session && $1 != me {{print $1}}'
}}
terminate_session() {{
  signal_name="$1"
  trap '' HUP INT TERM
  if declare -F write_status >/dev/null 2>&1; then
    write_status FAILED || true
  fi
  # This driver is launched by setsid, so $$ is the session id. GNU timeout may
  # create a child PGID, therefore enumerate the whole session rather than only
  # the driver's process group.
  members=$(session_members)
  [ -z "$members" ] || kill -TERM $members 2>/dev/null || true
  waited=0
  while [ -n "$members" ] && [ "$waited" -lt 10 ]; do
    sleep 1
    waited=$((waited + 1))
    members=$(session_members)
  done
  if [ -n "$members" ]; then
    kill -KILL $members 2>/dev/null || true
  fi
  case "$signal_name" in
    HUP) exit 129 ;;
    INT) exit 130 ;;
    *) exit 143 ;;
  esac
}}
trap cleanup EXIT
trap 'terminate_session HUP' HUP
trap 'terminate_session INT' INT
trap 'terminate_session TERM' TERM
wait_for_client_cleanup() {{
  waited=0
  while [ ! -f {cleanup_ack} ] && [ "$waited" -lt 7200 ]; do
    sleep 5
    waited=$((waited + 5))
  done
}}
# MINERU_REMOTE_ACTIVATE is a trusted administrator shell snippet and is the only
# intentionally unquoted configuration value in this file.
{ACTIVATE}
activate_rc=$?
write_status() {{
  printf '%s\\n' "$1" > {status_tmp} && mv -f {status_tmp} {status}
}}
if [ "$activate_rc" -ne 0 ]; then
  printf '=== FAIL activation rc=%s ===\\n' "$activate_rc"
  write_status FAILED
  wait_for_client_cleanup
  exit {EXIT_PROCESSING}
fi
printf '=== start %s ===\\n' "$(date)"
failed=0
for pdf in {remote_in}/*.pdf; do
  name=$(basename "$pdf" '.pdf')
  printf '=== [%s] PROCESSING %s ===\\n' "$(date +%H:%M:%S)" "$name"
  timeout 1200 mineru -p "$pdf" -o {remote_out} -b pipeline 2>&1 &
  ocr_pid=$!
  wait "$ocr_pid"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '=== FAIL %s rc=%s ===\\n' "$name" "$rc"
    failed=1
  fi
done
if [ "$failed" -ne 0 ]; then
  printf '=== batch FAILED %s ===\\n' "$(date)"
  write_status FAILED
  wait_for_client_cleanup
  exit {EXIT_PROCESSING}
fi
printf '=== all done %s ===\\n' "$(date)"
write_status DONE
wait_for_client_cleanup
exit 0
"""


def _close(client):
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def cleanup_remote_workspace(paths):
    """Best-effort client cleanup; the remote EXIT trap remains authoritative."""
    if not paths or not _valid_remote_root(paths["root"]):
        return
    client = None
    try:
        client = connect()
        root = shlex.quote(paths["root"])
        pid_path = shlex.quote(paths["pid"])
        status_path = shlex.quote(paths["status"])
        ack_path = shlex.quote(paths["cleanup_ack"])
        command = f"""
if [ -d {root} ] && [ ! -L {root} ] && [ -O {root} ]; then
  state=''
  [ -f {status_path} ] && state=$(cat {status_path} 2>/dev/null || true)
  pid=''
  [ -f {pid_path} ] && pid=$(cat {pid_path} 2>/dev/null || true)
  case "$pid" in (*[!0-9]*|'') pid='' ;; esac
  sid=''
  if [ -n "$pid" ]; then
    sid=$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  fi
  if [ -n "$pid" ] && [ "$sid" = "$pid" ]; then
    if [ "$state" = DONE ] || [ "$state" = FAILED ]; then
      : > {ack_path}
    else
      members=$(ps -eo pid=,sid= | awk -v session="$pid" \
        '$2 == session {{print $1}}')
      [ -z "$members" ] || kill -TERM $members 2>/dev/null || true
    fi
    waited=0
    members=$(ps -eo pid=,sid= | awk -v session="$pid" \
      '$2 == session {{print $1}}')
    while [ -n "$members" ] && [ "$waited" -lt 10 ]; do
      sleep 1
      waited=$((waited + 1))
      members=$(ps -eo pid=,sid= | awk -v session="$pid" \
        '$2 == session {{print $1}}')
    done
    if [ -n "$members" ]; then
      kill -KILL $members 2>/dev/null || true
      sleep 1
    fi
  elif [ -n "$pid" ]; then
    # Do not signal a potentially reused PID that is no longer our session leader.
    rm -rf -- {root}
    exit 1
  fi
  [ ! -e {root} ] || rm -rf -- {root}
  if [ -n "$pid" ] && [ "$sid" = "$pid" ]; then
    members=$(ps -eo pid=,sid= | awk -v session="$pid" \
      '$2 == session {{print $1}}')
    [ -z "$members" ] || exit 1
  fi
  [ ! -e {root} ] || exit 1
fi
"""
        rc, _out, err = run_cmd(client, command, timeout=SSH_TIMEOUT_SECONDS)
        if rc != 0:
            raise OSError(f"remote cleanup did not complete: {err.strip()}")
    except Exception as error:
        raise OSError(f"remote cleanup could not be confirmed: {error}") from error
    finally:
        _close(client)


def _log_host_key_error(error):
    log(f"ERROR: {error}")
    log("SSH host identity was not accepted; no OCR output was downloaded or published.")


def verify_remote_pdf(client, remote_pdf, expected):
    quoted = shlex.quote(remote_pdf)
    command = (
        f"size=$(wc -c < {quoted}) || exit 1; "
        f"hash=$(sha256sum {quoted} | awk '{{print $1}}') || exit 1; "
        "printf '%s %s\\n' \"$size\" \"$hash\"")
    rc, out, err = run_cmd(client, command, timeout=SSH_TIMEOUT_SECONDS)
    fields = out.strip().split()
    if (rc != 0 or len(fields) != 2 or not fields[0].isdigit()
            or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[1])):
        raise OSError(
            f"could not verify uploaded PDF {remote_pdf}: {err.strip() or out.strip()}")
    actual = {"size": int(fields[0]), "sha256": fields[1].lower()}
    if actual != expected:
        raise OSError(
            f"uploaded PDF integrity mismatch for {remote_pdf}: "
            f"expected {expected}, got {actual}")


def run_remote_pipeline(pdfs, sources, pdf_metadata, started_at):
    paths = None
    staging_dir = None
    preserve_staging = False
    try:
        client = connect()
        try:
            log("connected with verified SSH host key")
            rc, out, err = run_cmd(
                client,
                "nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits",
                timeout=15)
            head = (out.strip().split("\n")[0] if out.strip() else "").strip()
            if rc != 0 or not head.isdigit():
                log("GPU pre-check failed -- nvidia-smi did not return a number:")
                log((out or err).strip())
                log("abort: fix the GPU/driver, do NOT fall back to CPU.")
                return 4
            free_mb = int(head)
            log(f"GPU free memory: {free_mb} MB")
            if free_mb < MIN_FREE_GPU_MB:
                _rc, who, _err = run_cmd(
                    client,
                    "nvidia-smi --query-compute-apps=pid,used_memory,process_name "
                    "--format=csv,noheader",
                    timeout=15)
                log(f"GPU occupied (need >= {MIN_FREE_GPU_MB} MB). Current apps:")
                log(who.strip())
                log("abort: do NOT fall back to CPU. Retry when the GPU frees up.")
                return 4

            paths = create_remote_workspace(client)
            log(f"private remote workspace: {paths['root']}")
            sftp = open_sftp(client)
            try:
                for index, (source, pdf) in enumerate(sources.items(), 1):
                    # Normalize every extension to lowercase so .PDF behaves like .pdf.
                    remote_pdf = posixpath.join(paths["in"], source + ".pdf")
                    sftp.put(pdf, remote_pdf)
                    verify_remote_pdf(client, remote_pdf, pdf_metadata[pdf])
                    log(f"  uploaded+verified [{index}/{len(pdfs)}] {os.path.basename(pdf)}")
                with sftp.open(paths["driver"], "w") as stream:
                    stream.write(build_driver(paths))
            finally:
                try:
                    sftp.close()
                except Exception:
                    pass

            rc, _out, err = run_cmd(
                client, f"chmod 700 {shlex.quote(paths['driver'])}")
            if rc != 0:
                raise OSError(f"could not make remote driver executable: {err.strip()}")
            launch = (
                f"cd {shlex.quote(paths['root'])} && "
                "command -v setsid >/dev/null 2>&1 || exit 1; "
                f"nohup setsid bash {shlex.quote(paths['driver'])} "
                f"> {shlex.quote(paths['log'])} 2>&1 < /dev/null & "
                f"pid=$!; printf '%s\\n' \"$pid\" > {shlex.quote(paths['pid'])}; "
                "sleep 1; "
                "sid=$(ps -o sid= -p \"$pid\" 2>/dev/null | tr -d ' '); "
                "[ \"$sid\" = \"$pid\" ] || "
                "{ kill -TERM \"$pid\" 2>/dev/null || true; "
                "sleep 1; kill -KILL \"$pid\" 2>/dev/null || true; exit 1; }")
            rc, _out, err = run_cmd(client, launch, timeout=10)
            if rc != 0:
                raise OSError(f"remote launch failed: {err.strip()}")
        finally:
            _close(client)

        time.sleep(3)
        client = connect()
        try:
            rc, out, err = run_cmd(
                client, f"cat {shlex.quote(paths['pid'])}", timeout=10)
            pid = out.strip()
            if rc != 0 or not re.fullmatch(r"[0-9]+", pid):
                raise OSError(
                    f"remote driver PID is missing or non-numeric: {pid!r} {err.strip()}")
            rc, out, err = run_cmd(
                client,
                f"sid=$(ps -o sid= -p {shlex.quote(pid)} 2>/dev/null "
                "| tr -d ' '); printf '%s\\n' \"$sid\"",
                timeout=10)
            if rc != 0 or out.strip() != pid:
                raise OSError(
                    f"remote driver is not its own session leader: "
                    f"pid={pid!r} sid={out.strip()!r} {err.strip()}")
            log(f"launched pid={pid}")
        finally:
            _close(client)

        final_state = None
        reconnect_failures = 0
        for poll_number in range(1, MAX_POLLS + 1):
            time.sleep(POLL_INTERVAL_SECONDS)
            client = None
            try:
                client = connect()
                reconnect_failures = 0
                status_path = shlex.quote(paths["status"])
                state_command = (
                    f"if [ -f {status_path} ]; then cat {status_path}; "
                    f"elif kill -0 {shlex.quote(pid)} 2>/dev/null; then echo ALIVE; "
                    "else echo DEAD; fi")
                rc, out, err = run_cmd(client, state_command, timeout=15)
                if rc != 0:
                    raise OSError(f"status check failed: {err.strip()}")
                state = out.strip().split("\n")[-1]
                if state not in ("ALIVE", "DONE", "FAILED", "DEAD"):
                    raise OSError(f"invalid remote state: {state!r}")
                final_state = state

                _rc, md_out, _err = run_cmd(
                    client,
                    f"find {shlex.quote(paths['out'])} -name '*.md' "
                    "-type f 2>/dev/null | wc -l",
                    timeout=10)
                md_text = md_out.strip()
                md_count = int(md_text) if md_text.isdigit() else 0
                _rc, gpu_out, _err = run_cmd(
                    client,
                    "nvidia-smi --query-gpu=utilization.gpu,memory.used "
                    "--format=csv,noheader",
                    timeout=10)
                _rc, tail_out, _err = run_cmd(
                    client,
                    f"grep -E {shlex.quote('PROCESSING|FAIL|all done')} "
                    f"{shlex.quote(paths['log'])} | tail -5",
                    timeout=10)
                tail_short = tail_out.strip().replace("\n", " || ")[:250]
                log(f"poll#{poll_number} state={final_state} mds={md_count} "
                    f"gpu=[{gpu_out.strip()}]")
                if tail_short:
                    log(f"  milestones: {tail_short}")
                if final_state in ("DONE", "FAILED", "DEAD"):
                    _rc, final_log, _err = run_cmd(
                        client, f"tail -30 {shlex.quote(paths['log'])}", timeout=15)
                    log(f"loop end state={final_state} -- final log tail:")
                    for line in final_log.splitlines()[-30:]:
                        log(f"  | {line}")
                    break
            except HostKeyVerificationError:
                raise
            except (OSError, paramiko.SSHException, socket.timeout,
                    TimeoutError, EOFError) as error:
                reconnect_failures += 1
                log(f"poll#{poll_number} transport failure: {error}")
                if reconnect_failures >= 3:
                    raise OSError("three consecutive remote poll failures") from error
            finally:
                _close(client)

        if final_state != "DONE":
            if final_state is None or final_state == "ALIVE":
                log(f"SAFETY STOP -- run exceeded "
                    f"{MAX_POLLS * POLL_INTERVAL_SECONDS // 60} min")
            else:
                log(f"ERROR: remote OCR ended in state={final_state}")
            return EXIT_PROCESSING

        staging_dir = create_staging_dir("remote-")
        staged_out = os.path.join(staging_dir, "output")
        client = connect()
        sftp = None
        try:
            sftp = open_sftp(client)
            file_count = download_tree(sftp, paths["out"], staged_out)
            sftp.get(paths["log"], os.path.join(staging_dir, "_serial_remote.log"))
            log(f"downloaded {file_count} file(s) into project-local staging")
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            _close(client)

        recheck_pdfs(pdf_metadata)
        batch_id = uuid.uuid4().hex
        markdown = add_completion_manifests(
            staged_out, sources, "remote-gpu", pdf_metadata, batch_id)
        try:
            committed_marker = commit_sources(
                staged_out, sources, batch_id, pdf_metadata, "remote-gpu")
        except CommitFailure as error:
            preserve_staging = error.recovery_required
            if preserve_staging:
                log("RECOVERY REQUIRED: staging and the pending batch marker were retained")
            raise

        markdown_count = sum(len(paths_) for paths_ in markdown.values())
        log(f"DONE in {time.time()-started_at:.0f}s: {len(sources)} source(s) "
            f"committed via {os.path.basename(committed_marker)}, "
            f"{markdown_count} Markdown file(s)")
        log("next: run the wiki-compile action")
        return 0
    finally:
        cleanup_remote_workspace(paths)
        if staging_dir is not None:
            if preserve_staging:
                log(f"recovery staging retained at: {staging_dir}")
            else:
                cleanup_staging(staging_dir)


def _main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if not validate_namespace(NS):
        log(f"ERROR: invalid MINERU_NS {NS!r}; expected only lowercase letters, "
            "digits, and underscores (^[a-z0-9_]+$).")
        return 2
    if not (HOST and USER and PASS):
        log("ERROR: set MINERU_REMOTE_HOST / MINERU_REMOTE_USER / MINERU_REMOTE_PASS "
            "before running (inject the password from the shell or an OS secret store; "
            "never write it to the repo or a client memory file).")
        return 2

    input_dir = os.path.abspath(argv[0]) if argv else DEFAULT_IN
    if not os.path.isdir(input_dir):
        log(f"ERROR: input dir not found: {input_dir}")
        return 2

    pdfs = list_pdfs(input_dir)
    if not pdfs:
        log(f"no *.pdf directly under {input_dir} -- nothing to do "
            "(PDFs in subfolders are NOT picked up; stage them flat first)")
        return 0
    try:
        sources = source_map(pdfs)
    except ValueError as error:
        log(f"ERROR: {error}")
        return 2

    try:
        preflight_publication()
        recover_pending_batches()
        conflicts = output_conflicts(sources)
    except (OSError, ValueError) as error:
        log(f"ERROR: publication preflight failed: {error}")
        return EXIT_PROCESSING
    if conflicts:
        log("ERROR: append-only output conflict; refusing to overwrite existing path(s):")
        for path in conflicts:
            log(f"  - {path}")
        return 2

    try:
        pdf_metadata = inspect_pdfs(pdfs)
    except ValueError as error:
        log(f"ERROR: invalid or unstable PDF: {error}")
        log("abort: use a regular PDF with a %PDF- header and trailing %%EOF marker.")
        return 3

    log(f"input dir: {input_dir}")
    log(f"PDFs to process: {len(pdfs)}")
    return run_remote_pipeline(pdfs, sources, pdf_metadata, time.time())


def main(argv=None):
    try:
        return _main(argv)
    except HostKeyVerificationError as error:
        _log_host_key_error(error)
        return EXIT_PROCESSING
    except (CommitFailure, OSError, ValueError, paramiko.SFTPError,
            paramiko.SSHException,
            paramiko.buffered_pipe.PipeTimeout, socket.timeout,
            TimeoutError, EOFError) as error:
        log(f"ERROR: remote OCR transport/processing failure: {error}")
        return EXIT_PROCESSING


if __name__ == "__main__":
    sys.exit(main())
