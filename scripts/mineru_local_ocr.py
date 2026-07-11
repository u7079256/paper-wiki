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
import time
import unicodedata
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
_STAGING_IDENTITIES = {}


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


def _assert_regular_single_link(path, label):
    """Reject linked, reparsed, special, or hard-linked control/source files."""
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise OSError(f"{label} is missing or unreadable: {path}: {error}") from error
    if (_is_reparse_point(path) or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1):
        raise OSError(f"{label} must be a regular single-link file: {path}")
    return metadata


def _file_identity(metadata):
    """Return the stable identity fields used to detect path replacement."""
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_size)


def _directory_identity(metadata):
    """Return fields that stay stable while directory contents change."""
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))


def _assert_no_reparse_chain(path, allowed_root, label):
    """Reject link/reparse traversal from an explicit input boundary."""
    root = os.path.abspath(allowed_root)
    candidate = os.path.abspath(path)
    try:
        inside = os.path.commonpath([
            os.path.normcase(root), os.path.normcase(candidate)
        ]) == os.path.normcase(root)
    except ValueError:
        inside = False
    if not inside:
        raise OSError(f"{label} escapes its input directory: {candidate}")
    current = root
    relative = os.path.relpath(candidate, root)
    components = [] if relative == "." else relative.split(os.sep)
    for index, component in enumerate([None] + components):
        if component is not None:
            current = os.path.join(current, component)
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise OSError(f"{label} path is missing or unreadable: {current}: {error}") from error
        if _is_reparse_point(current):
            raise OSError(f"{label} traverses a symlink/junction/reparse point: {current}")
        if index < len(components) and not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"{label} ancestor is not a directory: {current}")


def _open_verified_regular(path, label, allowed_root=None):
    """Open without following the final link, then bind path and handle identity.

    No file content is read until the handle, final path, and ancestor chain have
    all been checked.  The post-read caller check catches replacement during a
    snapshot copy without ever consuming the replacement through this handle.
    """
    boundary = os.path.abspath(allowed_root or os.path.dirname(path))
    _assert_no_reparse_chain(path, boundary, label)
    before_path = _assert_regular_single_link(path, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise OSError(f"could not safely open {label} {path}: {error}") from error
    try:
        handle = os.fstat(descriptor)
        _assert_no_reparse_chain(path, boundary, label)
        after_path = _assert_regular_single_link(path, label)
        if (not stat.S_ISREG(handle.st_mode)
                or handle.st_nlink != 1
                or _file_identity(before_path) != _file_identity(handle)
                or _file_identity(after_path) != _file_identity(handle)):
            raise OSError(f"{label} changed between path validation and open: {path}")
        return descriptor, handle, boundary
    except Exception:
        os.close(descriptor)
        raise


def _assert_open_identity(path, descriptor, expected, boundary, label):
    after_handle = os.fstat(descriptor)
    _assert_no_reparse_chain(path, boundary, label)
    after_path = _assert_regular_single_link(path, label)
    if (_file_identity(after_handle) != _file_identity(expected)
            or _file_identity(after_path) != _file_identity(expected)
            or (after_handle.st_mtime_ns, after_handle.st_ctime_ns)
            != (expected.st_mtime_ns, expected.st_ctime_ns)):
        raise OSError(f"{label} changed while it was being copied or hashed: {path}")


def _consume_pdf(path, destination=None, allowed_root=None):
    """Hash a verified PDF handle and optionally create an owner-only snapshot."""
    descriptor, before, boundary = _open_verified_regular(
        path, "PDF", allowed_root=allowed_root)
    output = None
    try:
        if destination is not None:
            output = open(destination, "xb", buffering=0)
            try:
                os.chmod(destination, 0o600)
            except OSError:
                pass
        digest = hashlib.sha256()
        header = b""
        tail = b""
        size = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if len(header) < 5:
                    header = (header + chunk)[:5]
                digest.update(chunk)
                size += len(chunk)
                tail = (tail + chunk)[-4096:]
                if output is not None:
                    output.write(chunk)
            _assert_open_identity(path, descriptor, before, boundary, "PDF")
        if header != b"%PDF-":
            raise ValueError(f"missing %PDF- header: {os.path.basename(path)}")
        if not tail.rstrip(b" \t\r\n\f").endswith(b"%%EOF"):
            raise ValueError(f"missing trailing %%EOF marker: {os.path.basename(path)}")
        if output is not None:
            output.flush()
            os.fsync(output.fileno())
        return {"size": size, "sha256": digest.hexdigest()}
    except OSError as error:
        raise ValueError(f"could not read PDF {path}: {error}") from error
    finally:
        if output is not None:
            output.close()
        os.close(descriptor)


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


def inspect_pdf(path, allowed_root=None):
    """Validate strict framing from an identity-bound, no-follow file handle."""
    return _consume_pdf(path, allowed_root=allowed_root)


def snapshot_pdf(path, destination, allowed_root):
    """Create the only PDF copy that MinerU is allowed to consume."""
    try:
        return _consume_pdf(path, destination=destination, allowed_root=allowed_root)
    except Exception:
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise


def inspect_pdfs(pdfs, allowed_root=None):
    return {path: inspect_pdf(path, allowed_root=allowed_root) for path in pdfs}


def recheck_pdfs(expected, allowed_root=None):
    for path, metadata in expected.items():
        current = inspect_pdf(path, allowed_root=allowed_root)
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


def _safe_entry_name(name):
    """Return whether a source name is portable across Windows and Linux."""
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
    if (base in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", base)):
        return False
    return True


def _portable_name_key(name):
    return unicodedata.normalize("NFC", name).casefold()


def _safe_relative_posix_path(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    if "\\" in value or os.path.splitdrive(value)[0]:
        return False
    components = value.split("/")
    return all(_safe_entry_name(component) for component in components)


def _portable_relative_key(value):
    return "/".join(_portable_name_key(component) for component in value.split("/"))


def source_map(pdfs):
    """Return source-name -> PDF, rejecting ambiguous destination names."""
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


def source_pdf_project_path(pdf):
    """Return a stable project-relative path for PDFs already under raw/<topic>.

    External flat staging inputs remain supported and return ``None``.  A PDF
    that is lexically under the topic must pass the same no-link/no-reparse
    containment checks as publication paths before its path is recorded.
    """
    topic = os.path.abspath(DEFAULT_IN)
    candidate = os.path.abspath(pdf)
    try:
        lexically_inside = os.path.commonpath([
            os.path.normcase(topic), os.path.normcase(candidate)
        ]) == os.path.normcase(topic)
    except ValueError:
        lexically_inside = False
    if not lexically_inside:
        return None
    assert_safe_project_path(candidate, "source PDF")
    if not _inside(topic, candidate):
        raise OSError(f"source PDF resolves outside raw topic: {candidate}")
    if _inside(LOCAL_OUT, candidate):
        raise OSError(f"source PDF must be outside the mineru output tree: {candidate}")
    relative = os.path.relpath(candidate, WIKI_ROOT).replace(os.sep, "/")
    if not _safe_relative_posix_path(relative):
        raise OSError(f"source PDF project path is not portable: {relative!r}")
    return relative


def output_conflicts(sources):
    assert_safe_project_path(LOCAL_OUT, "OCR output")
    conflicts = []
    if os.path.lexists(LOCAL_OUT):
        if os.path.islink(LOCAL_OUT) or not os.path.isdir(LOCAL_OUT):
            conflicts.append(LOCAL_OUT)
            return conflicts
    requested = {_portable_name_key(source) for source in sources}
    if os.path.isdir(LOCAL_OUT):
        for name in os.listdir(LOCAL_OUT):
            if _portable_name_key(name) in requested:
                conflicts.append(os.path.join(LOCAL_OUT, name))
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
    probe = create_staging_dir("no-replace-probe-")
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
        cleanup_staging(probe)


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
    parent_metadata = os.lstat(STAGING_PARENT)
    staging_metadata = os.lstat(staging_dir)
    if (_is_reparse_point(STAGING_PARENT)
            or _is_reparse_point(staging_dir)
            or not stat.S_ISDIR(parent_metadata.st_mode)
            or not stat.S_ISDIR(staging_metadata.st_mode)):
        raise OSError("OCR staging paths must be real directories")
    _STAGING_IDENTITIES[os.path.abspath(staging_dir)] = {
        "parent": _directory_identity(parent_metadata),
        "stage": _directory_identity(staging_metadata),
    }
    return staging_dir


def _regular_tree_files(source_dir):
    """Return every regular single-link file in a portable, no-link tree."""
    boundary = os.path.realpath(source_dir)
    if (_is_reparse_point(source_dir) or not os.path.isdir(source_dir)
            or not _inside(boundary, source_dir)):
        raise OSError(f"unsafe staged OCR source directory: {source_dir}")
    found = []
    for root, dirs, files in os.walk(source_dir, followlinks=False):
        if not _inside(boundary, root):
            raise OSError(f"staged OCR output escapes its source directory: {root}")
        names_seen = {}
        for name in dirs + files:
            entry = os.path.join(root, name)
            if not _safe_entry_name(name):
                raise OSError(f"non-portable name found in staged OCR output: {entry}")
            key = _portable_name_key(name)
            if key in names_seen:
                raise OSError(
                    "staged OCR names collide after NFC/casefold normalization: "
                    f"{names_seen[key]!r} and {name!r} in {root}")
            names_seen[key] = name
            if _is_reparse_point(entry):
                raise OSError(f"link/reparse point found in staged OCR output: {entry}")
        for name in dirs:
            entry = os.path.join(root, name)
            if not stat.S_ISDIR(os.lstat(entry).st_mode):
                raise OSError(f"non-directory found in staged OCR tree: {entry}")
        for name in files:
            entry = os.path.join(root, name)
            _assert_regular_single_link(entry, "staged OCR file")
            found.append(entry)
    return sorted(found)


def markdown_files(source_dir):
    return [path for path in _regular_tree_files(source_dir)
            if path.lower().endswith(".md")]


def _hash_regular_file(path, source_dir):
    descriptor, before, boundary = _open_verified_regular(
        path, "OCR content file", allowed_root=source_dir)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        _assert_open_identity(
            path, descriptor, before, boundary, "OCR content file")
        return {"size": size, "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def _canonical_content_bytes(files):
    payload = bytearray(b"paper-wiki/ocr-content/v1\n")
    for item in sorted(files, key=lambda value: value["path"].encode("utf-8")):
        payload.extend(item["path"].encode("utf-8"))
        payload.extend(b"\0")
        payload.extend(str(item["size"]).encode("ascii"))
        payload.extend(b"\0")
        payload.extend(item["sha256"].encode("ascii"))
        payload.extend(b"\n")
    return bytes(payload)


def _build_content_fingerprint(source_dir):
    files = []
    for path in _regular_tree_files(source_dir):
        relative = os.path.relpath(path, source_dir).replace(os.sep, "/")
        if relative == COMPLETION_MANIFEST:
            continue
        metadata = _hash_regular_file(path, source_dir)
        files.append({"path": relative, **metadata})
    files.sort(key=lambda value: value["path"].encode("utf-8"))
    return {
        "schema": "paper-wiki/ocr-content/v1",
        "files": files,
        "tree_sha256": hashlib.sha256(_canonical_content_bytes(files)).hexdigest(),
    }


def _validate_content_fingerprint_shape(value, label="content fingerprint"):
    if (not isinstance(value, dict)
            or value.get("schema") != "paper-wiki/ocr-content/v1"):
        raise OSError(f"invalid {label} schema")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise OSError(f"{label} has no files")
    normalized = []
    paths = set()
    portable = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise OSError(f"invalid file record in {label}")
        path = item["path"]
        size = item["size"]
        digest = item["sha256"]
        if (not _safe_relative_posix_path(path)
                or path == COMPLETION_MANIFEST):
            raise OSError(f"unsafe file path in {label}: {path!r}")
        if path in paths:
            raise OSError(f"duplicate file path in {label}: {path!r}")
        key = _portable_relative_key(path)
        if key in portable:
            raise OSError(
                f"portable file collision in {label}: {portable[key]!r} and {path!r}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise OSError(f"invalid file size in {label}: {path!r}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise OSError(f"invalid file SHA-256 in {label}: {path!r}")
        paths.add(path)
        portable[key] = path
        normalized.append({"path": path, "size": size, "sha256": digest})
    normalized.sort(key=lambda item: item["path"].encode("utf-8"))
    tree_digest = value.get("tree_sha256")
    expected_digest = hashlib.sha256(
        _canonical_content_bytes(normalized)).hexdigest()
    if (not isinstance(tree_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", tree_digest)
            or tree_digest != expected_digest):
        raise OSError(f"invalid tree SHA-256 in {label}")
    return {
        "schema": "paper-wiki/ocr-content/v1",
        "files": normalized,
        "tree_sha256": tree_digest,
    }


def _validate_manifest_markdown(source_dir, value, fingerprint):
    if not isinstance(value, list) or not value:
        raise OSError(f"completion manifest has no Markdown: {source_dir}")
    listed = set()
    portable = {}
    for relative in value:
        if not _safe_relative_posix_path(relative):
            raise OSError(f"unsafe Markdown path in completion manifest: {relative!r}")
        if relative in listed:
            raise OSError(f"duplicate Markdown path in completion manifest: {relative!r}")
        key = _portable_relative_key(relative)
        if key in portable:
            raise OSError(
                "completion Markdown paths collide after NFC/casefold normalization: "
                f"{portable[key]!r} and {relative!r}")
        listed.add(relative)
        portable[key] = relative
    declared = _validate_content_fingerprint_shape(
        fingerprint, "completion content fingerprint")
    fingerprint_markdown = {
        item["path"] for item in declared["files"]
        if item["path"].lower().endswith(".md")
    }
    if fingerprint_markdown != listed:
        raise OSError(
            f"completion Markdown set does not match fingerprint: {source_dir}")
    actual = _build_content_fingerprint(source_dir)
    if actual != declared:
        raise OSError(
            f"completion content fingerprint does not match source tree: {source_dir}")
    return sorted(listed), actual


def _tree_identity_snapshot(source_dir):
    """Capture entry identities around the atomic publication rename."""
    snapshot = []
    for root, dirs, files in os.walk(source_dir, followlinks=False):
        names_seen = {}
        for name in dirs + files:
            if not _safe_entry_name(name):
                raise OSError(f"non-portable name found in OCR source tree: {name!r}")
            key = _portable_name_key(name)
            if key in names_seen:
                raise OSError(
                    "OCR source names collide after NFC/casefold normalization: "
                    f"{names_seen[key]!r} and {name!r}")
            names_seen[key] = name
        for name in ["."] + sorted(dirs) + sorted(files):
            entry = root if name == "." else os.path.join(root, name)
            metadata = os.lstat(entry)
            if _is_reparse_point(entry):
                raise OSError(f"link/reparse point found in OCR source tree: {entry}")
            if name in dirs and not stat.S_ISDIR(metadata.st_mode):
                raise OSError(f"non-directory found in OCR source tree: {entry}")
            if name in files and (not stat.S_ISREG(metadata.st_mode)
                                  or metadata.st_nlink != 1):
                raise OSError(f"OCR source file must be regular and single-link: {entry}")
            relative = os.path.relpath(entry, source_dir).replace(os.sep, "/")
            snapshot.append((relative, metadata.st_dev, metadata.st_ino,
                             metadata.st_mode, metadata.st_nlink,
                             metadata.st_size, metadata.st_mtime_ns,
                             metadata.st_ctime_ns))
    return tuple(snapshot)


def batch_marker_name(batch_id, state):
    return f"{BATCH_MARKER_PREFIX}{batch_id}.{state}.json"


def _pending_batch_id_from_marker(marker):
    """Extract only a strict lowercase-hex batch id from a pending basename."""
    name = os.path.basename(marker)
    match = re.fullmatch(
        re.escape(BATCH_MARKER_PREFIX) + r"([0-9a-f]{32})\.pending\.json",
        name)
    if match is None:
        raise OSError(f"invalid pending batch marker name: {marker}")
    return match.group(1)


def add_completion_manifests(staged_out, sources, backend, pdf_metadata, batch_id,
                             pdf_project_paths=None):
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
        content_fingerprint = _build_content_fingerprint(source_dir)
        manifest = {
            "schema": "paper-wiki/ocr-completion/v2",
            "backend": backend,
            "completed_at": completed_at,
            "source": source,
            "source_pdf": os.path.basename(pdf),
            "source_pdf_size": pdf_metadata[pdf]["size"],
            "source_pdf_sha256": pdf_metadata[pdf]["sha256"],
            "source_pdf_project_path": (
                pdf_project_paths[pdf] if pdf_project_paths is not None
                else source_pdf_project_path(pdf)),
            "batch_id": batch_id,
            "batch_commit_marker": committed_marker,
            "state": "requires-batch-commit",
            "commit_rule": "complete only when the batch_commit_marker exists",
            "markdown": relative_markdown,
            "content_fingerprint": content_fingerprint,
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


def _read_json_regular(path, label):
    descriptor, before, boundary = _open_verified_regular(
        path, label, allowed_root=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            value = json.load(stream)
        _assert_open_identity(path, descriptor, before, boundary, label)
        return value
    finally:
        os.close(descriptor)


def _validate_source_record(item, label="batch source"):
    if not isinstance(item, dict):
        raise OSError(f"{label} must be an object")
    source = item.get("source")
    source_pdf = item.get("source_pdf")
    size = item.get("source_pdf_size")
    digest = item.get("source_pdf_sha256")
    project_path = item.get("source_pdf_project_path")
    if not _safe_entry_name(source):
        raise OSError(f"invalid source in {label}: {source!r}")
    if (not _safe_entry_name(source_pdf)
            or not source_pdf.lower().endswith(".pdf")):
        raise OSError(f"invalid PDF basename in {label}: {source_pdf!r}")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise OSError(f"invalid PDF size in {label}: {size!r}")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise OSError(f"invalid PDF SHA-256 in {label}: {digest!r}")
    if project_path is not None and not _safe_relative_posix_path(project_path):
        raise OSError(f"invalid project PDF path in {label}: {project_path!r}")
    _validate_content_fingerprint_shape(
        item.get("content_fingerprint"), f"{label} content fingerprint")
    return item


def _validate_current_project_pdf(item):
    project_path = item.get("source_pdf_project_path")
    if project_path is None:
        return
    candidate = os.path.abspath(os.path.join(WIKI_ROOT, *project_path.split("/")))
    topic = os.path.abspath(DEFAULT_IN)
    try:
        inside_topic = os.path.commonpath([
            os.path.normcase(topic), os.path.normcase(candidate)
        ]) == os.path.normcase(topic)
        inside_output = os.path.commonpath([
            os.path.normcase(os.path.abspath(LOCAL_OUT)), os.path.normcase(candidate)
        ]) == os.path.normcase(os.path.abspath(LOCAL_OUT))
    except ValueError:
        inside_topic = False
        inside_output = True
    if not inside_topic or inside_output:
        raise OSError(f"project PDF path is outside topic or inside mineru: {project_path!r}")
    if os.path.basename(candidate) != item["source_pdf"]:
        raise OSError(f"project PDF basename does not match provenance: {project_path!r}")
    current = inspect_pdf(candidate, allowed_root=topic)
    expected = {
        "size": item["source_pdf_size"],
        "sha256": item["source_pdf_sha256"],
    }
    if current != expected:
        raise OSError(
            f"project PDF changed since OCR: {project_path!r} "
            f"(expected {expected}, got {current})")


def _validate_batch_record(record, marker, resolution=None):
    if not isinstance(record, dict):
        raise OSError(f"batch record must be an object: {marker}")
    batch_id = record.get("batch_id")
    state = resolution or "pending"
    if (record.get("schema") != "paper-wiki/ocr-batch/v2"
            or not re.fullmatch(r"[0-9a-f]{32}", batch_id or "")
            or os.path.basename(marker) != batch_marker_name(batch_id, state)):
        raise OSError(f"invalid {state} batch record: {marker}")
    backend = record.get("backend")
    if not isinstance(backend, str) or not backend:
        raise OSError(f"batch backend is missing: {marker}")
    if resolution is None:
        if "resolution" in record:
            raise OSError(f"pending batch unexpectedly has a resolution: {marker}")
    elif (record.get("resolution") != resolution
          or not isinstance(record.get("resolved_at"), str)
          or not record.get("resolved_at")):
        raise OSError(f"invalid {resolution} resolution record: {marker}")
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        raise OSError(f"batch record has no sources: {marker}")
    records = {}
    for item in sources:
        _validate_source_record(item)
        source = item["source"]
        key = _portable_name_key(source)
        if key in records:
            raise OSError(f"duplicate source in batch record: {source!r}")
        records[key] = item
    return batch_id, records


def _read_pending_record(marker):
    record = _read_json_regular(marker, "pending batch marker")
    if isinstance(record, dict) and record.get("schema") == "paper-wiki/ocr-batch/v1":
        _validate_legacy_v1_batch_record(record, marker)
        raise OSError(
            "unresolved legacy v1 OCR batch cannot be recovered safely; "
            "re-OCR it or use an explicitly reviewed reseal procedure: "
            f"{marker}")
    batch_id, records = _validate_batch_record(record, marker)
    return batch_id, records, record


def _validate_resolution_record(path, state, pending):
    resolved = _read_json_regular(path, f"{state} batch marker")
    _validate_batch_record(resolved, path, resolution=state)
    base = {key: value for key, value in resolved.items()
            if key not in ("resolution", "resolved_at")}
    if base != pending:
        raise OSError(f"{state} marker does not match pending batch: {path}")


def _validate_legacy_v1_batch_record(record, marker, resolution=None):
    """Validate only the historical v1 audit fields needed to skip resolution.

    A resolved v1 pair never becomes eligible OCR content.  This compatibility
    path merely prevents an already-resolved append-only journal from blocking
    unrelated new OCR work; wiki-compile still rejects all v1 content.
    """
    if not isinstance(record, dict):
        raise OSError(f"legacy v1 batch record must be an object: {marker}")
    batch_id = record.get("batch_id")
    state = resolution or "pending"
    if (record.get("schema") != "paper-wiki/ocr-batch/v1"
            or not re.fullmatch(r"[0-9a-f]{32}", batch_id or "")
            or os.path.basename(marker) != batch_marker_name(batch_id, state)):
        raise OSError(f"invalid legacy v1 {state} batch record: {marker}")
    if resolution is None:
        if "resolution" in record or "resolved_at" in record:
            raise OSError(
                f"legacy v1 pending batch unexpectedly has a resolution: {marker}")
    elif (record.get("resolution") != resolution
          or not isinstance(record.get("resolved_at"), str)
          or not record.get("resolved_at")):
        raise OSError(f"invalid legacy v1 {state} resolution record: {marker}")
    sources = record.get("sources")
    if not isinstance(sources, list) or not sources:
        raise OSError(f"legacy v1 batch record has no sources: {marker}")
    seen = set()
    for item in sources:
        source = item.get("source") if isinstance(item, dict) else None
        if (not isinstance(source, str) or source in ("", ".", "..")
                or os.path.basename(source) != source
                or "/" in source or "\\" in source):
            raise OSError(f"invalid source in legacy v1 batch record: {source!r}")
        key = source.casefold()
        if key in seen:
            raise OSError(f"duplicate source in legacy v1 batch record: {source!r}")
        seen.add(key)
    return batch_id


def _validate_resolved_audit_pair(pending_path, resolution_path, state, batch_id):
    """Safely validate one immutable v1/v2 pending+resolution audit pair."""
    pending = _read_json_regular(pending_path, "pending batch marker")
    resolved = _read_json_regular(resolution_path, f"{state} batch marker")
    schema = pending.get("schema") if isinstance(pending, dict) else None
    if schema == "paper-wiki/ocr-batch/v2":
        pending_id, _records = _validate_batch_record(pending, pending_path)
        resolved_id, _records = _validate_batch_record(
            resolved, resolution_path, resolution=state)
    elif schema == "paper-wiki/ocr-batch/v1":
        pending_id = _validate_legacy_v1_batch_record(pending, pending_path)
        resolved_id = _validate_legacy_v1_batch_record(
            resolved, resolution_path, resolution=state)
    else:
        raise OSError(f"invalid resolved batch schema: {pending_path}")
    if pending_id != batch_id or resolved_id != batch_id:
        raise OSError(f"resolved batch id does not match pending basename: {pending_path}")
    base = {key: value for key, value in resolved.items()
            if key not in ("resolution", "resolved_at")}
    if base != pending:
        raise OSError(f"{state} marker does not match pending batch: {resolution_path}")


def pending_batch_markers():
    if not os.path.isdir(LOCAL_OUT):
        return []
    unresolved = []
    for name in os.listdir(LOCAL_OUT):
        if not (name.startswith(BATCH_MARKER_PREFIX)
                and name.endswith(".pending.json")):
            continue
        pending = os.path.join(LOCAL_OUT, name)
        batch_id = _pending_batch_id_from_marker(pending)
        committed = os.path.join(
            LOCAL_OUT, batch_marker_name(batch_id, "committed"))
        aborted = os.path.join(
            LOCAL_OUT, batch_marker_name(batch_id, "aborted"))
        committed_exists = os.path.lexists(committed)
        aborted_exists = os.path.lexists(aborted)
        if committed_exists and aborted_exists:
            raise OSError(f"batch has both committed and aborted markers: {pending}")
        if committed_exists or aborted_exists:
            if committed_exists:
                _validate_resolved_audit_pair(
                    pending, committed, "committed", batch_id)
            else:
                _validate_resolved_audit_pair(
                    pending, aborted, "aborted", batch_id)
            continue
        unresolved.append(pending)
    return sorted(unresolved)


def _publish_batch_resolution(marker, state, record):
    """Append a committed/aborted marker while retaining the pending journal."""
    if state not in ("committed", "aborted"):
        raise ValueError(f"invalid batch resolution: {state}")
    _validate_batch_record(record, marker)
    batch_id = record["batch_id"]
    destination = os.path.join(LOCAL_OUT, batch_marker_name(batch_id, state))
    marker_stage = create_staging_dir("batch-marker-")
    staged_marker = os.path.join(marker_stage, os.path.basename(destination))
    try:
        resolved = dict(record)
        resolved["resolution"] = state
        resolved["resolved_at"] = (
            datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
                "+00:00", "Z"))
        _write_json_exclusive(staged_marker, resolved)
        if state == "committed":
            for item in record["sources"]:
                _validate_pending_source(
                    os.path.join(LOCAL_OUT, item["source"]), batch_id, item,
                    record["backend"])
        atomic_move_no_replace(staged_marker, destination)
        return destination
    finally:
        cleanup_staging(marker_stage)


def _validate_pending_source(source_dir, batch_id, expected, batch_backend):
    assert_safe_project_path(source_dir, "pending OCR source")
    if not os.path.isdir(source_dir) or _is_reparse_point(source_dir):
        raise OSError(f"pending OCR source is not a safe directory: {source_dir}")
    manifest_path = os.path.join(source_dir, COMPLETION_MANIFEST)
    manifest = _read_json_regular(manifest_path, "pending source manifest")
    if (not isinstance(manifest, dict)
            or manifest.get("schema") != "paper-wiki/ocr-completion/v2"
            or manifest.get("state") != "requires-batch-commit"
            or manifest.get("backend") != batch_backend
            or manifest.get("batch_id") != batch_id
            or manifest.get("batch_commit_marker")
            != batch_marker_name(batch_id, "committed")
            or manifest.get("source") != expected.get("source")
            or manifest.get("source") != os.path.basename(source_dir)
            or manifest.get("source_pdf") != expected.get("source_pdf")
            or manifest.get("source_pdf_size") != expected.get("source_pdf_size")
            or manifest.get("source_pdf_sha256") != expected.get("source_pdf_sha256")
            or manifest.get("source_pdf_project_path")
            != expected.get("source_pdf_project_path")
            or manifest.get("content_fingerprint")
            != expected.get("content_fingerprint")):
        raise OSError(f"pending source manifest does not match batch: {source_dir}")
    _validate_source_record(manifest, "completion manifest provenance")
    _validate_manifest_markdown(
        source_dir, manifest.get("markdown"), manifest.get("content_fingerprint"))
    _validate_current_project_pdf(expected)


def recover_pending_batches():
    """Finish fully moved batches or quarantine partial batches before new OCR."""
    for marker in pending_batch_markers():
        batch_id, records, record = _read_pending_record(marker)
        sources = [item["source"] for item in records.values()]
        present = [
            source for source in sources
            if os.path.lexists(os.path.join(LOCAL_OUT, source))
        ]
        for source in present:
            _validate_pending_source(
                os.path.join(LOCAL_OUT, source), batch_id,
                records[_portable_name_key(source)], record["backend"])
        if len(present) == len(sources):
            committed = _publish_batch_resolution(marker, "committed", record)
            log(f"recovered fully published OCR batch: {os.path.basename(committed)}")
            continue
        if not present:
            aborted = _publish_batch_resolution(marker, "aborted", record)
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
            _publish_batch_resolution(marker, "aborted", record)
        except Exception as error:
            raise CommitFailure(
                f"partial batch recovery failed after moving {moved}: {error}; "
                f"pending marker retained at {marker}; recovery data: {recovery_dir}",
                recovery_required=True) from error
        raise CommitFailure(
            f"partial OCR batch was quarantined at {recovery_dir} and marked aborted; "
            "inspect it, then rerun OCR",
            recovery_required=True)


def commit_sources(staged_out, sources, batch_id, pdf_metadata, backend,
                   pdf_project_paths=None):
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
    fingerprints = {
        source: _build_content_fingerprint(os.path.join(staged_out, source))
        for source in sources
    }
    record = {
        "schema": "paper-wiki/ocr-batch/v2",
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
                "source_pdf_project_path": (
                    pdf_project_paths[pdf] if pdf_project_paths is not None
                    else source_pdf_project_path(pdf)),
                "content_fingerprint": fingerprints[source],
            }
            for source, pdf in sources.items()
        ],
    }
    record_by_key = {
        _portable_name_key(item["source"]): item for item in record["sources"]
    }
    tree_snapshots = {}
    for source in sources:
        staged_source = os.path.join(staged_out, source)
        _validate_pending_source(
            staged_source, batch_id,
            record_by_key[_portable_name_key(source)], backend)
        tree_snapshots[source] = _tree_identity_snapshot(staged_source)
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
        for source in moved:
            destination = os.path.join(LOCAL_OUT, source)
            if _tree_identity_snapshot(destination) != tree_snapshots[source]:
                raise OSError(f"OCR source changed during publication: {source}")
        committed_path = _publish_batch_resolution(pending_path, "committed", record)
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
                aborted_path = _publish_batch_resolution(pending_path, "aborted", record)
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


def _clear_directory_fd(directory_fd):
    """Delete children relative to an already verified POSIX directory handle."""
    with os.scandir(directory_fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                if (_directory_identity(os.fstat(child_fd))
                        != _directory_identity(metadata)):
                    raise OSError(f"staging directory changed during cleanup: {name}")
                _clear_directory_fd(child_fd)
                current = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False)
                if (_directory_identity(current) != _directory_identity(metadata)):
                    raise OSError(f"staging directory was replaced during cleanup: {name}")
                os.rmdir(name, dir_fd=directory_fd)
            finally:
                os.close(child_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def cleanup_staging(staging_dir):
    """Remove only the exact staging directory created by this process.

    A failed identity check deliberately leaves the directory in place.  This is
    safer than following a replaced staging parent into an unrelated tree.
    """
    staging_dir = os.path.abspath(staging_dir)
    expected = _STAGING_IDENTITIES.get(staging_dir)
    if expected is None:
        log(f"WARNING: retained untracked staging directory: {staging_dir}")
        return False
    parent = os.path.abspath(STAGING_PARENT)
    if os.path.dirname(staging_dir) != parent:
        log(f"WARNING: retained staging outside its recorded parent: {staging_dir}")
        return False
    try:
        parent_metadata = os.lstat(parent)
        stage_metadata = os.lstat(staging_dir)
        if (_is_reparse_point(parent) or _is_reparse_point(staging_dir)
                or not stat.S_ISDIR(parent_metadata.st_mode)
                or not stat.S_ISDIR(stage_metadata.st_mode)
                or _directory_identity(parent_metadata) != expected["parent"]
                or _directory_identity(stage_metadata) != expected["stage"]):
            raise OSError("staging path identity changed")

        if os.name == "nt":
            assert_safe_project_path(staging_dir, "OCR cleanup staging")
            shutil.rmtree(staging_dir)
        else:
            flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            parent_fd = os.open(parent, flags)
            try:
                if _directory_identity(os.fstat(parent_fd)) != expected["parent"]:
                    raise OSError("staging parent changed while opening cleanup handle")
                name = os.path.basename(staging_dir)
                stage_fd = os.open(name, flags, dir_fd=parent_fd)
                try:
                    if _directory_identity(os.fstat(stage_fd)) != expected["stage"]:
                        raise OSError("staging directory changed while opening cleanup handle")
                    _clear_directory_fd(stage_fd)
                    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if _directory_identity(current) != expected["stage"]:
                        raise OSError("staging directory was replaced before final removal")
                    os.rmdir(name, dir_fd=parent_fd)
                finally:
                    os.close(stage_fd)
            finally:
                os.close(parent_fd)
    except OSError as error:
        log(f"WARNING: retained staging after safe-cleanup refusal: {staging_dir}: {error}")
        return False

    _STAGING_IDENTITIES.pop(staging_dir, None)
    try:
        current_parent = os.lstat(parent)
        if (_directory_identity(current_parent) == expected["parent"]
                and not _is_reparse_point(parent)):
            os.rmdir(parent)
    except OSError:
        pass
    return True


def _posix_live_group_members(group_id):
    """Return non-zombie PIDs in a POSIX process group."""
    result = subprocess.run(
        ["ps", "-eo", "pid=,pgid=,stat="], capture_output=True,
        text=True, check=False, timeout=5)
    if result.returncode != 0:
        raise ProcessTreeTerminationError(
            f"could not inspect process group {group_id}: {result.stderr.strip()}")
    members = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        if int(fields[1]) == int(group_id) and not fields[2].startswith("Z"):
            members.add(int(fields[0]))
    return members


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
    group_id = int(process.pid)
    try:
        os.killpg(group_id, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=1)
        return True
    deadline = time.monotonic() + 5
    members = _posix_live_group_members(group_id)
    while members and time.monotonic() < deadline:
        time.sleep(0.1)
        members = _posix_live_group_members(group_id)
    if members:
        try:
            os.killpg(group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        members = _posix_live_group_members(group_id)
        while members and time.monotonic() < deadline:
            time.sleep(0.1)
            members = _posix_live_group_members(group_id)
    if members:
        raise ProcessTreeTerminationError(
            f"POSIX OCR process group {group_id} survived cleanup: {sorted(members)}")
    process.wait(timeout=5)
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
    snapshot_dir = os.path.join(staging_dir, "input-snapshots")
    os.mkdir(snapshot_dir, 0o700)
    log(f"input : {input_dir}")
    log(f"stage : {staged_out}")
    log(f"commit: {LOCAL_OUT}")
    log(f"PDFs  : {len(pdfs)}")
    log(f"timeout: {timeout_seconds}s per PDF ({LOCAL_TIMEOUT_ENV})")

    failures = []
    preserve_staging = False
    try:
        pdf_metadata = {}
        pdf_snapshots = {}
        pdf_project_paths = {}
        try:
            for source, pdf in sources.items():
                snapshot = os.path.join(snapshot_dir, source + ".pdf")
                pdf_metadata[pdf] = snapshot_pdf(pdf, snapshot, input_dir)
                pdf_snapshots[pdf] = snapshot
                pdf_project_paths[pdf] = source_pdf_project_path(pdf)
        except (OSError, ValueError) as error:
            log(f"ERROR: invalid or unstable PDF: {error}")
            log("abort: use a regular, non-linked PDF with a %PDF- header and "
                "trailing %%EOF marker.")
            return 3

        for index, pdf in enumerate(pdfs, 1):
            name = os.path.basename(pdf)
            log(f"[{index}/{len(pdfs)}] {name}")
            # -b pipeline: the default hybrid auto-engine routes through Qwen2VL
            # and crashes on an mRoPE mismatch. Serial on purpose.
            try:
                return_code, timed_out = run_mineru(
                    pdf_snapshots[pdf], staged_out, timeout_seconds)
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
            recheck_pdfs(pdf_metadata, allowed_root=input_dir)
            batch_id = uuid.uuid4().hex
            markdown = add_completion_manifests(
                staged_out, sources, "local-gpu", pdf_metadata, batch_id,
                pdf_project_paths=pdf_project_paths)
            committed_marker = commit_sources(
                staged_out, sources, batch_id, pdf_metadata, "local-gpu",
                pdf_project_paths=pdf_project_paths)
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
