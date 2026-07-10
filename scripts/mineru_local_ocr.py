"""Local GPU MinerU OCR -- run mineru on THIS machine's NVIDIA GPU (no SSH).

For users who have a local NVIDIA GPU + the `mineru` conda env. Mirrors the remote
script's safety: refuses to run without a GPU (never CPU), serial one-PDF-at-a-time,
strict PDF framing/hash checks, append-only output, and the same output layout.

PREREQ: an NVIDIA GPU, and `mineru` on PATH (run `conda activate mineru` first).
Install once -- see docs/OCR-SETUP.md.

Usage:
    conda activate mineru
    python scripts/mineru_local_ocr.py [input_dir]      # default raw/<topic>/

Output -> raw/<topic>/mineru/<name>/auto/<name>.md (+ images/). Then run the
wiki-compile action. Existing source directories are never overwritten.

NON-RECURSIVE: only *.pdf DIRECTLY under input_dir are processed. PDFs in
subfolders? stage them flat into one temp dir first (the script warns you).

PPTX: mineru does not read .pptx. Convert first (`soffice --headless --convert-to
pdf deck.pptx`) or use scripts/extract_pptx.py. See docs/OCR-SETUP.md.

EXIT CODES: 0 ok / 2 bad config, input, or output conflict / 3 truncated PDF /
4 no GPU / 5 processing, validation, staging, or commit failure

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
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

WIKI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(WIKI_ROOT, "raw", "__WIKI_TOPIC__")
LOCAL_OUT = os.path.join(DEFAULT_IN, "mineru")
STAGING_PARENT = os.path.join(WIKI_ROOT, ".paper-wiki", "ocr-staging")
COMPLETION_MANIFEST = "_paper-wiki-ocr-complete.json"
BATCH_MARKER_PREFIX = "_paper-wiki-ocr-batch-"
EXIT_PROCESSING = 5
DEFAULT_LOCAL_TIMEOUT_SECONDS = 1200
LOCAL_TIMEOUT_ENV = "MINERU_LOCAL_TIMEOUT_SECONDS"


class CommitFailure(OSError):
    """A batch commit failed; recovery_required keeps staging for recovery."""

    def __init__(self, message, *, recovery_required=False):
        super().__init__(message)
        self.recovery_required = recovery_required


class ProcessTreeTerminationError(RuntimeError):
    """Raised when timeout cleanup cannot prove that every descendant stopped."""


if os.name == "nt":
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]


def _windows_process_table():
    """Return pid -> parent pid using a Toolhelp snapshot."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    table = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(error)
            return table
        while True:
            table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not process_next(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:
                    raise ctypes.WinError(error)
                break
        return table
    finally:
        close_handle(snapshot)


def _windows_descendants(root_pid, table=None):
    table = _windows_process_table() if table is None else table
    descendants = set()
    frontier = {int(root_pid)}
    while frontier:
        children = {
            pid for pid, parent in table.items()
            if parent in frontier and pid not in descendants and pid != root_pid
        }
        descendants.update(children)
        frontier = children
    return descendants


def _windows_pid_alive(pid):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    wait = kernel32.WaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x00100000 | 0x1000, False, int(pid))
    if not handle:
        error = ctypes.get_last_error()
        if error in (87, 1168):  # ERROR_INVALID_PARAMETER / ERROR_NOT_FOUND
            return False
        return True  # Access denied or another error means liveness is unproven.
    try:
        return wait(handle, 0) == 0x00000102  # WAIT_TIMEOUT
    finally:
        close_handle(handle)


def _windows_terminate_pid(pid):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    terminate = kernel32.TerminateProcess
    terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate.restype = wintypes.BOOL
    wait = kernel32.WaitForSingleObject
    wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x0001 | 0x00100000, False, int(pid))
    if not handle:
        if not _windows_pid_alive(pid):
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not terminate(handle, EXIT_PROCESSING):
            error = ctypes.get_last_error()
            if _windows_pid_alive(pid):
                raise ctypes.WinError(error)
        if wait(handle, 5000) != 0x00000000:  # WAIT_OBJECT_0
            raise ProcessTreeTerminationError(
                f"process {pid} did not terminate within 5 seconds")
    finally:
        close_handle(handle)


def _terminate_windows_tree(root_pid):
    """Stop the launcher first, repeatedly enumerate descendants, and verify all died."""
    root_pid = int(root_pid)
    if root_pid == os.getpid():
        raise ProcessTreeTerminationError("refusing to terminate the OCR controller")
    known = set()
    for _attempt in range(6):
        table = _windows_process_table()
        known.update(_windows_descendants(root_pid, table))
        if _windows_pid_alive(root_pid):
            _windows_terminate_pid(root_pid)
        # The launcher is now stopped, so it cannot create more descendants.
        table = _windows_process_table()
        known.update(_windows_descendants(root_pid, table))
        for pid in sorted(known, reverse=True):
            if _windows_pid_alive(pid):
                _windows_terminate_pid(pid)
        table = _windows_process_table()
        known.update(_windows_descendants(root_pid, table))
        remaining = {
            pid for pid in known | {root_pid}
            if _windows_pid_alive(pid)
        }
        if not remaining:
            return
    raise ProcessTreeTerminationError(
        "could not prove complete Windows process-tree termination; "
        f"remaining PIDs: {sorted(remaining)}")


def log(message):
    print(f"[local-ocr] {message}", flush=True)


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
    """Reject paths escaping the project or traversing links/reparse points."""
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
    """Validate strict PDF framing and return immutable size/SHA-256 metadata."""
    if _is_reparse_point(path):
        raise ValueError(f"PDF must not be a symlink/junction/reparse point: {path}")
    try:
        with open(path, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"PDF is not a regular file: {path}")
            header = stream.read(5)
            if header != b"%PDF-":
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
            tail = stream.read().rstrip(b" \t\r\n\f")
            if not tail.endswith(b"%%EOF"):
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


def local_timeout_seconds():
    raw = os.environ.get(LOCAL_TIMEOUT_ENV, str(DEFAULT_LOCAL_TIMEOUT_SECONDS))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{LOCAL_TIMEOUT_ENV} must be an integer number of seconds") from error
    if value < 1 or value > 86400:
        raise ValueError(f"{LOCAL_TIMEOUT_ENV} must be between 1 and 86400 seconds")
    return value


def gpu_available():
    """True if an NVIDIA GPU is usable. Never run OCR on CPU."""
    if not shutil.which("nvidia-smi"):
        return False
    try:
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, timeout=15, check=False)
        if result.returncode != 0:
            return False
    except Exception:
        return False
    # If torch is importable, double-check CUDA is actually visible.
    try:
        import torch  # noqa: F401
        return bool(torch.cuda.is_available())
    except Exception:
        return True  # nvidia-smi works; assume mineru's own torch sees the GPU


def list_pdfs(input_dir):
    return sorted(
        (os.path.join(input_dir, name) for name in os.listdir(input_dir)
         if name.lower().endswith(".pdf")
         and os.path.isfile(os.path.join(input_dir, name))),
        key=lambda path: os.path.basename(path).casefold())


def source_map(pdfs):
    """Return source-name -> PDF, rejecting ambiguous destination names."""
    result = {}
    folded = {}
    for pdf in pdfs:
        source = os.path.splitext(os.path.basename(pdf))[0]
        if source in ("", ".", ".."):
            raise ValueError(f"invalid PDF source name: {os.path.basename(pdf)!r}")
        key = source.casefold()
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
    """Atomically rename a file/directory while refusing an existing target."""
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
    """Exercise no-replace on the target filesystem before expensive OCR starts."""
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
    """Create project-local staging and verify it shares LOCAL_OUT's filesystem."""
    os.makedirs(STAGING_PARENT, exist_ok=True)
    assert_safe_project_path(STAGING_PARENT, "OCR staging")
    output_ancestor = _existing_ancestor(LOCAL_OUT)
    if os.stat(STAGING_PARENT).st_dev != os.stat(output_ancestor).st_dev:
        raise OSError(".paper-wiki OCR staging and raw output are on different filesystems")
    staging_dir = tempfile.mkdtemp(prefix=prefix, dir=STAGING_PARENT)
    assert_safe_project_path(staging_dir, "OCR run staging")
    return staging_dir


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
    """Validate sources and bind them to one recoverable batch commit marker."""
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
        if (not isinstance(source, str) or source in ("", ".", "..")
                or os.path.basename(source) != source
                or "/" in source or "\\" in source):
            raise OSError(f"invalid source in pending batch record: {source!r}")
        key = source.casefold()
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
    """Finish fully moved batches or quarantine partial batches before new OCR."""
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
                records[source.casefold()])
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
    """Publish a recoverable batch; the committed marker is the sole commit point."""
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


def terminate_process_group(process):
    """Terminate MinerU and all descendants after a local timeout."""
    if os.name == "nt":
        taskkill_succeeded = False
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False, capture_output=True, timeout=15)
            taskkill_succeeded = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            taskkill_succeeded = False
        # Never trust taskkill's status alone. Verify the root and every process
        # still parented to it; use Toolhelp termination if anything survived.
        survivors = _windows_descendants(process.pid)
        if (not taskkill_succeeded or _windows_pid_alive(process.pid)
                or any(_windows_pid_alive(pid) for pid in survivors)):
            _terminate_windows_tree(process.pid)
        final_descendants = _windows_descendants(process.pid)
        remaining = {
            pid for pid in final_descendants | {process.pid}
            if _windows_pid_alive(pid)
        }
        if remaining:
            raise ProcessTreeTerminationError(
                "Windows OCR process tree is still alive after cleanup: "
                + ", ".join(str(pid) for pid in sorted(remaining)))
        return True
    # The launcher is the session/group leader because run_mineru uses
    # start_new_session=True. Its group can outlive the launcher itself.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    # A process that detached into another session cannot be proven part of this
    # group; the local launcher deliberately starts a fresh session and MinerU is
    # expected to keep descendants in it.
    return True


def run_mineru(pdf, staged_out, timeout_seconds):
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        ["mineru", "-p", pdf, "-o", staged_out, "-b", "pipeline"],
        **kwargs)
    try:
        return process.wait(timeout=timeout_seconds), False
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            raise ProcessTreeTerminationError(
                f"MinerU launcher {process.pid} remained alive after tree cleanup")
        return process.returncode, True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
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
        timeout_seconds = local_timeout_seconds()
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

    try:
        staging_dir = create_staging_dir("local-")
    except OSError as error:
        log(f"ERROR: could not create safe project-local staging: {error}")
        return EXIT_PROCESSING

    staged_out = os.path.join(staging_dir, "output")
    os.makedirs(staged_out)
    log(f"input : {input_dir}")
    log(f"stage : {staged_out}")
    log(f"commit: {LOCAL_OUT}")
    log(f"PDFs  : {len(pdfs)}")
    log(f"timeout: {timeout_seconds}s per PDF ({LOCAL_TIMEOUT_ENV})")

    failures = []
    preserve_staging = False
    try:
        for index, pdf in enumerate(pdfs, 1):
            name = os.path.basename(pdf)
            log(f"[{index}/{len(pdfs)}] {name}")
            # -b pipeline: the default hybrid auto-engine routes through Qwen2VL
            # and crashes on an mRoPE mismatch. Serial on purpose.
            try:
                return_code, timed_out = run_mineru(pdf, staged_out, timeout_seconds)
            except Exception as error:
                failures.append(name)
                log(f"  FAIL could not run mineru: {error} (continuing)")
                continue
            if timed_out:
                failures.append(name)
                log(f"  FAIL timed out after {timeout_seconds}s; process group terminated")
            elif return_code != 0:
                failures.append(name)
                log(f"  FAIL rc={return_code} (continuing)")

        if failures:
            log(f"ERROR: OCR failed for {len(failures)} PDF(s): {', '.join(failures)}")
            log("no raw output was published; staged partial output is being removed")
            return EXIT_PROCESSING

        try:
            recheck_pdfs(pdf_metadata)
            batch_id = uuid.uuid4().hex
            markdown = add_completion_manifests(
                staged_out, sources, "local-gpu", pdf_metadata, batch_id)
            committed_marker = commit_sources(
                staged_out, sources, batch_id, pdf_metadata, "local-gpu")
        except CommitFailure as error:
            preserve_staging = error.recovery_required
            log(f"ERROR: validation/commit failed: {error}")
            if preserve_staging:
                log("RECOVERY REQUIRED: staging and the pending batch marker were retained")
            return EXIT_PROCESSING
        except Exception as error:
            log(f"ERROR: validation/commit failed: {error}")
            log("no batch commit marker was published")
            return EXIT_PROCESSING

        markdown_count = sum(len(paths) for paths in markdown.values())
        log(f"DONE: {len(sources)}/{len(sources)} sources committed via "
            f"{os.path.basename(committed_marker)}, "
            f"{markdown_count} Markdown file(s)")
        log("next: run the wiki-compile action")
        return 0
    finally:
        if preserve_staging:
            log(f"recovery staging retained at: {staging_dir}")
        else:
            cleanup_staging(staging_dir)


if __name__ == "__main__":
    sys.exit(main())
