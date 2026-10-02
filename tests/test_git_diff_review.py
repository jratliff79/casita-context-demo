import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import demo
import git_diff_review as diff
import git_diff_example as example
import git_review as review
from git_review_example import make_repository


class GitDiffChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.base = make_repository(self.repo)
        (self.repo / "classifier.py").write_text("FIRST = True\nSECOND = False\n", encoding="utf-8")
        (self.repo / "added.txt").write_bytes(b"new\r\nlast")
        review.git(self.repo, "rm", "-q", "--", "pages.json")
        self.head = self.commit("Synthetic changes")
        # Dirty files and untracked material must never be packaged.
        (self.repo / "classifier.py").write_text("DIRTY_SENTINEL\n")
        (self.repo / "private-note.txt").write_text("UNTRACKED_SENTINEL\n")
        self.spec = {"schema": diff.SPEC_SCHEMA, "source_label": "synthetic diff test", "base_commit": self.base,
                     "head_commit": self.head, "sensitivity": "synthetic", "purpose": "Synthetic protocol test",
                     "paths": ["classifier.py", "added.txt", "pages.json"], "selections": [
                         {"version": "base", "id": "code", "path": "classifier.py", "start": 3, "end": 6},
                         {"version": "head", "id": "code", "path": "classifier.py", "start": 1, "end": 2}]}
        self.spec_path = self.root / "spec.json"
        self.spec_path.write_bytes(demo.canonical(self.spec))
        self.context = self.root / "context"
        self.ident = diff.create_context(self.context, self.repo, self.spec_path)
        self.pins = {"content_id": self.ident, "directory_key": "casita.directory.v1:" + "a" * 43}
        _, sources = diff.verify_context(self.context, self.ident)
        citations = []
        for version in diff.VERSIONS:
            item = sources[version + ":code"]
            citations.append({"version": version, "selection_id": "code", "path": item["path"],
                              "blob_sha256": item["blob_sha256"], "line_start": item["start"],
                              "line_end": item["start"], "excerpt": item["lines"][0]})
        self.report = {"schema": diff.REPORT_SCHEMA, "context_id": self.ident, "context_directory_key": self.pins["directory_key"],
                       "base_commit": self.base, "head_commit": self.head, "reviewer": "synthetic test",
                       "scope": review.SCOPE, "findings": [{"title": "Synthetic citation control", "body": "No AI review.",
                       "priority": "P3", "citations": citations}], "limitations": ["Synthetic protocol test."]}

    def commit(self, message):
        review.git(self.repo, "add", "-A")
        review.git(self.repo, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
        return review.git(self.repo, "rev-parse", "HEAD").decode().strip()

    def rebound(self):
        path = self.context / "manifest.json"
        manifest = review.read_json(path)
        files = demo.file_map(self.context)
        files.pop("manifest.json")
        manifest["files"] = files
        path.write_bytes(demo.canonical(manifest))
        return demo.digest(path.read_bytes())

    def test_exact_source_versions_dirty_exclusion_and_added_deleted_files(self):
        self.assertTrue(diff.verify_git_source(self.repo, self.context, self.ident))
        changes = review.read_json(self.context / "changes.json")
        added, deleted = changes["files"][1:]
        self.assertIsNone(added["base"])
        self.assertIsNone(deleted["head"])
        self.assertIn("+new\r\n", added["diff"])
        self.assertIn("\\ No newline at end of file", added["diff"])
        all_bytes = b"".join(p.read_bytes() for p in self.context.rglob("*") if p.is_file())
        self.assertNotIn(b"DIRTY_SENTINEL", all_bytes)
        self.assertNotIn(b"UNTRACKED_SENTINEL", all_bytes)
        self.assertNotIn(str(self.root).encode(), all_bytes)

    def test_cross_version_findings_and_empty_report(self):
        self.assertEqual(diff.validate_report(self.report, self.context, self.pins), self.report)
        self.report["findings"] = []
        diff.validate_report(self.report, self.context, self.pins)

    def test_swapped_version_hash_excerpt_and_boolean_lines_reject(self):
        for key, value in (("version", "head"), ("version", "other"), ("blob_sha256", "0" * 64),
                           ("excerpt", "invented"), ("line_start", True), ("selection_id", "missing")):
            report = copy.deepcopy(self.report)
            report["findings"][0]["citations"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                diff.validate_report(report, self.context, self.pins)

    def test_wrong_base_head_context_and_unknown_report_fields(self):
        for field in ("base_commit", "head_commit", "context_id", "context_directory_key", "extra"):
            report = copy.deepcopy(self.report)
            report[field] = "wrong"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "original Git diff context"):
                diff.validate_report(report, self.context, self.pins)

    def test_rebound_forged_diff_is_rejected_against_original_git(self):
        path = self.context / "changes.json"
        changes = review.read_json(path)
        changes["files"][0]["diff"] = "invented diff"
        path.write_bytes(demo.canonical(changes))
        ident = self.rebound()
        diff.verify_context(self.context, ident)
        with self.assertRaisesRegex(ValueError, "original Git blobs"):
            diff.verify_git_source(self.repo, self.context, ident)

    def test_rebound_forged_selection_and_diff_hash_still_fail_original_git(self):
        path = self.context / "source/head/code.json"
        selected = review.read_json(path)
        selected.update(blob_sha256="0" * 64, lines=["invented", "also invented"])
        path.write_bytes(demo.canonical(selected))
        changes = review.read_json(self.context / "changes.json")
        changes["files"][0]["head"]["blob_sha256"] = "0" * 64
        (self.context / "changes.json").write_bytes(demo.canonical(changes))
        ident = self.rebound()
        diff.verify_context(self.context, ident)
        with self.assertRaises(ValueError):
            diff.verify_git_source(self.repo, self.context, ident)

    def test_extra_files_symlinks_and_task_mutation_reject(self):
        (self.context / "extra.json").write_text("{}")
        with self.assertRaises(ValueError):
            diff.verify_context(self.context, self.rebound())
        (self.context / "extra.json").unlink()
        (self.context / "task.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "trusted schema"):
            diff.verify_context(self.context, self.rebound())
        (self.context / "alias").symlink_to(self.spec_path)
        with self.assertRaisesRegex(ValueError, "link"):
            demo.file_map(self.context)

    def test_spec_paths_versions_duplicates_and_missing_sources_reject(self):
        changes = [dict(base_commit=self.head), dict(paths=["../outside"]), dict(paths=["added.txt"]),
                   dict(paths=["classifier.py", "classifier.py"]),
                   dict(selections=[dict(self.spec["selections"][0], version="other")]),
                   dict(selections=[self.spec["selections"][0]] * 2),
                   dict(selections=[dict(self.spec["selections"][0], path="added.txt")])]
        for index, update in enumerate(changes):
            spec = dict(self.spec, **update)
            self.spec_path.write_bytes(demo.canonical(spec))
            with self.subTest(update=update), self.assertRaises(ValueError):
                diff.create_context(self.root / ("bad-" + str(index)), self.repo, self.spec_path)

    def test_no_changes_absent_binary_and_link_source_reject(self):
        for paths in (["missing.txt"], ["private-note.txt"]):
            with self.assertRaises(ValueError):
                diff.make_changes(self.repo, self.spec, paths)
        (self.repo / "binary.bin").write_bytes(b"binary\x00bytes")
        (self.repo / "link.txt").symlink_to("classifier.py")
        head = self.commit("Synthetic unsafe files")
        for path in ("binary.bin", "link.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                diff.make_changes(self.repo, dict(self.spec, head_commit=head), [path])

    def test_lf_only_diff_lines_and_mode_only_change(self):
        self.assertEqual(diff.diff_lines(b"a\xc2\x85b\r\nc"), ["a\x85b\r\n", "c"])
        review.git(self.repo, "checkout", self.head, "--", "classifier.py")
        review.git(self.repo, "update-index", "--chmod=+x", "classifier.py")
        review.git(self.repo, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic mode")
        head = review.git(self.repo, "rev-parse", "HEAD").decode().strip()
        changes = diff.make_changes(self.repo, {"base_commit": self.head, "head_commit": head}, ["classifier.py"])
        self.assertEqual(changes["files"][0]["diff"], "")
        self.assertEqual(changes["files"][0]["head"]["mode"], "100755")

    def test_git_environment_redirect_and_subdirectory_do_not_change_diff(self):
        child = self.repo / "subdirectory"
        child.mkdir()
        with patch.dict(review.os.environ, {"GIT_DIR": str(self.root / "absent.git")}):
            self.assertTrue(diff.verify_git_source(child, self.context, self.ident))

    def test_existing_output_preserved(self):
        (self.root / "sentinel").write_text("preserve")
        with self.assertRaises(FileExistsError):
            diff.run(argparse.Namespace(casita="python3", output=self.root, mode="prepare"))
        self.assertEqual((self.root / "sentinel").read_text(), "preserve")


class PublicDiffExampleChecks(unittest.TestCase):
    def test_wrong_public_source_rejected_before_artifact_creation(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "output"
            with patch.object(review, "git_blob", return_value=b"not allowlisted"), self.assertRaisesRegex(ValueError, "allowlisted"):
                example.run(argparse.Namespace(source=Path(name), output=output))
            self.assertFalse(output.exists())

    def test_failure_removes_throwaway_keys(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "output"
            def fail(path):
                path.write_text("synthetic marker")
                raise RuntimeError("controlled failure")
            with patch.object(example, "verify_public_source"), patch.object(example.auth, "make_demo_key", side_effect=fail), self.assertRaisesRegex(RuntimeError, "controlled failure"):
                example.run(argparse.Namespace(source=Path(name), output=output))
            self.assertFalse((output / "throwaway-keys").exists())
            self.assertTrue(review.read_json(output / "receipt.json")["throwaway_private_keys_removed"])


if __name__ == "__main__":
    unittest.main()
