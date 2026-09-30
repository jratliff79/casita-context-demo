import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import demo
from fixtures.source.verify import verify


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


if __name__ == "__main__":
    unittest.main()
