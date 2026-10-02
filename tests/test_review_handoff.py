import argparse
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import demo
import review_handoff as review
import review_replay


class ReviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source_bytes = b"def example():\n    return 1\n"
        source = {"repository": review.SOURCE["repository"], "commit": review.SOURCE["commit"],
                  "files": {"example.py": demo.digest(self.source_bytes)}}
        self.source_patch = patch.object(review, "SOURCE", source)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.context = self.folder / "context"
        with patch.object(review.subprocess, "check_output", return_value=self.source_bytes) as git:
            content_id = review.create_context(self.context, self.folder)
            self.assertEqual(git.call_args.args[0][-1], source["commit"] + ":example.py")
        self.pins = {"schema": "casita-context-demo.review-input-pins.v1", "content_id": content_id,
                     "archive_sha256": "0" * 64, "directory_key": "casita.directory.v1:" + "a" * 43}
        self.report = {"schema": "casita-context-demo.review-report.v1", "context_id": content_id,
            "context_directory_key": self.pins["directory_key"], "source_repository": source["repository"],
            "source_commit": source["commit"], "reviewer": "synthetic unit-test reviewer",
            "scope": "static review; no received code executed", "limitations": ["Synthetic parser fixture"],
            "findings": [{"path": "source/example.py", "file_sha256": demo.digest(self.source_bytes),
                "line_start": 1, "line_end": 2, "excerpt": "def example():\n    return 1",
                "priority": "P2", "title": "Synthetic citation", "body": "Parser fixture, not an actual finding."}]}

    def test_context_binding_and_exact_citations_accepted(self):
        self.assertEqual(review.validate_report(self.report, self.context, self.pins), self.report)
        self.report["findings"] = []
        self.assertEqual(review.validate_report(self.report, self.context, self.pins)["findings"], [])

    def test_wrong_context_key_commit_and_repository_rejected(self):
        for name in ("context_id", "context_directory_key", "source_commit", "source_repository"):
            with self.subTest(field=name):
                report = copy.deepcopy(self.report)
                report[name] = "wrong"
                with self.assertRaisesRegex(ValueError, "original source context"):
                    review.validate_report(report, self.context, self.pins)

    def test_forged_citations_hashes_paths_and_line_ranges_rejected(self):
        changes = [("path", "../../outside"), ("path", "task.md"), ("file_sha256", "0" * 64),
                   ("excerpt", "invented code"), ("line_start", True), ("line_start", 0),
                   ("line_end", 3), ("line_end", 0)]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                report = copy.deepcopy(self.report)
                report["findings"][0][field] = value
                with self.assertRaises(ValueError):
                    review.validate_report(report, self.context, self.pins)

    def test_report_fields_and_resource_limits_enforced(self):
        for field, value in (("extra", "unexpected"), ("scope", "tests executed"),
                             ("findings", None), ("findings", [self.report["findings"][0]] * 21),
                             ("limitations", "wrong type"), ("reviewer", "x" * 121)):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report[field] = value
                with self.assertRaises(ValueError):
                    review.validate_report(report, self.context, self.pins)
        for field, value in (("priority", "P5"), ("title", "x" * 161), ("body", "x" * 4001)):
            report = copy.deepcopy(self.report)
            report["findings"][0][field] = value
            with self.assertRaises(ValueError):
                review.validate_report(report, self.context, self.pins)

    def test_changed_citation_bytes_cannot_retain_the_original_hash(self):
        original_read = review.auth.read_regular
        def changed_read(path, limit):
            if path == self.context / "source/example.py":
                return b"def altered():\n    return 2\n"
            return original_read(path, limit)
        report = copy.deepcopy(self.report)
        report["findings"][0]["excerpt"] = "def altered():\n    return 2"
        with patch.object(review.auth, "read_regular", side_effect=changed_read):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                review.validate_report(report, self.context, self.pins)

    def test_rebound_context_cannot_claim_different_public_source(self):
        source = self.context / "source/example.py"
        source.write_bytes(b"changed public code\n")
        manifest = review.read_json(self.context / "manifest.json")
        manifest["files"]["source/example.py"] = demo.digest(source.read_bytes())
        raw = demo.canonical(manifest)
        (self.context / "manifest.json").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "allowlisted public snapshot"):
            review.verify_context(self.context, demo.digest(raw))

    def test_bad_signature_rejected_before_store_initialization(self):
        store = Mock()
        with patch.object(review.auth, "verify_pins", side_effect=ValueError("pin signature rejected")):
            with self.assertRaisesRegex(ValueError, "signature rejected"):
                review.receive(store, self.transfer_args(), self.folder, "review-input", {})
        store.run.assert_not_called()

    def transfer_args(self):
        return argparse.Namespace(pins=self.folder / "pins.json", signature=self.folder / "pins.sig",
            allowed_signers=self.folder / "allowed", signer="synthetic-sender", archive=self.folder / "input.casitar")

    def test_wrong_archive_digest_rejected_before_initialization(self):
        args = self.transfer_args()
        args.archive.write_bytes(b"different archive")
        store = Mock()
        with patch.object(review.auth, "verify_pins", return_value=demo.canonical(self.pins)):
            with self.assertRaisesRegex(ValueError, "archive SHA"):
                review.receive(store, args, self.folder, "review-input", {})
        store.run.assert_not_called()

    def test_import_uses_the_verified_archive_snapshot(self):
        args = self.transfer_args()
        args.archive.write_bytes(b"original archive")
        self.pins["archive_sha256"] = demo.digest(args.archive.read_bytes())
        store = Mock()
        store.roots.return_value = {"received/0": self.pins["directory_key"]}
        def change_original(*command):
            if command[0] == "init":
                args.archive.write_bytes(b"replaced during import")
        store.run.side_effect = change_original
        with patch.object(review.auth, "verify_pins", return_value=demo.canonical(self.pins)):
            review.receive(store, args, self.folder, "review-input", {})
        imported = store.run.call_args_list[1].args[2]
        self.assertEqual(imported.read_bytes(), b"original archive")
        self.assertNotEqual(imported, args.archive)

    def test_existing_output_preserved(self):
        marker = self.folder / "keep"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            review.run(argparse.Namespace(casita="python3", output=self.folder, mode="prepare"))
        self.assertEqual(marker.read_text(), "keep")

    def test_failed_replay_removes_its_private_keys(self):
        output = self.folder / "failed-replay"
        with patch.object(review, "run", side_effect=ValueError("controlled failure")):
            with self.assertRaisesRegex(ValueError, "controlled failure"):
                review_replay.run(argparse.Namespace(casita="python3", source=self.folder,
                    report=self.folder / "unused.json", output=output))
        self.assertFalse((output / "throwaway-keys").exists())
        self.assertFalse(review.read_json(output / "receipt.json")["ok"])
