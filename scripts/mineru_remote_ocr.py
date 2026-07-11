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
import secrets
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
GUARDIAN_WAIT_SECONDS = 7200
GUARDIAN_POLL_SECONDS = 5
REMOTE_ACTIVATION_TIMEOUT_SECONDS = 300
REMOTE_PDF_TIMEOUT_SECONDS = 1200
REMOTE_TIMEOUT_KILL_GRACE_SECONDS = 30
REMOTE_RESULT_TTL_SECONDS = 7200
GUARDIAN_MAX_LEASE_SECONDS = 86400
_STAGING_IDENTITIES = {}


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
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode,
            metadata.st_nlink, metadata.st_size)


def _directory_identity(metadata):
    return (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))


def _assert_no_reparse_chain(path, allowed_root, label):
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
    return _consume_pdf(path, allowed_root=allowed_root)


def snapshot_pdf(path, destination, allowed_root):
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
    return all(_safe_entry_name(component) for component in value.split("/"))


def _portable_relative_key(value):
    return "/".join(_portable_name_key(component) for component in value.split("/"))


def _sftp_identity(attributes):
    mode = getattr(attributes, "st_mode", None)
    size = getattr(attributes, "st_size", None)
    if not isinstance(mode, int) or not isinstance(size, int):
        raise OSError("SFTP server omitted required mode/size identity fields")
    return (
        mode,
        size,
        getattr(attributes, "st_uid", None),
        getattr(attributes, "st_gid", None),
        getattr(attributes, "st_mtime", None),
        getattr(attributes, "st_ino", None),
    )


def _require_sftp_identity(actual, expected, label):
    if _sftp_identity(actual) != _sftp_identity(expected):
        raise OSError(f"SFTP path identity changed: {label}")


def _download_regular_file(sftp, remote_path, local_path, expected=None):
    """Read one SFTP file only after lstat/open/fstat identity binding."""
    before = sftp.lstat(remote_path)
    if expected is not None:
        _require_sftp_identity(before, expected, remote_path)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise OSError(f"SFTP regular file changed type: {remote_path}")

    remote_stream = sftp.open(remote_path, "rb")
    temp_path = None
    try:
        opened = remote_stream.stat()
        after_open = sftp.lstat(remote_path)
        _require_sftp_identity(opened, before, remote_path)
        _require_sftp_identity(after_open, before, remote_path)
        if (stat.S_ISLNK(after_open.st_mode)
                or not stat.S_ISREG(opened.st_mode)):
            raise OSError(f"SFTP file became linked or non-regular: {remote_path}")

        local_parent = os.path.dirname(local_path)
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".paper-wiki-sftp-", dir=local_parent)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "wb") as local_stream:
            while True:
                chunk = remote_stream.read(1024 * 1024)
                if not chunk:
                    break
                local_stream.write(chunk)
            local_stream.flush()
            os.fsync(local_stream.fileno())

        opened_after = remote_stream.stat()
        path_after = sftp.lstat(remote_path)
        _require_sftp_identity(opened_after, before, remote_path)
        _require_sftp_identity(path_after, before, remote_path)
        if os.path.lexists(local_path):
            raise OSError(f"duplicate local staging entry: {local_path}")
        atomic_move_no_replace(temp_path, local_path)
        temp_path = None
    finally:
        try:
            remote_stream.close()
        except Exception:
            pass
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def download_tree(sftp, remote_root, local_root, _local_boundary=None,
                  _remote_expected=None):
    """Download an identity-bound regular tree without following remote links."""
    boundary = os.path.realpath(_local_boundary or local_root)
    os.makedirs(local_root, exist_ok=True)
    if _is_reparse_point(local_root) or not _inside(boundary, local_root):
        raise OSError(f"unsafe local staging directory: {local_root}")
    root_before = sftp.lstat(remote_root)
    if _remote_expected is not None:
        _require_sftp_identity(root_before, _remote_expected, remote_root)
    if stat.S_ISLNK(root_before.st_mode) or not stat.S_ISDIR(root_before.st_mode):
        raise OSError(f"unsafe SFTP directory: {remote_root}")
    entries = sftp.listdir_attr(remote_root)
    root_after_list = sftp.lstat(remote_root)
    _require_sftp_identity(root_after_list, root_before, remote_root)
    count = 0
    names_seen = {}
    for entry in entries:
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

        current = sftp.lstat(remote_path)
        _require_sftp_identity(current, entry, remote_path)
        mode = current.st_mode
        if stat.S_ISLNK(mode):
            raise OSError(f"SFTP symlink rejected: {remote_path}")
        if stat.S_ISDIR(mode):
            if os.path.lexists(local_path) and not os.path.isdir(local_path):
                raise OSError(f"local staging collision: {local_path}")
            count += download_tree(
                sftp, remote_path, local_path, _local_boundary=boundary,
                _remote_expected=current)
        elif stat.S_ISREG(mode):
            if os.path.lexists(local_path):
                raise OSError(f"duplicate local staging entry: {local_path}")
            _download_regular_file(
                sftp, remote_path, local_path, expected=current)
            count += 1
        else:
            raise OSError(f"non-regular SFTP entry rejected: {remote_path}")
    root_after = sftp.lstat(remote_root)
    _require_sftp_identity(root_after, root_before, remote_root)
    return count


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
                if _directory_identity(current) != _directory_identity(metadata):
                    raise OSError(f"staging directory was replaced during cleanup: {name}")
                os.rmdir(name, dir_fd=directory_fd)
            finally:
                os.close(child_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def cleanup_staging(staging_dir):
    """Remove only the exact staging directory created by this process."""
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


def _guardian_hard_deadline(pdf_count):
    requested = (GUARDIAN_WAIT_SECONDS + REMOTE_ACTIVATION_TIMEOUT_SECONDS
                 + max(1, int(pdf_count))
                 * (REMOTE_PDF_TIMEOUT_SECONDS
                    + REMOTE_TIMEOUT_KILL_GRACE_SECONDS + 60)
                 + REMOTE_RESULT_TTL_SECONDS)
    return min(GUARDIAN_MAX_LEASE_SECONDS, requested)


def remote_paths(root, pdf_count=1, run_token=None):
    return {
        "root": root,
        "run_token": run_token or secrets.token_hex(32),
        "pre_handoff_deadline": GUARDIAN_WAIT_SECONDS,
        "hard_deadline": _guardian_hard_deadline(pdf_count),
        "in": posixpath.join(root, "input"),
        "out": posixpath.join(root, "output"),
        "log": posixpath.join(root, "driver.log"),
        "driver_identity": posixpath.join(root, "driver.identity"),
        "status": posixpath.join(root, "status"),
        "status_tmp": posixpath.join(root, "status.tmp"),
        "driver": posixpath.join(root, "driver.sh"),
        "cleanup_ack": posixpath.join(root, "cleanup.ack"),
        "guardian": posixpath.join(root, "guardian.sh"),
        "guardian_identity": posixpath.join(root, "guardian.identity"),
        "guardian_log": posixpath.join(root, "guardian.log"),
        "guardian_handoff": posixpath.join(root, "guardian.handoff"),
    }


def _valid_remote_root(root):
    prefix = f"/tmp/mineru_{NS}_"
    suffix = root[len(prefix):] if root.startswith(prefix) else ""
    return bool(suffix and re.fullmatch(r"[A-Za-z0-9]+", suffix))


def _identity_check_shell(identity_path, run_token, function_name,
                          allow_orphaned_session=False):
    identity = shlex.quote(identity_path)
    token = shlex.quote(run_token)
    orphaned = """
  [ ! -e \"/proc/$pid\" ] || return 1
  members=$(ps -eo pid=,sid= | awk -v session=\"$pid\" '$2 == session {print $1}')
  [ -n \"$members\" ] || return 1
  printf '%s\\n' \"$pid\"
  return 0
""" if allow_orphaned_session else "  return 1\n"
    return f"""{function_name}() {{
  [ -f {identity} ] && [ ! -L {identity} ] && [ -O {identity} ] || return 1
  [ "$(wc -l < {identity} 2>/dev/null || echo 0)" = 1 ] || return 1
  IFS=' ' read -r saved_token saved_boot pid saved_start saved_sid extra < {identity} || return 1
  [ -z "$extra" ] && [ "$saved_token" = {token} ] || return 1
  case "$pid:$saved_start:$saved_sid" in (*[!0-9:]*|*::*|:*|*:) return 1 ;; esac
  [ "$saved_sid" = "$pid" ] || return 1
  current_boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
  [ -n "$current_boot" ] && [ "$saved_boot" = "$current_boot" ] || return 1
  current_start=$(awk '{{print $22}}' "/proc/$pid/stat" 2>/dev/null || true)
  current_sid=$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  if [ -n "$current_start" ] || [ -n "$current_sid" ]; then
    [ "$saved_start" = "$current_start" ] && [ "$current_sid" = "$pid" ] \
      || return 1
    printf '%s\n' "$pid"
    return 0
  fi
{orphaned}}}
"""


def build_guardian(paths):
    """Build a pre-upload guardian that owns cleanup until the driver takes over.

    The guardian starts before any PDF leaves the client.  Before handoff it
    removes an abandoned private workspace after two hours.  After handoff it
    watches the verified driver session and removes the root if that session
    disappears without running its EXIT trap.
    """
    root = shlex.quote(paths["root"])
    identity = shlex.quote(paths["driver_identity"])
    handoff = shlex.quote(paths["guardian_handoff"])
    identity_check = _identity_check_shell(
        paths["driver_identity"], paths["run_token"], "driver_session",
        allow_orphaned_session=True)
    return f"""#!/bin/bash
set -u
umask 077
{identity_check}
remove_root() {{
  if [ -d {root} ] && [ ! -L {root} ] && [ -O {root} ]; then
    rm -rf -- {root}
  fi
}}
handoff_ready() {{
  [ -f {handoff} ] && [ ! -L {handoff} ] && [ -O {handoff} ] \
    && [ -f {identity} ] && cmp -s {handoff} {identity}
}}
terminate_driver_session() {{
  session="${{1:-}}"
  [ -n "$session" ] || session=$(driver_session) || return 0
  signal_session TERM "$session"
  waited_for_exit=0
  members=$(session_members "$session")
  while [ -n "$members" ] && [ "$waited_for_exit" -lt {REMOTE_TIMEOUT_KILL_GRACE_SECONDS} ]; do
    sleep 1
    waited_for_exit=$((waited_for_exit + 1))
    members=$(session_members "$session")
  done
  if [ -n "$members" ]; then
    current=$(driver_session 2>/dev/null || true)
    [ "$current" = "$session" ] && signal_session KILL "$session"
  fi
}}
session_members() {{
  ps -eo pid=,sid= | awk -v session="$1" '$2 == session {{print $1}}'
}}
signal_session() {{
  signal_name="$1"
  session="$2"
  while read -r member member_sid; do
    case "$member:$member_sid" in (*[!0-9:]*|*::*|:*|*:) continue ;; esac
    [ "$member_sid" = "$session" ] || continue
    current_sid=$(ps -o sid= -p "$member" 2>/dev/null | tr -d ' ' || true)
    [ "$current_sid" = "$session" ] || continue
    kill -"$signal_name" "$member" 2>/dev/null || true
  done < <(ps -eo pid=,sid=)
}}
started_at=$(date +%s)
pre_handoff_deadline=$((started_at + {paths['pre_handoff_deadline']}))
hard_deadline=$((started_at + {paths['hard_deadline']}))
authorized_sid=''
while [ -d {root} ] && [ ! -L {root} ] && [ -O {root} ]; do
  now=$(date +%s)
  if [ "$now" -ge "$hard_deadline" ]; then
    sid=$(driver_session 2>/dev/null || true)
    if [ -n "$authorized_sid" ] && [ "$sid" != "$authorized_sid" ]; then
      sid=''
    fi
    [ -z "$sid" ] || terminate_driver_session "$sid"
    remove_root
    exit 0
  fi
  if handoff_ready; then
    current_sid=$(driver_session) || {{ remove_root; exit 0; }}
    if [ -z "$authorized_sid" ]; then authorized_sid="$current_sid"; fi
    [ "$current_sid" = "$authorized_sid" ] || {{ remove_root; exit 0; }}
    if [ -n "$(session_members "$authorized_sid")" ]; then
      sleep {GUARDIAN_POLL_SECONDS}
      continue
    fi
    remove_root
    exit 0
  fi
  if [ "$now" -ge "$pre_handoff_deadline" ]; then
    sid=$(driver_session 2>/dev/null || true)
    [ -z "$sid" ] || terminate_driver_session "$sid"
    remove_root
    exit 0
  fi
  sleep {GUARDIAN_POLL_SECONDS}
done
exit 0
"""


def install_guardian(client, paths):
    """Upload and launch the cleanup guardian before uploading any PDF."""
    sftp = open_sftp(client)
    try:
        with sftp.open(paths["guardian"], "w") as stream:
            stream.write(build_guardian(paths))
    finally:
        try:
            sftp.close()
        except Exception:
            pass

    root = shlex.quote(paths["root"])
    guardian = shlex.quote(paths["guardian"])
    guardian_log = shlex.quote(paths["guardian_log"])
    guardian_identity = shlex.quote(paths["guardian_identity"])
    token = shlex.quote(paths["run_token"])
    command = (
        "umask 077; "
        f"chmod 700 {guardian} || exit 1; "
        f"cd {root} && command -v setsid >/dev/null 2>&1 || exit 1; "
        f"nohup setsid bash {guardian} > {guardian_log} 2>&1 < /dev/null & "
        "pid=$!; sleep 1; "
        "sid=$(ps -o sid= -p \"$pid\" 2>/dev/null | tr -d ' '); "
        "start=$(awk '{print $22}' \"/proc/$pid/stat\" 2>/dev/null); "
        "boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null); "
        "[ \"$sid\" = \"$pid\" ] && [ -n \"$start\" ] && [ -n \"$boot\" ] || "
        "{ kill -TERM \"$pid\" 2>/dev/null || true; "
        "sleep 1; kill -KILL \"$pid\" 2>/dev/null || true; exit 1; }; "
        f"tmp={guardian_identity}.tmp.$$; "
        f"printf '%s %s %s %s %s\\n' {token} \"$boot\" \"$pid\" \"$start\" \"$sid\" > \"$tmp\"; "
        "sync -f \"$tmp\" || exit 1; "
        f"mv -f \"$tmp\" {guardian_identity} || exit 1; "
        "kill -0 \"$pid\" 2>/dev/null || exit 1")
    rc, _out, err = run_cmd(client, command, timeout=10)
    if rc != 0:
        raise OSError(f"remote cleanup guardian launch failed: {err.strip()}")


def create_remote_workspace(client, pdf_count=1):
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
    return remote_paths(root, pdf_count=pdf_count)


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
write_status() {{
  printf '%s\n' "$1" > {status_tmp} && mv -f {status_tmp} {status}
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
  while [ -n "$members" ] && [ "$waited" -lt {REMOTE_TIMEOUT_KILL_GRACE_SECONDS} ]; do
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
  while [ ! -f {cleanup_ack} ] && [ "$waited" -lt {REMOTE_RESULT_TTL_SECONDS} ]; do
    sleep {GUARDIAN_POLL_SECONDS}
    waited=$((waited + {GUARDIAN_POLL_SECONDS}))
  done
}}
# MINERU_REMOTE_ACTIVATE is a trusted administrator shell snippet and is the only
# intentionally unquoted configuration value in this file.
activation_watchdog() {{
  sleep {REMOTE_ACTIVATION_TIMEOUT_SECONDS}
  watchdog_pid="$BASHPID"
  members=$(ps -eo pid=,sid= | awk -v me="$watchdog_pid" -v session="$$" \
    '$2 == session && $1 != me {{print $1}}')
  [ -z "$members" ] || kill -TERM $members 2>/dev/null || true
  waited=0
  while [ -n "$members" ] && [ "$waited" -lt {REMOTE_TIMEOUT_KILL_GRACE_SECONDS} ]; do
    sleep 1
    waited=$((waited + 1))
    members=$(ps -eo pid=,sid= | awk -v me="$watchdog_pid" -v session="$$" \
      '$2 == session && $1 != me {{print $1}}')
  done
  [ -z "$members" ] || kill -KILL $members 2>/dev/null || true
  rm -rf -- {remote_root}
}}
activation_watchdog &
activation_watchdog_pid=$!
{ACTIVATE}
activate_rc=$?
kill -TERM "$activation_watchdog_pid" 2>/dev/null || true
wait "$activation_watchdog_pid" 2>/dev/null || true
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
  timeout --signal=TERM --kill-after={REMOTE_TIMEOUT_KILL_GRACE_SECONDS}s \
    {REMOTE_PDF_TIMEOUT_SECONDS}s mineru -p "$pdf" -o {remote_out} -b pipeline 2>&1 &
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


def build_driver_launch(paths):
    """Launch the driver and atomically publish its authenticated handoff."""
    root = shlex.quote(paths["root"])
    driver = shlex.quote(paths["driver"])
    log_path = shlex.quote(paths["log"])
    identity = shlex.quote(paths["driver_identity"])
    handoff = shlex.quote(paths["guardian_handoff"])
    token = shlex.quote(paths["run_token"])
    return (
        "umask 077; "
        f"cd {root} && command -v setsid >/dev/null 2>&1 || exit 1; "
        f"[ ! -e {identity} ] && [ ! -e {handoff} ] || exit 1; "
        f"nohup setsid bash {driver} > {log_path} 2>&1 < /dev/null & "
        "pid=$!; sleep 1; "
        "sid=$(ps -o sid= -p \"$pid\" 2>/dev/null | tr -d ' '); "
        "start=$(awk '{print $22}' \"/proc/$pid/stat\" 2>/dev/null); "
        "boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null); "
        "[ \"$sid\" = \"$pid\" ] && [ -n \"$start\" ] && [ -n \"$boot\" ] || "
        "{ kill -TERM \"$pid\" 2>/dev/null || true; "
        "sleep 1; kill -KILL \"$pid\" 2>/dev/null || true; exit 1; }; "
        f"identity_tmp={identity}.tmp.$$; "
        f"handoff_tmp={handoff}.tmp.$$; "
        f"printf '%s %s %s %s %s\\n' {token} \"$boot\" \"$pid\" \"$start\" \"$sid\" > \"$identity_tmp\"; "
        "sync -f \"$identity_tmp\" || exit 1; "
        f"mv -f \"$identity_tmp\" {identity} || exit 1; "
        f"printf '%s %s %s %s %s\\n' {token} \"$boot\" \"$pid\" \"$start\" \"$sid\" > \"$handoff_tmp\"; "
        "sync -f \"$handoff_tmp\" || exit 1; "
        f"[ ! -e {handoff} ] || exit 1; "
        f"mv -f \"$handoff_tmp\" {handoff} || exit 1; "
        "kill -0 \"$pid\" 2>/dev/null || exit 1")


def _close(client):
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def cleanup_remote_workspace(paths):
    """Confirm client cleanup; guardian/driver traps cover reconnect failure."""
    if not paths or not _valid_remote_root(paths["root"]):
        return
    client = None
    try:
        client = connect()
        root = shlex.quote(paths["root"])
        status_path = shlex.quote(paths["status"])
        ack_path = shlex.quote(paths["cleanup_ack"])
        driver_check = _identity_check_shell(
            paths["driver_identity"], paths["run_token"], "verified_driver",
            allow_orphaned_session=True)
        guardian_check = _identity_check_shell(
            paths["guardian_identity"], paths["run_token"], "verified_guardian")
        command = f"""
{driver_check}
{guardian_check}
session_members() {{
  ps -eo pid=,sid= | awk -v session="$1" '$2 == session {{print $1}}'
}}
signal_session() {{
  signal_name="$1"
  session="$2"
  while read -r member member_sid; do
    case "$member:$member_sid" in (*[!0-9:]*|*::*|:*|*:) continue ;; esac
    [ "$member_sid" = "$session" ] || continue
    current_sid=$(ps -o sid= -p "$member" 2>/dev/null | tr -d ' ' || true)
    [ "$current_sid" = "$session" ] || continue
    kill -"$signal_name" "$member" 2>/dev/null || true
  done < <(ps -eo pid=,sid=)
}}
if [ -d {root} ] && [ ! -L {root} ] && [ -O {root} ]; then
  state=''
  [ -f {status_path} ] && state=$(cat {status_path} 2>/dev/null || true)
  pid=$(verified_driver 2>/dev/null || true)
  if [ -n "$pid" ]; then
    if [ "$state" = DONE ] || [ "$state" = FAILED ]; then
      : > {ack_path}
      sleep {GUARDIAN_POLL_SECONDS}
    fi
    current=$(verified_driver 2>/dev/null || true)
    [ "$current" = "$pid" ] && signal_session TERM "$pid"
    waited=0
    members=$(session_members "$pid")
    while [ -n "$members" ] && [ "$waited" -lt {REMOTE_TIMEOUT_KILL_GRACE_SECONDS} ]; do
      sleep 1
      waited=$((waited + 1))
      members=$(session_members "$pid")
    done
    if [ -n "$members" ]; then
      current=$(verified_driver 2>/dev/null || true)
      [ "$current" = "$pid" ] && signal_session KILL "$pid"
      sleep 1
    fi
  fi
  guardian_pid=$(verified_guardian 2>/dev/null || true)
  if [ -n "$guardian_pid" ]; then
    current=$(verified_guardian 2>/dev/null || true)
    if [ "$current" = "$guardian_pid" ]; then
      kill -TERM "$guardian_pid" 2>/dev/null || true
    fi
    waited=0
    current=$(verified_guardian 2>/dev/null || true)
    while [ "$current" = "$guardian_pid" ] && [ "$waited" -lt {REMOTE_TIMEOUT_KILL_GRACE_SECONDS} ]; do
      sleep 1
      waited=$((waited + 1))
      current=$(verified_guardian 2>/dev/null || true)
    done
    current=$(verified_guardian 2>/dev/null || true)
    if [ "$current" = "$guardian_pid" ]; then
      kill -KILL "$guardian_pid" 2>/dev/null || true
      sleep 1
    fi
  fi
  [ ! -e {root} ] || rm -rf -- {root}
  [ ! -e {root} ] || exit 1
fi
"""
        cleanup_timeout = max(
            SSH_TIMEOUT_SECONDS,
            2 * REMOTE_TIMEOUT_KILL_GRACE_SECONDS + GUARDIAN_POLL_SECONDS + 15)
        rc, _out, err = run_cmd(client, command, timeout=cleanup_timeout)
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


def run_remote_pipeline(pdfs, sources, pdf_metadata, started_at, input_dir=None):
    paths = None
    staging_dir = None
    preserve_staging = False
    try:
        input_dir = os.path.abspath(input_dir or os.path.dirname(pdfs[0]))
        staging_dir = create_staging_dir("remote-")
        snapshot_dir = os.path.join(staging_dir, "input-snapshots")
        os.mkdir(snapshot_dir, 0o700)
        staged_out = os.path.join(staging_dir, "output")
        snapshot_metadata = {}
        pdf_snapshots = {}
        pdf_project_paths = {}
        try:
            for source, pdf in sources.items():
                snapshot = os.path.join(snapshot_dir, source + ".pdf")
                snapshot_metadata[pdf] = snapshot_pdf(pdf, snapshot, input_dir)
                pdf_snapshots[pdf] = snapshot
                pdf_project_paths[pdf] = source_pdf_project_path(pdf)
        except (OSError, ValueError) as error:
            log(f"ERROR: invalid or unstable PDF: {error}")
            return 3
        if pdf_metadata is not None and snapshot_metadata != pdf_metadata:
            log("ERROR: PDF changed between caller preflight and private snapshot")
            return 3
        pdf_metadata = snapshot_metadata

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

            paths = create_remote_workspace(client, pdf_count=len(pdfs))
            log(f"private remote workspace: {paths['root']}")
            install_guardian(client, paths)
            log("pre-upload cleanup guardian active")
            sftp = open_sftp(client)
            try:
                for index, (source, pdf) in enumerate(sources.items(), 1):
                    # Normalize every extension to lowercase so .PDF behaves like .pdf.
                    remote_pdf = posixpath.join(paths["in"], source + ".pdf")
                    sftp.put(pdf_snapshots[pdf], remote_pdf)
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
            launch = build_driver_launch(paths)
            rc, _out, err = run_cmd(client, launch, timeout=10)
            if rc != 0:
                raise OSError(f"remote launch failed: {err.strip()}")
        finally:
            _close(client)

        time.sleep(3)
        client = connect()
        try:
            identity_check = _identity_check_shell(
                paths["driver_identity"], paths["run_token"], "verified_driver")
            rc, out, err = run_cmd(
                client, identity_check + "verified_driver", timeout=10)
            pid = out.strip()
            if rc != 0 or not re.fullmatch(r"[0-9]+", pid):
                raise OSError(
                    f"remote driver PID is missing or non-numeric: {pid!r} {err.strip()}")
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
                    identity_check
                    +
                    f"if [ -f {status_path} ]; then cat {status_path}; "
                    "elif verified_driver >/dev/null 2>&1; then echo ALIVE; "
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

        client = connect()
        sftp = None
        try:
            sftp = open_sftp(client)
            file_count = download_tree(sftp, paths["out"], staged_out)
            _download_regular_file(
                sftp, paths["log"],
                os.path.join(staging_dir, "_serial_remote.log"))
            log(f"downloaded {file_count} file(s) into project-local staging")
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            _close(client)

        recheck_pdfs(pdf_metadata, allowed_root=input_dir)
        batch_id = uuid.uuid4().hex
        markdown = add_completion_manifests(
            staged_out, sources, "remote-gpu", pdf_metadata, batch_id,
            pdf_project_paths=pdf_project_paths)
        try:
            committed_marker = commit_sources(
                staged_out, sources, batch_id, pdf_metadata, "remote-gpu",
                pdf_project_paths=pdf_project_paths)
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
        try:
            cleanup_remote_workspace(paths)
        finally:
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

    log(f"input dir: {input_dir}")
    log(f"PDFs to process: {len(pdfs)}")
    return run_remote_pipeline(
        pdfs, sources, None, time.time(), input_dir=input_dir)


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
