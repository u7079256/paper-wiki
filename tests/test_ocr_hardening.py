"""No-GPU/SSH regression tests for Paper Wiki OCR safety boundaries."""

import ast
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def run_bash(script, timeout=20):
    """Run WSL/bash with LF bytes so PowerShell cannot inject CRLF."""
    return subprocess.run(
        ["bash"], input=script.encode("utf-8"), capture_output=True,
        check=False, timeout=timeout, cwd=ROOT)


def require_linux_bash(testcase, *, python=False):
    """Skip only when the real Linux capabilities used by a test are absent."""
    commands = ["setsid", "ps", "awk", "readlink"]
    if python:
        commands.append("python3")
    probe = "set -eu\n" + "\n".join(
        f"command -v {command} >/dev/null" for command in commands)
    probe += "\ntest -r /proc/sys/kernel/random/boot_id\ntest -r /proc/self/stat\n"
    try:
        result = run_bash(probe, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        testcase.skipTest(f"Linux bash runtime unavailable: {error}")
    if result.returncode != 0:
        testcase.skipTest(
            "Linux bash capabilities unavailable: "
            + result.stderr.decode(errors="replace"))


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def patched(module, **values):
    old = {name: getattr(module, name) for name in values}
    try:
        for name, value in values.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in old.items():
            setattr(module, name, value)


@contextlib.contextmanager
def project_paths(module):
    root = tempfile.mkdtemp(prefix="paper-wiki-ocr-test-")
    topic = os.path.join(root, "raw", "topic")
    os.makedirs(topic)
    values = {
        "WIKI_ROOT": root,
        "DEFAULT_IN": topic,
        "LOCAL_OUT": os.path.join(topic, "mineru"),
        "STAGING_PARENT": os.path.join(root, ".paper-wiki", "ocr-staging"),
    }
    with patched(module, **values):
        try:
            yield root, topic
        finally:
            for path in list(module._STAGING_IDENTITIES):
                if os.path.commonpath([root, path]) == root:
                    module._STAGING_IDENTITIES.pop(path, None)
    shutil.rmtree(root, ignore_errors=True)


class OcrHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.local = load_module("paper_wiki_local_test", "scripts/mineru_local_ocr.py")
        cls.remote = load_module("paper_wiki_remote_test", "scripts/mineru_remote_ocr.py")

    def test_python_sources_parse(self):
        for relative in ("scripts/mineru_local_ocr.py", "scripts/mineru_remote_ocr.py"):
            ast.parse((ROOT / relative).read_text(encoding="utf-8"))

    def test_local_and_remote_share_portable_source_name_rules(self):
        invalid = (
            "", ".", "..", "a/b", "a\\b", "C:ads", "CON", "NUL.txt",
            "CONIN$.md", "CONOUT$.md", "COM¹.md", "COM².md", "COM³.md",
            "LPT¹.md", "LPT².md", "LPT³.md", "com1.foo", "lpt9.md",
            "bad.", "bad ", "bad<name", "line\nfeed",
        )
        for name in invalid:
            self.assertFalse(self.local._safe_entry_name(name), name)
            self.assertFalse(self.remote._safe_entry_name(name), name)
        for name in ("paper", "论文-一", "a_b-2"):
            self.assertTrue(self.local._safe_entry_name(name), name)
            self.assertTrue(self.remote._safe_entry_name(name), name)
        composed = os.path.join("x", "é.pdf")
        decomposed = os.path.join("x", "e\u0301.pdf")
        with self.assertRaises(ValueError):
            self.local.source_map([composed, decomposed])

    def test_existing_output_portable_collision_is_rejected(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (_root, topic):
                output = Path(topic, "mineru")
                output.mkdir()
                (output / "Étude").mkdir()
                conflicts = module.output_conflicts({"étude": "unused.pdf"})
                self.assertEqual(len(conflicts), 1)
                self.assertEqual(Path(conflicts[0]).name, "Étude")

    def test_project_pdf_path_is_recorded_only_for_safe_topic_inputs(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (root, topic):
                pdf = os.path.join(topic, "paper.pdf")
                Path(pdf).write_bytes(b"%PDF-1.7\n%%EOF")
                self.assertEqual(
                    module.source_pdf_project_path(pdf), "raw/topic/paper.pdf")
                external = os.path.join(tempfile.gettempdir(), "external-paper.pdf")
                self.assertIsNone(module.source_pdf_project_path(external))

    def test_completion_and_batch_records_bind_project_pdf_path(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (_root, topic):
                pdf = os.path.join(topic, "paper.pdf")
                payload = b"%PDF-1.7\n%%EOF"
                Path(pdf).write_bytes(payload)
                metadata = {pdf: {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }}
                os.makedirs(module.STAGING_PARENT)
                staging = module.create_staging_dir("record-")
                staged_out = os.path.join(staging, "output")
                source_dir = os.path.join(staged_out, "paper", "auto")
                os.makedirs(source_dir)
                Path(source_dir, "paper.md").write_text("# paper\n", encoding="utf-8")
                batch_id = "a" * 32
                module.add_completion_manifests(
                    staged_out, {"paper": pdf}, "test", metadata, batch_id)
                manifest = __import__("json").loads(
                    Path(staged_out, "paper", module.COMPLETION_MANIFEST)
                    .read_text(encoding="utf-8"))
                self.assertEqual(manifest["source_pdf_project_path"],
                                 "raw/topic/paper.pdf")
                self.assertEqual(manifest["schema"], "paper-wiki/ocr-completion/v2")
                self.assertEqual(
                    manifest["content_fingerprint"]["schema"],
                    "paper-wiki/ocr-content/v1")
                marker = module.commit_sources(
                    staged_out, {"paper": pdf}, batch_id, metadata, "test")
                batch = __import__("json").loads(Path(marker).read_text(encoding="utf-8"))
                self.assertEqual(batch["sources"][0]["source_pdf_project_path"],
                                 "raw/topic/paper.pdf")
                self.assertEqual(batch["schema"], "paper-wiki/ocr-batch/v2")
                self.assertEqual(
                    batch["sources"][0]["content_fingerprint"],
                    manifest["content_fingerprint"])

    def test_staged_markdown_hard_links_are_rejected(self):
        for module in (self.local, self.remote):
            root = tempfile.mkdtemp(prefix="paper-wiki-hardlink-test-")
            try:
                source = Path(root, "source")
                source.mkdir()
                first = source / "first.md"
                second = source / "second.md"
                first.write_text("# source\n", encoding="utf-8")
                os.link(first, second)
                with self.assertRaisesRegex(OSError, "single-link"):
                    module.markdown_files(str(source))
            finally:
                shutil.rmtree(root, ignore_errors=True)

    def test_snapshot_rejects_path_swap_before_read_for_both_scripts(self):
        require_linux_bash(self, python=True)
        code = r'''
import hashlib, importlib.util, os, pathlib, shutil, sys, tempfile, types
repo = pathlib.Path.cwd()
sys.modules.setdefault('paramiko', types.SimpleNamespace())
for filename in ('mineru_local_ocr.py', 'mineru_remote_ocr.py'):
    spec = importlib.util.spec_from_file_location('probe_' + filename, repo/'scripts'/filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    root = tempfile.mkdtemp(prefix='paper-wiki-snapshot-race-')
    try:
        candidate = os.path.join(root, 'paper.pdf')
        secret = os.path.join(root, 'secret.pdf')
        snapshot = os.path.join(root, 'snapshot.pdf')
        pathlib.Path(candidate).write_bytes(b'%PDF-benign\n%%EOF')
        pathlib.Path(secret).write_bytes(b'%PDF-secret\n%%EOF')
        original = module._is_reparse_point
        swapped = [False]
        def inject(path):
            if path == candidate and not swapped[0]:
                swapped[0] = True
                os.unlink(candidate)
                os.symlink(secret, candidate)
                return False
            return original(path)
        module._is_reparse_point = inject
        try:
            module.snapshot_pdf(candidate, snapshot, root)
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError(filename + ' followed a swapped symlink')
        assert not os.path.exists(snapshot), filename
    finally:
        shutil.rmtree(root, ignore_errors=True)
print('snapshot-race-rejected')
'''
        result = subprocess.run(
            ["bash", "-lc", "python3 -"], input=code.encode("utf-8"),
            capture_output=True, check=False, timeout=30, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"snapshot-race-rejected", result.stdout)

    def test_local_mineru_consumes_snapshot_and_original_is_only_rechecked(self):
        with project_paths(self.local) as (_root, topic):
            original = Path(topic, "paper.pdf")
            benign = b"%PDF-benign\n%%EOF"
            secret = b"%PDF-secret\n%%EOF"
            original.write_bytes(benign)
            consumed = []

            def fake_mineru(pdf, staged_out, _timeout):
                consumed.append((pdf, Path(pdf).read_bytes()))
                original.write_bytes(secret)
                output = Path(staged_out, "paper", "auto")
                output.mkdir(parents=True)
                Path(output, "paper.md").write_text("# paper\n", encoding="utf-8")
                return 0, False

            values = {
                "preflight_publication": lambda: None,
                "recover_pending_batches": lambda: None,
                "output_conflicts": lambda _sources: [],
                "gpu_available": lambda: True,
                "run_mineru": fake_mineru,
            }
            with patched(self.local, **values), mock.patch.object(
                    self.local.shutil, "which", return_value="mineru"):
                rc = self.local.main([topic])
            self.assertEqual(rc, self.local.EXIT_PROCESSING)
            self.assertNotEqual(os.path.abspath(consumed[0][0]), str(original.resolve()))
            self.assertEqual(consumed[0][1], benign)

    def _write_pending_fixture(self, module, topic):
        output = Path(topic, "mineru")
        source_dir = output / "paper" / "auto"
        source_dir.mkdir(parents=True)
        (source_dir / "paper.md").write_text("# paper\n", encoding="utf-8")
        pdf = Path(topic, "paper.pdf")
        payload = b"%PDF-1.7\n%%EOF"
        pdf.write_bytes(payload)
        batch_id = "d" * 32
        item = {
            "source": "paper",
            "source_pdf": "paper.pdf",
            "source_pdf_size": len(payload),
            "source_pdf_sha256": hashlib.sha256(payload).hexdigest(),
            "source_pdf_project_path": "raw/topic/paper.pdf",
        }
        fingerprint = module._build_content_fingerprint(str(output / "paper"))
        item["content_fingerprint"] = fingerprint
        batch = {
            "schema": "paper-wiki/ocr-batch/v2",
            "batch_id": batch_id,
            "backend": "test",
            "created_at": "2026-01-01T00:00:00Z",
            "commit_rule": "test",
            "sources": [json.loads(json.dumps(item))],
        }
        manifest = {
            "schema": "paper-wiki/ocr-completion/v2",
            "backend": "test",
            "completed_at": "2026-01-01T00:00:00Z",
            **item,
            "batch_id": batch_id,
            "batch_commit_marker": module.batch_marker_name(batch_id, "committed"),
            "state": "requires-batch-commit",
            "commit_rule": "test",
            "markdown": ["auto/paper.md"],
            "content_fingerprint": json.loads(json.dumps(fingerprint)),
        }
        manifest_path = output / "paper" / module.COMPLETION_MANIFEST
        pending_path = output / module.batch_marker_name(batch_id, "pending")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        pending_path.write_text(json.dumps(batch), encoding="utf-8")
        return output, pdf, pending_path, manifest_path, batch, manifest

    def _write_resolved_v1_fixture(self, module, topic, state):
        output = Path(topic, "mineru")
        output.mkdir(parents=True)
        batch_id = "e" * 32
        pending = {
            "schema": "paper-wiki/ocr-batch/v1",
            "batch_id": batch_id,
            "backend": "legacy-test",
            "created_at": "2025-01-01T00:00:00Z",
            "commit_rule": "legacy audit fixture",
            "sources": [{
                "source": "legacy-paper",
                "source_pdf": "legacy-paper.pdf",
                "source_pdf_size": 17,
                "source_pdf_sha256": "1" * 64,
            }],
        }
        resolved = json.loads(json.dumps(pending))
        resolved["resolution"] = state
        resolved["resolved_at"] = "2025-01-01T00:00:01Z"
        pending_path = output / module.batch_marker_name(batch_id, "pending")
        resolution_path = output / module.batch_marker_name(batch_id, state)
        pending_path.write_text(json.dumps(pending), encoding="utf-8")
        resolution_path.write_text(json.dumps(resolved), encoding="utf-8")
        return output, pending_path, resolution_path, pending, resolved

    def test_resolved_legacy_v1_audit_history_does_not_block_new_batches(self):
        for module in (self.local, self.remote):
            for state in ("committed", "aborted"):
                with self.subTest(module=module.__name__, state=state):
                    with project_paths(module) as (_root, topic):
                        (_output, pending, resolution,
                         _pending_value, _resolved_value) = (
                            self._write_resolved_v1_fixture(module, topic, state))
                        self.assertEqual(module.pending_batch_markers(), [])
                        module.recover_pending_batches()
                        self.assertTrue(pending.is_file())
                        self.assertTrue(resolution.is_file())

    def test_unresolved_legacy_v1_requires_reocr_or_reviewed_reseal(self):
        for module in (self.local, self.remote):
            with self.subTest(module=module.__name__):
                with project_paths(module) as (_root, topic):
                    (_output, _pending, resolution,
                     _pending_value, _resolved_value) = (
                        self._write_resolved_v1_fixture(
                            module, topic, "committed"))
                    resolution.unlink()
                    with self.assertRaisesRegex(
                            OSError, r"unresolved legacy v1.*re-OCR.*reseal"):
                        module.recover_pending_batches()

    def test_legacy_v1_conflicts_and_forged_markers_fail_closed(self):
        cases = ("both", "mismatch", "hardlink", "bad-pending-name")
        for module in (self.local, self.remote):
            for case in cases:
                with self.subTest(module=module.__name__, case=case):
                    with project_paths(module) as (_root, topic):
                        if case == "bad-pending-name":
                            output = Path(topic, "mineru")
                            output.mkdir(parents=True)
                            (output / (module.BATCH_MARKER_PREFIX
                                       + "not-hex.pending.json")).write_text(
                                "{}", encoding="utf-8")
                        else:
                            (output, _pending, resolution,
                             pending_value, resolved_value) = (
                                self._write_resolved_v1_fixture(
                                    module, topic, "committed"))
                            if case == "both":
                                aborted = json.loads(json.dumps(pending_value))
                                aborted["resolution"] = "aborted"
                                aborted["resolved_at"] = "2025-01-01T00:00:02Z"
                                Path(output, module.batch_marker_name(
                                    "e" * 32, "aborted")).write_text(
                                        json.dumps(aborted), encoding="utf-8")
                            elif case == "mismatch":
                                resolved_value["sources"][0]["source_pdf_size"] = 18
                                resolution.write_text(
                                    json.dumps(resolved_value), encoding="utf-8")
                            elif case == "hardlink":
                                os.link(resolution, output / "resolution-hardlink")
                        with self.assertRaises(OSError):
                            module.pending_batch_markers()

    def test_recovery_rejects_every_invalid_control_plane_mutation(self):
        cases = (
            "manifest-schema", "manifest-state", "backend-mismatch",
            "duplicate-markdown", "unsafe-markdown", "invalid-hash",
            "changed-project-pdf", "fingerprint-file-hash",
            "fingerprint-tree-hash", "changed-markdown", "extra-tree-file",
            "legacy-v1", "resolution-conflict",
        )
        for module in (self.local, self.remote):
            for case in cases:
                with self.subTest(module=module.__name__, case=case):
                    with project_paths(module) as (_root, topic):
                        os.makedirs(module.STAGING_PARENT)
                        (output, pdf, pending_path, manifest_path,
                         batch, manifest) = self._write_pending_fixture(module, topic)
                        if case == "manifest-schema":
                            manifest["schema"] = "attacker/invalid"
                        elif case == "manifest-state":
                            manifest["state"] = "already-trusted"
                        elif case == "backend-mismatch":
                            manifest["backend"] = "other"
                        elif case == "duplicate-markdown":
                            manifest["markdown"] = ["auto/paper.md", "auto/paper.md"]
                        elif case == "unsafe-markdown":
                            manifest["markdown"] = ["../paper.md"]
                        elif case == "invalid-hash":
                            batch["sources"][0]["source_pdf_sha256"] = "BAD"
                        elif case == "changed-project-pdf":
                            pdf.write_bytes(b"%PDF-changed\n%%EOF")
                        elif case == "fingerprint-file-hash":
                            manifest["content_fingerprint"]["files"][0]["sha256"] = "0" * 64
                        elif case == "fingerprint-tree-hash":
                            manifest["content_fingerprint"]["tree_sha256"] = "0" * 64
                        elif case == "changed-markdown":
                            (output / "paper" / "auto" / "paper.md").write_text(
                                "# swapped\n", encoding="utf-8")
                        elif case == "extra-tree-file":
                            (output / "paper" / "auto" / "extra.png").write_bytes(b"image")
                        elif case == "legacy-v1":
                            batch["schema"] = "paper-wiki/ocr-batch/v1"
                            manifest["schema"] = "paper-wiki/ocr-completion/v1"
                        elif case == "resolution-conflict":
                            for state in ("committed", "aborted"):
                                Path(output, module.batch_marker_name("d" * 32, state)).write_text(
                                    "{}", encoding="utf-8")
                        pending_path.write_text(json.dumps(batch), encoding="utf-8")
                        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                        with self.assertRaises((OSError, ValueError, json.JSONDecodeError)):
                            module.recover_pending_batches()
                        committed_path = Path(
                            output, module.batch_marker_name("d" * 32, "committed"))
                        if case == "resolution-conflict":
                            self.assertEqual(committed_path.read_text(encoding="utf-8"), "{}")
                        else:
                            self.assertFalse(committed_path.is_file())

    def test_recovery_validates_then_publishes_the_in_memory_snapshot(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (_root, topic):
                os.makedirs(module.STAGING_PARENT)
                output, _pdf, pending, _manifest, _batch, _value = (
                    self._write_pending_fixture(module, topic))
                batch_id, _records, record = module._read_pending_record(str(pending))
                self.assertEqual(batch_id, "d" * 32)
                original_read = module._read_json_regular

                def reject_pending_reread(path, *args):
                    if os.path.abspath(path) == os.path.abspath(pending):
                        raise AssertionError("publish re-read the pending path")
                    return original_read(path, *args)

                with patched(module, _read_json_regular=reject_pending_reread):
                    committed = module._publish_batch_resolution(
                        str(pending), "committed", record)
                self.assertTrue(Path(committed).is_file())
                self.assertEqual(Path(committed).parent, output)
                self.assertEqual(module.pending_batch_markers(), [])

    def test_commit_revalidates_content_at_resolution_boundary(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (_root, topic):
                pdf = Path(topic, "paper.pdf")
                payload = b"%PDF-1.7\n%%EOF"
                pdf.write_bytes(payload)
                metadata = {str(pdf): {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }}
                os.makedirs(module.STAGING_PARENT)
                staging = module.create_staging_dir("publish-swap-")
                staged_out = Path(staging, "output")
                source = staged_out / "paper" / "auto"
                source.mkdir(parents=True)
                (source / "paper.md").write_text("BENIGN\n", encoding="utf-8")
                batch_id = "b" * 32
                module.add_completion_manifests(
                    str(staged_out), {"paper": str(pdf)}, "test", metadata,
                    batch_id)
                original_publish = module._publish_batch_resolution
                swapped = [False]

                def inject(marker, state, record):
                    if state == "committed" and not swapped[0]:
                        swapped[0] = True
                        Path(module.LOCAL_OUT, "paper", "auto", "paper.md").write_text(
                            "ATTACKER-SWAP\n", encoding="utf-8")
                    return original_publish(marker, state, record)

                with patched(module, _publish_batch_resolution=inject):
                    with self.assertRaises(module.CommitFailure):
                        module.commit_sources(
                            str(staged_out), {"paper": str(pdf)}, batch_id,
                            metadata, "test")
                self.assertFalse(Path(
                    module.LOCAL_OUT,
                    module.batch_marker_name(batch_id, "committed")).exists())

    def test_recovery_revalidates_content_at_resolution_boundary(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (_root, topic):
                os.makedirs(module.STAGING_PARENT)
                output, _pdf, pending, _manifest, _batch, _value = (
                    self._write_pending_fixture(module, topic))
                original_publish = module._publish_batch_resolution
                swapped = [False]

                def inject(marker, state, record):
                    if state == "committed" and not swapped[0]:
                        swapped[0] = True
                        (output / "paper" / "auto" / "paper.md").write_text(
                            "ATTACKER-SWAP\n", encoding="utf-8")
                    return original_publish(marker, state, record)

                with patched(module, _publish_batch_resolution=inject):
                    with self.assertRaises(OSError):
                        module.recover_pending_batches()
                committed = Path(
                    output, module.batch_marker_name("d" * 32, "committed"))
                self.assertFalse(committed.exists())
                self.assertTrue(pending.exists())

    def test_linux_nested_portable_names_are_rejected(self):
        require_linux_bash(self, python=True)
        code = r'''
import importlib.util, os, pathlib, shutil, sys, tempfile, types
repo = pathlib.Path.cwd()
sys.modules.setdefault('paramiko', types.SimpleNamespace())
for filename in ('mineru_local_ocr.py', 'mineru_remote_ocr.py'):
    spec = importlib.util.spec_from_file_location('portable_' + filename, repo/'scripts'/filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    root = tempfile.mkdtemp(prefix='paper-wiki-portable-')
    try:
        source = pathlib.Path(root, 'paper', 'auto'); source.mkdir(parents=True)
        (source/'CON.md').write_text('# bad\n')
        try: module.markdown_files(str(pathlib.Path(root, 'paper')))
        except OSError: pass
        else: raise AssertionError(filename + ' accepted CON.md')
        (source/'CON.md').unlink()
        (source/'é.md').write_text('# one\n')
        (source/'e\u0301.md').write_text('# two\n')
        try: module.markdown_files(str(pathlib.Path(root, 'paper')))
        except OSError: pass
        else: raise AssertionError(filename + ' accepted NFC collision')
    finally: shutil.rmtree(root, ignore_errors=True)
print('portable-rejected')
'''
        result = subprocess.run(
            ["bash", "-lc", "python3 -"], input=code.encode("utf-8"),
            capture_output=True, check=False, timeout=30, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"portable-rejected", result.stdout)

    def test_project_pdf_inside_mineru_is_rejected(self):
        for module in (self.local, self.remote):
            with project_paths(module) as (_root, topic):
                pdf = Path(topic, "mineru", "paper.pdf")
                pdf.parent.mkdir()
                pdf.write_bytes(b"%PDF-1.7\n%%EOF")
                with self.assertRaisesRegex(OSError, "outside the mineru"):
                    module.source_pdf_project_path(str(pdf))

    def test_posix_staging_parent_swap_never_deletes_outside_tree(self):
        require_linux_bash(self, python=True)
        code = r'''
import importlib.util, pathlib, shutil, sys, tempfile, types
repo = pathlib.Path.cwd()
sys.modules.setdefault('paramiko', types.SimpleNamespace())
for filename in ('mineru_local_ocr.py', 'mineru_remote_ocr.py'):
    spec = importlib.util.spec_from_file_location('cleanup_' + filename, repo/'scripts'/filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    base = pathlib.Path(tempfile.mkdtemp(prefix='paper-wiki-cleanup-boundary-'))
    try:
        root = base/'project'; topic = root/'raw'/'topic'; topic.mkdir(parents=True)
        parent = root/'.paper-wiki'/'ocr-staging'
        module.WIKI_ROOT = str(root); module.DEFAULT_IN = str(topic)
        module.LOCAL_OUT = str(topic/'mineru'); module.STAGING_PARENT = str(parent)
        stage = pathlib.Path(module.create_staging_dir('run-'))
        (stage/'owned.txt').write_text('owned')
        outside = base/'outside'; victim = outside/stage.name; victim.mkdir(parents=True)
        sentinel = victim/'DO_NOT_DELETE'; sentinel.write_text('secret')
        old_parent = parent.with_name('ocr-staging-old'); parent.rename(old_parent)
        parent.symlink_to(outside, target_is_directory=True)
        assert module.cleanup_staging(str(parent/stage.name)) is False
        assert sentinel.is_file(), filename + ' deleted outside staging data'
        print(filename + ':cleanup-boundary-pass')
    finally:
        shutil.rmtree(base, ignore_errors=True)
'''
        result = subprocess.run(
            ["bash", "-lc", "python3 -"], input=code.encode("utf-8"),
            capture_output=True, check=False, timeout=30, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout.count(b"cleanup-boundary-pass"), 2)

    def test_local_posix_cleanup_kills_term_ignoring_child(self):
        require_linux_bash(self, python=True)
        code = r'''
import importlib.util, os, pathlib, shutil, signal, subprocess, tempfile, time
repo = pathlib.Path.cwd()
spec = importlib.util.spec_from_file_location('local_kill', repo/'scripts'/'mineru_local_ocr.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
root = tempfile.mkdtemp(prefix='paper-wiki-local-group-')
pidfile = os.path.join(root, 'child.pid')
script = ('trap "exit 0" TERM\n' +
          "bash -c 'trap \"\" TERM; echo $$ > \"" + pidfile +
          "\"; while :; do sleep 1; done' &\nwait\n")
process = subprocess.Popen(['bash', '-c', script], start_new_session=True)
try:
    for _attempt in range(50):
        if os.path.exists(pidfile): break
        time.sleep(.1)
    child = int(pathlib.Path(pidfile).read_text())
    module.terminate_process_group(process)
    assert not module._posix_live_group_members(process.pid)
    print('local-posix-group-pass')
finally:
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    shutil.rmtree(root, ignore_errors=True)
'''
        result = subprocess.run(
            ["bash", "-lc", "python3 -"], input=code.encode("utf-8"),
            capture_output=True, check=False, timeout=30, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"local-posix-group-pass", result.stdout)

    def test_sftp_file_and_directory_swaps_are_rejected_before_secret_read(self):
        require_linux_bash(self, python=True)
        code = r'''
import importlib.util, os, pathlib, shutil, stat, sys, tempfile, types
repo = pathlib.Path.cwd()
sys.modules.setdefault('paramiko', types.SimpleNamespace())
spec = importlib.util.spec_from_file_location('remote_sftp', repo/'scripts'/'mineru_remote_ocr.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class Attr:
    def __init__(self, value, filename=None):
        for name in ('st_mode','st_size','st_uid','st_gid','st_mtime','st_ino'):
            setattr(self, name, getattr(value, name, None))
        self.filename = filename

class Handle:
    def __init__(self, stream, owner): self.stream = stream; self.owner = owner
    def stat(self): return Attr(os.fstat(self.stream.fileno()))
    def read(self, size): self.owner.secret_consumed = True; return self.stream.read(size)
    def close(self): self.stream.close()

base = pathlib.Path(tempfile.mkdtemp(prefix='paper-wiki-sftp-swap-'))
try:
    remote = base/'remote'; remote.mkdir(); local = base/'local'
    target = remote/'paper.md'; target.write_text('benign')
    secret = base/'secret.txt'; secret.write_text('REMOTE-ACCOUNT-SECRET')
    class FileSwapSftp:
        secret_consumed = False
        swapped = False
        def lstat(self, path): return Attr(os.lstat(path))
        def listdir_attr(self, path):
            return [Attr(os.lstat(target), 'paper.md')]
        def open(self, path, mode):
            if not self.swapped:
                self.swapped = True; target.unlink(); target.symlink_to(secret)
            return Handle(open(path, mode), self)
    file_sftp = FileSwapSftp()
    try: module.download_tree(file_sftp, str(remote), str(local))
    except OSError: pass
    else: raise AssertionError('file swap was accepted')
    assert not file_sftp.secret_consumed
    assert not (local/'paper.md').exists()

    shutil.rmtree(local, ignore_errors=True)
    target.unlink(); child = remote/'child'; child.mkdir(); (child/'safe.md').write_text('safe')
    secret_dir = base/'secret-dir'; secret_dir.mkdir(); (secret_dir/'secret.md').write_text('SECRET')
    class DirectorySwapSftp:
        swapped = False
        def lstat(self, path): return Attr(os.lstat(path))
        def listdir_attr(self, path):
            path = pathlib.Path(path)
            if path == child and not self.swapped:
                self.swapped = True
                child.rename(remote/'child-old'); child.symlink_to(secret_dir, target_is_directory=True)
            return [Attr(os.lstat(path/name), name) for name in os.listdir(path)]
    try: module.download_tree(DirectorySwapSftp(), str(remote), str(local))
    except OSError: pass
    else: raise AssertionError('directory swap was accepted')
    assert not (local/'child'/'secret.md').exists()
    print('sftp-swap-pass')
finally:
    shutil.rmtree(base, ignore_errors=True)
'''
        result = subprocess.run(
            ["bash", "-lc", "python3 -"], input=code.encode("utf-8"),
            capture_output=True, check=False, timeout=30, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"sftp-swap-pass", result.stdout)

    def test_guardian_and_driver_are_valid_bash(self):
        if not shutil.which("bash"):
            self.skipTest("bash is unavailable")
        paths = self.remote.remote_paths("/tmp/mineru_review_ABC123")
        for script in (self.remote.build_guardian(paths),
                       self.remote.build_driver(paths)):
            result = subprocess.run(
                ["bash", "-n"], input=script.encode("utf-8"),
                capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_driver_launch_publishes_matching_identity_and_handoff(self):
        require_linux_bash(self)
        root = "/tmp/mineru_test_launchidentity"
        token = "1" * 64
        paths = self.remote.remote_paths(root, run_token=token)
        launch = self.remote.build_driver_launch(paths)
        harness = f'''set -eu
rm -rf -- {root}
mkdir -m 700 -p {root}/input {root}/output
printf '%s\n' '#!/bin/bash' 'sleep 30' > {root}/driver.sh
chmod 700 {root}/driver.sh
{launch}
cmp -s {root}/driver.identity {root}/guardian.handoff
read saved_token saved_boot pid saved_start saved_sid < {root}/driver.identity
[ "$saved_token" = {token} ] && [ "$pid" = "$saved_sid" ]
[ "$saved_boot" = "$(cat /proc/sys/kernel/random/boot_id)" ]
[ "$saved_start" = "$(awk '{{print $22}}' /proc/$pid/stat)" ]
echo IDENTITY_HANDOFF_OK
members=$(ps -eo pid=,sid= | awk -v s="$pid" '$2 == s {{print $1}}')
[ -z "$members" ] || kill -KILL $members 2>/dev/null || true
sleep 1
rm -rf -- {root}
'''
        result = run_bash(harness, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"IDENTITY_HANDOFF_OK", result.stdout)

    def test_guardian_hard_deadline_kills_only_identity_matched_driver(self):
        require_linux_bash(self)
        token = "2" * 64
        with patched(
                self.remote, GUARDIAN_POLL_SECONDS=1,
                REMOTE_TIMEOUT_KILL_GRACE_SECONDS=1):
            good_root = "/tmp/mineru_test_guardianmatched"
            good = self.remote.remote_paths(good_root, run_token=token)
            good["pre_handoff_deadline"] = 10
            good["hard_deadline"] = 2
            good_script = self.remote.build_guardian(good)
            stale_root = "/tmp/mineru_test_guardianstale"
            stale = self.remote.remote_paths(stale_root, run_token=token)
            stale["pre_handoff_deadline"] = 10
            stale["hard_deadline"] = 2
            stale_script = self.remote.build_guardian(stale)
        harness = f'''set -eu
rm -rf -- {good_root} {stale_root}
mkdir -m 700 {good_root} {stale_root}
setsid sleep 30 >/dev/null 2>&1 & good_pid=$!
boot=$(cat /proc/sys/kernel/random/boot_id)
good_start=$(awk '{{print $22}}' /proc/$good_pid/stat)
printf '%s %s %s %s %s\n' {token} "$boot" "$good_pid" "$good_start" "$good_pid" > {good_root}/driver.identity
cp {good_root}/driver.identity {good_root}/guardian.handoff
bash -s <<'GOOD_GUARDIAN' &
{good_script}
GOOD_GUARDIAN
good_guardian=$!
setsid sleep 30 >/dev/null 2>&1 & stale_pid=$!
stale_start=$(awk '{{print $22}}' /proc/$stale_pid/stat)
wrong_start=$((stale_start + 1))
printf '%s %s %s %s %s\n' {token} "$boot" "$stale_pid" "$wrong_start" "$stale_pid" > {stale_root}/driver.identity
cp {stale_root}/driver.identity {stale_root}/guardian.handoff
bash -s <<'STALE_GUARDIAN' &
{stale_script}
STALE_GUARDIAN
stale_guardian=$!
sleep 5
good_state=$(ps -o stat= -p "$good_pid" 2>/dev/null | tr -d ' ' || true)
stale_state=$(ps -o stat= -p "$stale_pid" 2>/dev/null | tr -d ' ' || true)
[ ! -d {good_root} ] && [ ! -d {stale_root} ]
case "$good_state" in (''|Z*) : ;; (*) echo "good driver survived: $good_state"; exit 1 ;; esac
case "$stale_state" in (''|Z*) echo 'stale PID was killed'; exit 1 ;; (*) : ;; esac
echo GUARDIAN_IDENTITY_OK
kill -KILL "$stale_pid" 2>/dev/null || true
wait "$good_pid" "$stale_pid" "$good_guardian" "$stale_guardian" 2>/dev/null || true
rm -rf -- {good_root} {stale_root}
'''
        result = run_bash(harness, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"GUARDIAN_IDENTITY_OK", result.stdout)

    def test_guardian_kills_term_ignoring_child_after_leader_exits(self):
        require_linux_bash(self)
        root = "/tmp/mineru_test_guardian_termchild"
        childfile = "/tmp/mineru_test_guardian_termchild.pid"
        token = "e" * 64
        with patched(
                self.remote, GUARDIAN_POLL_SECONDS=1,
                REMOTE_TIMEOUT_KILL_GRACE_SECONDS=2):
            paths = self.remote.remote_paths(root, run_token=token)
            paths["pre_handoff_deadline"] = 10
            paths["hard_deadline"] = 2
            guardian = self.remote.build_guardian(paths)
        harness = f'''set -eu
rm -rf -- {root}; rm -f {childfile}; mkdir -m 700 {root}
cat > {root}/victim.sh <<'DRIVER'
#!/bin/bash
trap 'exit 0' TERM
bash -c 'trap "" TERM; echo $$ > {childfile}; while :; do sleep 1; done' &
wait
DRIVER
chmod 700 {root}/victim.sh
setsid bash {root}/victim.sh >/dev/null 2>&1 & pid=$!
for _attempt in 1 2 3 4 5; do [ -s {childfile} ] && break; sleep 1; done
child=$(cat {childfile})
boot=$(cat /proc/sys/kernel/random/boot_id)
start=$(awk '{{print $22}}' /proc/$pid/stat)
printf '%s %s %s %s %s\n' {token} "$boot" "$pid" "$start" "$pid" > {root}/driver.identity
cp {root}/driver.identity {root}/guardian.handoff
bash -s <<'GUARD' &
{guardian}
GUARD
guardian_pid=$!
wait "$guardian_pid"; sleep 1
state=$(ps -o stat= -p "$child" 2>/dev/null | tr -d ' ' || true)
case "$state" in (''|Z*) : ;; (*) echo "child survived: $state"; exit 1 ;; esac
[ ! -e {root} ]
echo GUARDIAN_TERM_CHILD_OK
wait "$pid" 2>/dev/null || true
rm -f {childfile}
'''
        result = run_bash(harness, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn(b"GUARDIAN_TERM_CHILD_OK", result.stdout)

    def test_driver_kill_after_and_activation_watchdog_cleanup_real_sessions(self):
        require_linux_bash(self)
        cases = (("mineru", ":"), ("activation", "sleep 30"))
        for label, activate in cases:
            with self.subTest(label=label), patched(
                    self.remote, ACTIVATE=activate,
                    REMOTE_ACTIVATION_TIMEOUT_SECONDS=1,
                    REMOTE_PDF_TIMEOUT_SECONDS=1,
                    REMOTE_TIMEOUT_KILL_GRACE_SECONDS=1,
                    REMOTE_RESULT_TTL_SECONDS=1,
                    GUARDIAN_POLL_SECONDS=1):
                root = f"/tmp/mineru_test_driver_{label}"
                paths = self.remote.remote_paths(root, run_token="3" * 64)
                driver = self.remote.build_driver(paths)
            harness = f'''set -eu
rm -rf -- {root}
mkdir -p {root}/input {root}/output {root}/bin
: > {root}/input/paper.pdf
printf '%s\n' '#!/bin/bash' "trap '' TERM" 'while :; do sleep 1; done' > {root}/bin/mineru
chmod 700 {root}/bin/mineru
cat > {root}/driver.sh <<'DRIVER'
{driver}
DRIVER
chmod 700 {root}/driver.sh
PATH="{root}/bin:$PATH" setsid bash {root}/driver.sh >/dev/null 2>&1 & pid=$!
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  state=$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  if [ ! -d {root} ]; then
    case "$state" in (''|Z*) break ;; esac
  fi
  sleep 1
done
state=$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ' || true)
[ ! -d {root} ]
case "$state" in (''|Z*) : ;; (*) echo "driver survived: $state"; exit 1 ;; esac
echo DRIVER_TIMEOUT_CLEANED_{label}
wait "$pid" 2>/dev/null || true
rm -rf -- {root}
'''
            result = run_bash(harness, timeout=15)
            self.assertEqual(
                result.returncode, 0,
                result.stdout.decode(errors="replace")
                + result.stderr.decode(errors="replace"))
            self.assertIn(f"DRIVER_TIMEOUT_CLEANED_{label}".encode(), result.stdout)

    def test_cleanup_does_not_signal_stale_reused_pid(self):
        require_linux_bash(self)
        root = "/tmp/mineru_test_cleanupstale"
        token = "4" * 64
        paths = self.remote.remote_paths(root, run_token=token)
        setup = f'''set -eu
rm -rf -- {root}
mkdir -m 700 {root}
nohup setsid sleep 30 >/dev/null 2>&1 & pid=$!
sleep 1
boot=$(cat /proc/sys/kernel/random/boot_id)
start=$(awk '{{print $22}}' /proc/$pid/stat)
wrong=$((start + 1))
printf '%s %s %s %s %s\n' {token} "$boot" "$pid" "$wrong" "$pid" > {root}/driver.identity
echo "$pid"
'''
        created = run_bash(setup, timeout=10)
        self.assertEqual(created.returncode, 0, created.stderr.decode(errors="replace"))
        pid = created.stdout.decode().strip().splitlines()[-1]

        class Client:
            def close(self):
                pass

        def execute(_client, command, timeout=60):
            result = run_bash(command, timeout=timeout + 5)
            return (result.returncode, result.stdout.decode(errors="replace"),
                    result.stderr.decode(errors="replace"))

        with patched(
                self.remote, NS="test", connect=lambda: Client(), run_cmd=execute,
                GUARDIAN_POLL_SECONDS=1,
                REMOTE_TIMEOUT_KILL_GRACE_SECONDS=1):
            self.remote.cleanup_remote_workspace(paths)
        check = run_bash(
            f'''if kill -0 {pid} 2>/dev/null; then echo ALIVE; else echo KILLED; fi
[ ! -e {root} ]
kill -KILL {pid} 2>/dev/null || true
rm -rf -- {root}
''', timeout=10)
        self.assertEqual(check.returncode, 0, check.stderr.decode(errors="replace"))
        self.assertIn(b"ALIVE", check.stdout)

    def test_guardian_is_active_before_first_pdf_upload(self):
        events = []
        uploaded = []
        original_pdf = [None]
        paths = self.remote.remote_paths("/tmp/mineru_test_ABC123")

        class Client:
            def close(self):
                events.append("client-close")

        class FailingSftp:
            def put(self, source, _destination):
                events.append("pdf-put")
                uploaded.append((source, Path(source).read_bytes()))
                Path(original_pdf[0]).write_bytes(b"%PDF-secret\n%%EOF")
                raise OSError("injected upload disconnect")

            def close(self):
                events.append("sftp-close")

        def fake_run_cmd(_client, command, timeout=60):
            del timeout
            if command.startswith("nvidia-smi --query-gpu=memory.free"):
                return 0, "16000\n", ""
            raise AssertionError(f"unexpected remote command: {command}")

        def cleanup(_paths):
            events.append("cleanup-reconnect-failed")
            raise OSError("injected cleanup reconnect failure")

        with project_paths(self.remote) as (_root, topic):
            pdf = str(Path(topic, "paper.pdf"))
            Path(pdf).write_bytes(b"%PDF-1.7\n%%EOF")
            original_pdf[0] = pdf
            values = {
                "connect": lambda: Client(),
                "run_cmd": fake_run_cmd,
                "create_remote_workspace": (
                    lambda _client, pdf_count=1: paths),
                "install_guardian": (
                    lambda _client, _paths: events.append("guardian-active")),
                "open_sftp": lambda _client: FailingSftp(),
                "cleanup_remote_workspace": cleanup,
            }
            with patched(self.remote, **values):
                with self.assertRaisesRegex(OSError, "cleanup reconnect failure"):
                    self.remote.run_remote_pipeline(
                        [pdf], {"paper": pdf}, None, 0, input_dir=topic)
                staging_parent = Path(self.remote.STAGING_PARENT)
                self.assertFalse(
                    staging_parent.exists() and any(staging_parent.iterdir()),
                    "local staging survived a remote cleanup exception")
        self.assertLess(events.index("guardian-active"), events.index("pdf-put"))
        self.assertIn("cleanup-reconnect-failed", events)
        self.assertNotEqual(os.path.abspath(uploaded[0][0]), os.path.abspath(pdf))
        self.assertEqual(uploaded[0][1], b"%PDF-1.7\n%%EOF")

    def test_compile_contracts_fail_closed_before_body_read(self):
        files = (
            "commands/wiki-compile.md",
            "skills/paper-wiki/references/actions.md",
            "templates/research/WIKI.md.tmpl",
            "templates/course/WIKI.md.tmpl",
            "docs/llm-wiki.protocol.yaml",
        )
        required = (
            "paper-wiki/ocr-completion/v2",
            "paper-wiki/ocr-batch/v2",
            "paper-wiki/ocr-content/v1",
            "tree_sha256",
            "source_pdf_project_path",
            "realpath",
            "reparse",
        )
        for relative in files:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in required:
                self.assertIn(token, text, f"{token} missing from {relative}")


if __name__ == "__main__":
    unittest.main()
