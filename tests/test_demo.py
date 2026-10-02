import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

import demo
verify, _ = demo.load_trusted_checker()


class HandoffChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "context"
        self.pin = demo.create_context(self.folder, "v1")

    def test_wrong_pin_rejected(self):
        with self.assertRaisesRegex(ValueError, "ID mismatch"):
            demo.verify_context(self.folder, "0" * 64)

    def test_packaging_excludes_caches_and_unselected_local_files(self):
        fixtures = Path(self.temp.name) / "fixtures"
        (fixtures / "source" / "__pycache__").mkdir(parents=True)
        (fixtures / "v1").mkdir()
        for name in ("source/verify.py", "v1/observation.json", "v1/task.md"):
            (fixtures / name).write_bytes((demo.FIXTURES / name).read_bytes())
        (fixtures / "source/__pycache__/verify.pyc").write_bytes(b"fake local filename")
        (fixtures / "source/.env").write_text("PRIVATE_NOTE=synthetic test marker")
        context = Path(self.temp.name) / "selected-context"
        with patch.object(demo, "FIXTURES", fixtures):
            pin = demo.create_context(context, "v1")
        manifest = demo.verify_context(context, pin)
        self.assertEqual(set(manifest["files"]),
                         {"source/verify.py", "observation.json", "task.md"})
        self.assertEqual(set(demo.file_map(context)), set(manifest["files"]) | {"manifest.json"})

    def test_linked_fixture_input_and_directory_rejected(self):
        for linked_directory in (False, True):
            with self.subTest(linked_directory=linked_directory):
                fixtures = Path(self.temp.name) / str(linked_directory)
                fixtures.mkdir()
                (fixtures / "source").symlink_to(demo.FIXTURES / "source", target_is_directory=True)
                if not linked_directory:
                    (fixtures / "source").unlink()
                    (fixtures / "source").mkdir()
                    (fixtures / "source/verify.py").symlink_to(demo.FIXTURES / "source/verify.py")
                with patch.object(demo, "FIXTURES", fixtures):
                    with self.assertRaisesRegex(ValueError, "without links"):
                        demo.create_context(fixtures / "context", "v1")

    def test_altered_evidence_and_forged_manifest_rejected(self):
        evidence = self.folder / "observation.json"
        evidence.write_text("invented evidence")
        manifest_path = self.folder / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"]["observation.json"] = demo.digest(evidence.read_bytes())
        manifest_path.write_bytes(demo.canonical(manifest))
        with self.assertRaisesRegex(ValueError, "ID mismatch"):
            demo.verify_context(self.folder, self.pin)

    def test_unlisted_evidence_rejected(self):
        (self.folder / "extra.txt").write_text("unlisted evidence")
        with self.assertRaisesRegex(ValueError, "file set"):
            demo.verify_context(self.folder, self.pin)

    def test_linked_evidence_rejected(self):
        evidence = self.folder / "observation.json"
        original = Path(self.temp.name) / "outside.json"
        evidence.rename(original)
        evidence.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "link"):
            demo.verify_context(self.folder, self.pin)

    def test_archive_pin_rejected_before_import(self):
        archive = Path(self.temp.name) / "archive.casitar"
        archive.write_bytes(b"not the expected archive")
        with self.assertRaisesRegex(ValueError, "archive SHA"):
            demo.checked_archive(archive, "0" * 64)

    def test_existing_output_preserved_before_casita_runs(self):
        marker = self.folder / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            demo.run_demo("/not-a-real-executable", self.folder)
        self.assertEqual(marker.read_text(), "keep")

    def test_synthetic_control_changes_the_expected_verifier_result(self):
        for version in ("v1", "v2"):
            observation = json.loads((demo.FIXTURES / version / "observation.json").read_bytes())
            self.assertEqual(verify(observation["source_packets"], observation["output_audio_duration"]),
                             observation["verification_result"])

    def test_invalid_output_durations_rejected_as_unavailable(self):
        rows = [{"pts": 0, "duration": 0.063}]
        for duration in (-0.1, -1, 0, float("inf"), float("-inf"), float("nan"),
                         None, "N/A", "0.063", True, False, [], {}, 10 ** 400):
            with self.subTest(duration=duration):
                self.assertEqual(verify(rows, duration),
                                 "rejected: source or output audio timing unavailable")

    def test_valid_output_timing_preserves_negative_pts_and_shortening_tolerance(self):
        rows = [{"pts": -0.021, "duration": 1}]
        self.assertEqual(verify(rows, 1), "accepted")
        self.assertEqual(verify(rows, 0.75), "accepted")
        self.assertEqual(verify(rows, 0.749), "rejected: audio shortened")

    def test_json_exponent_overflow_rejected_through_context_and_result_checks(self):
        context = Path(self.temp.name) / "overflow-context"
        demo.create_context(context, "v2")
        observation = json.loads((context / "observation.json").read_bytes())
        raw = json.dumps(observation).replace('"output_audio_duration": 0.063',
                                              '"output_audio_duration": 1e309').encode()
        self.assertEqual(json.loads(raw)["output_audio_duration"], float("inf"))
        (context / "observation.json").write_bytes(raw)
        manifest = json.loads((context / "manifest.json").read_bytes())
        manifest["files"]["observation.json"] = demo.digest(raw)
        manifest_bytes = demo.canonical(manifest)
        (context / "manifest.json").write_bytes(manifest_bytes)
        pin = demo.digest(manifest_bytes)
        result = demo.expected_result(context, pin, "synthetic-directory-key")
        self.assertEqual(result["verdict"], "rejected: source or output audio timing unavailable")
        folder = Path(self.temp.name) / "overflow-result"
        folder.mkdir()
        result_bytes = demo.canonical(result)
        (folder / "result.json").write_bytes(result_bytes)
        demo.verify_result(folder, demo.digest(result_bytes), context, pin, "synthetic-directory-key")
        result["verdict"] = "accepted"
        forged = demo.canonical(result)
        (folder / "result.json").write_bytes(forged)
        with self.assertRaisesRegex(ValueError, "does not match original context"):
            demo.verify_result(folder, demo.digest(forged), context, pin, "synthetic-directory-key")

    def test_results_match_both_original_contexts(self):
        for version in ("v1", "v2"):
            context = Path(self.temp.name) / version
            pin = demo.create_context(context, version)
            result = demo.expected_result(context, pin, "synthetic-directory-key")
            observation = json.loads((context / "observation.json").read_bytes())
            self.assertEqual(result["verdict"], observation["verification_result"])
            folder = Path(self.temp.name) / (version + "-result")
            folder.mkdir()
            data = demo.canonical(result)
            (folder / "result.json").write_bytes(data)
            demo.verify_result(folder, demo.digest(data), context, pin, "synthetic-directory-key")

    def test_foreign_fixtures_package_cannot_replace_the_hashed_checker(self):
        package = Path(self.temp.name) / "fixtures"
        (package / "source").mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "source/__init__.py").write_text("")
        (package / "source/verify.py").write_text("raise AssertionError('foreign checker executed')\n")
        script = """import json, os, tempfile
from pathlib import Path
import fixtures
assert Path(fixtures.__file__) == Path(os.environ['PYTHONPATH']) / 'fixtures/__init__.py'
import demo
with tempfile.TemporaryDirectory() as directory:
    context = Path(directory) / 'context'
    pin = demo.create_context(context, 'v1')
    print(json.dumps(demo.expected_result(context, pin, 'synthetic-key')))
"""
        result = subprocess.run([sys.executable, "-B", "-c", script],
            cwd=demo.FIXTURES.parent, env={**os.environ, "PYTHONPATH": self.temp.name},
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        returned = json.loads(result.stdout)
        self.assertEqual(returned["checker_sha256"], demo.digest(demo.TRUSTED_CHECKER.read_bytes()))
        self.assertEqual(returned["verdict"], "rejected: source or output audio timing unavailable")

    def test_altered_rebound_and_wrong_context_results_rejected(self):
        folder = Path(self.temp.name) / "result"
        folder.mkdir()
        data = demo.canonical(demo.expected_result(self.folder, self.pin, "original-key"))
        result_path = folder / "result.json"
        result_path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "original context"):
            demo.verify_result(folder, demo.digest(data), self.folder, self.pin, "other-key")
        for field, value in (("verdict", "accepted"), ("checker_sha256", "0" * 64)):
            with self.subTest(field=field):
                altered = json.loads(data)
                altered[field] = value
                raw = demo.canonical(altered)
                result_path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "pin mismatch"):
                    demo.verify_result(folder, demo.digest(data), self.folder, self.pin, "original-key")
                with self.assertRaisesRegex(ValueError, "trusted checker"):
                    demo.verify_result(folder, demo.digest(raw), self.folder, self.pin, "original-key")

    def test_replaced_received_checker_rejected_even_with_new_manifest_pin(self):
        checker = self.folder / "source/verify.py"
        checker.write_text("raise AssertionError('received source executed')\n")
        manifest_path = self.folder / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"]["source/verify.py"] = demo.digest(checker.read_bytes())
        data = demo.canonical(manifest)
        manifest_path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "trusted local checker"):
            demo.expected_result(self.folder, demo.digest(data), "original-key")

    def test_unlisted_and_linked_result_files_rejected(self):
        folder = Path(self.temp.name) / "result"
        folder.mkdir()
        data = demo.canonical(demo.expected_result(self.folder, self.pin, "original-key"))
        result_path = folder / "result.json"
        result_path.write_bytes(data)
        extra = folder / "extra.txt"
        extra.write_text("unlisted")
        with self.assertRaisesRegex(ValueError, "file set"):
            demo.verify_result(folder, demo.digest(data), self.folder, self.pin, "original-key")
        extra.unlink()
        outside = Path(self.temp.name) / "outside-result.json"
        result_path.rename(outside)
        result_path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "link"):
            demo.verify_result(folder, demo.digest(data), self.folder, self.pin, "original-key")


if __name__ == "__main__":
    unittest.main()
