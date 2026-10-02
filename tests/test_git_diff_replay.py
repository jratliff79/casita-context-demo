import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import git_diff_example as example
import git_diff_replay as replay


class RecordedDiffChecks(unittest.TestCase):
    def test_public_allowlist_pins_both_versions_and_missing_base_files(self):
        self.assertEqual(replay.SOURCE["license"], "MIT")
        self.assertEqual(replay.SOURCE["repository"], "https://github.com/jratliff79/casita-context-demo")
        for version in ("base", "head"):
            self.assertEqual(set(replay.SOURCE["files"][version]), set(replay.SOURCE["spec"]["paths"]))
        self.assertIsNone(replay.SOURCE["files"]["base"]["git_diff_review.py"])
        self.assertIsNotNone(replay.SOURCE["files"]["head"]["git_diff_review.py"])

    def test_claimed_git_absence_is_checked(self):
        source = copy.deepcopy(replay.SOURCE)
        source["spec"]["paths"] = ["git_diff_review.py"]
        source["files"] = {"base": {"git_diff_review.py": None}, "head": {"git_diff_review.py": "0" * 64}}
        with patch.object(example.diff, "git_file", return_value=({"mode": "100644"}, b"unexpected")):
            with self.assertRaisesRegex(ValueError, "public Git absence"):
                example.verify_public_source(Path("unused"), source)

    def test_controls_do_not_invent_findings_in_empty_recorded_report(self):
        report = {"findings": [], "reviewer": "actual reviewer", "limitations": ["Actual limitations"]}
        sources = {"base:code": {"version": "base", "id": "code", "path": "file.py", "blob_sha256": "a" * 64,
                                  "start": 5, "lines": ["before"]},
                   "head:code": {"version": "head", "id": "code", "path": "file.py", "blob_sha256": "b" * 64,
                                  "start": 6, "lines": ["after"]}}
        expected = copy.deepcopy(report)
        control = example.control_report(report, sources)
        self.assertEqual(report, expected)
        self.assertEqual(control["reviewer"], "scripted invalid-report controls")
        self.assertEqual(control["findings"][0]["citations"][0]["excerpt"], "before")

    def test_replay_uses_recorded_report_and_explicit_public_fixture(self):
        with patch.object(example, "run", return_value={"ok": True}) as run:
            class Args:
                report = Path(__file__).parent.parent / "docs/diff-review-report.json"
            args = Args()
            replay.run(args)
            run.assert_called_once_with(args, source=replay.SOURCE, report_raw=args.report.read_bytes())

    def test_edited_context_bound_report_rejected_before_artifacts(self):
        original = Path(__file__).parent.parent / "docs/diff-review-report.json"
        report = json.loads(original.read_bytes())
        report["reviewer"] = "edited or newly authored report"
        with tempfile.TemporaryDirectory() as name:
            class Args:
                pass
            args = Args()
            args.report = Path(name) / "edited.json"
            args.output = Path(name) / "output"
            args.report.write_text(json.dumps(report))
            with patch.object(example, "run") as run, self.assertRaisesRegex(ValueError, "pinned recorded AI artifact"):
                replay.run(args)
            run.assert_not_called()
            self.assertFalse(args.output.exists())

    def test_verified_snapshot_is_passed_even_if_report_path_changes(self):
        original = (Path(__file__).parent.parent / "docs/diff-review-report.json").read_bytes()
        with tempfile.TemporaryDirectory() as name:
            class Args:
                pass
            args = Args()
            args.report = Path(name) / "report.json"
            args.report.write_bytes(original)
            def replace_path(options, source, report_raw):
                options.report.write_bytes(b"replaced after digest verification")
                self.assertEqual(report_raw, original)
                return {"ok": True}
            with patch.object(example, "run", side_effect=replace_path):
                self.assertTrue(replay.run(args)["ok"])
            self.assertNotEqual(args.report.read_bytes(), original)

    def test_recorded_report_is_empty_and_has_no_synthetic_finding(self):
        path = Path(__file__).parent.parent / "docs/diff-review-report.json"
        report = json.loads(path.read_bytes())
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["base_commit"], replay.SOURCE["spec"]["base_commit"])
        self.assertEqual(report["head_commit"], replay.SOURCE["spec"]["head_commit"])
        self.assertIn("static correctness review", report["reviewer"])


if __name__ == "__main__":
    unittest.main()
