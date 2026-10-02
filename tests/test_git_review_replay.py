import argparse
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import git_review_replay as replay


class PublicGitReplayChecks(unittest.TestCase):
    def test_source_allowlist_is_public_licensed_and_commit_pinned(self):
        self.assertEqual(replay.SOURCE["repository"], "https://github.com/jratliff79/casita-context-demo")
        self.assertEqual(replay.SOURCE["license"], "MIT")
        self.assertEqual(replay.SOURCE["commit"], "5cc171423d293ad5ca6b2e5323718d4f699e1136")
        self.assertEqual({s["path"] for s in replay.SOURCE["spec"]["selections"]}, set(replay.SOURCE["files"]))
        self.assertIn("LICENSE", replay.SOURCE["files"])

    def test_wrong_source_rejected_before_output_or_keys_exist(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "output"
            args = argparse.Namespace(source=Path(name), output=output)
            with patch.object(replay.review, "git_blob", return_value=b"not allowlisted public bytes"), self.assertRaisesRegex(ValueError, "allowlisted public Git blob"):
                replay.run(args)
            self.assertFalse(output.exists())

    def test_extra_selection_not_authorized_by_file_allowlist(self):
        source = copy.deepcopy(replay.SOURCE)
        source["spec"]["selections"].append({"id": "unexpected", "path": "unlisted.py", "start": 1, "end": 1})
        with patch.object(replay, "SOURCE", source), self.assertRaisesRegex(ValueError, "public source allowlist"):
            replay.verify_public_source(Path("unused"))

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "existing"
            output.mkdir()
            (output / "sentinel").write_text("preserve")
            with patch.object(replay, "verify_public_source"), self.assertRaises(FileExistsError):
                replay.run(argparse.Namespace(source=Path(name), output=output))
            self.assertEqual((output / "sentinel").read_text(), "preserve")

    def test_failed_replay_removes_throwaway_private_keys(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "run"
            args = argparse.Namespace(source=Path(name), output=output, casita="unused")
            def fail_key(path):
                path.write_text("synthetic temporary key marker")
                raise RuntimeError("synthetic failure")
            with patch.object(replay, "verify_public_source"), patch.object(replay.auth, "make_demo_key", side_effect=fail_key), self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                replay.run(args)
            self.assertFalse((output / "throwaway-keys").exists())
            self.assertIn('"throwaway_private_keys_removed":true', (output / "receipt.json").read_text())


if __name__ == "__main__":
    unittest.main()
