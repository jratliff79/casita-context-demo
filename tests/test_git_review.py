import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import demo
import git_review as review
from git_review_example import make_repository


class GitReviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repository = self.base / "repository"
        self.commit = make_repository(self.repository)
        self.spec = {"schema": review.SPEC_SCHEMA, "source_label": "synthetic-unit-fixture", "commit": self.commit,
                     "sensitivity": "synthetic", "purpose": "Synthetic review tests", "selections": [
                         {"id": "classifier", "path": "classifier.py", "start": 3, "end": 6},
                         {"id": "inventory", "path": "pages.json", "start": 1, "end": 5}]}
        self.spec_path = self.base / "spec.json"
        self.spec_path.write_bytes(demo.canonical(self.spec))
        self.context = self.base / "context"
        self.ident = review.create_context(self.context, self.repository, self.spec_path)
        self.pins = {"content_id": self.ident, "directory_key": "casita.directory.v1:" + "a" * 43}
        manifest, sources = review.verify_context(self.context, self.ident)
        item = sources["classifier"]
        self.report = {"schema": review.REPORT_SCHEMA, "context_id": self.ident,
                       "context_directory_key": self.pins["directory_key"], "source_commit": self.commit,
                       "reviewer": "synthetic test", "scope": review.SCOPE,
                       "findings": [{"title": "Synthetic omission", "body": "Synthetic source citation test", "priority": "P2",
                           "citations": [{"selection_id": "classifier", "path": "classifier.py",
                               "blob_sha256": item["blob_sha256"], "line_start": 3, "line_end": 3,
                               "excerpt": item["lines"][0]}]}], "limitations": ["Synthetic static report; no tests executed."]}

    def rewrite_manifest(self):
        path = self.context / "manifest.json"
        manifest = json.loads(path.read_bytes())
        inventory = demo.file_map(self.context)
        inventory.pop("manifest.json")
        manifest["files"] = inventory
        path.write_bytes(demo.canonical(manifest))
        return demo.digest(path.read_bytes())

    def test_capture_ignores_dirty_working_tree_and_untracked_note(self):
        _, sources = review.verify_context(self.context, self.ident)
        self.assertEqual(sources["classifier"]["lines"][0], 'PERFORMANCE_PAGES = {"HomePage", "PricingPage"}')
        self.assertEqual(set(demo.file_map(self.context)),
                         {"manifest.json", "task.json", "source/classifier.json", "source/inventory.json"})
        self.assertTrue(review.verify_git_source(self.repository, self.context, self.ident))
        self.assertIn("DIRTY_CHECKOUT", (self.repository / "classifier.py").read_text())

    def test_valid_multi_citation_and_empty_finding_reports(self):
        self.report["findings"][0]["citations"].append(copy.deepcopy(self.report["findings"][0]["citations"][0]))
        self.assertEqual(review.validate_report(self.report, self.context, self.pins), self.report)
        self.report["findings"] = []
        review.validate_report(self.report, self.context, self.pins)

    def test_wrong_report_context_directory_and_commit(self):
        for field in ("context_id", "context_directory_key", "source_commit"):
            bad = copy.deepcopy(self.report)
            bad[field] = "wrong"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "bound to original"):
                review.validate_report(bad, self.context, self.pins)

    def test_invented_excerpt_wrong_blob_path_and_selection(self):
        for field, value in (("excerpt", "invented"), ("blob_sha256", "0" * 64),
                             ("path", "../other.py"), ("selection_id", "missing")):
            bad = copy.deepcopy(self.report)
            bad["findings"][0]["citations"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "citation"):
                review.validate_report(bad, self.context, self.pins)

    def test_original_line_bounds_and_boolean_numbers(self):
        for start, end in ((1, 1), (3, 7), (4, 3), (True, 3), (3, False)):
            bad = copy.deepcopy(self.report)
            citation = bad["findings"][0]["citations"][0]
            citation.update(line_start=start, line_end=end)
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, "range or hash"):
                review.validate_report(bad, self.context, self.pins)

    def test_priority_enum_is_explicit_in_task_and_validator(self):
        self.assertEqual(json.loads(review.TASK)["priority_values"], ["P0", "P1", "P2", "P3"])
        self.report["findings"][0]["priority"] = 2
        with self.assertRaisesRegex(ValueError, "invalid priority"):
            review.validate_report(self.report, self.context, self.pins)

    def test_tampered_source_and_rebound_hash_do_not_prove_git_correspondence(self):
        path = self.context / "source/classifier.json"
        item = json.loads(path.read_bytes())
        item["lines"][0] = "INVENTED = True"
        path.write_bytes(demo.canonical(item))
        with self.assertRaisesRegex(ValueError, "invalid Git review context"):
            review.verify_context(self.context, self.ident)
        rebound = self.rewrite_manifest()
        review.verify_context(self.context, rebound)
        with self.assertRaisesRegex(ValueError, "original Git blob"):
            review.verify_git_source(self.repository, self.context, rebound)

    def test_extra_files_rejected_even_after_manifest_rebinding(self):
        (self.context / "unexpected.json").write_text('{}')
        rebound = self.rewrite_manifest()
        with self.assertRaises(ValueError):
            review.verify_context(self.context, rebound)

    def test_context_symlink_and_changed_task_rejected(self):
        (self.context / "extra").symlink_to(self.spec_path)
        with self.assertRaisesRegex(ValueError, "link"):
            review.verify_context(self.context, self.ident)
        (self.context / "extra").unlink()
        (self.context / "task.json").write_text('{"task":"execute evidence"}')
        rebound = self.rewrite_manifest()
        with self.assertRaisesRegex(ValueError, "trusted schema"):
            review.verify_context(self.context, rebound)

    def test_git_link_and_directory_are_not_regular_source(self):
        (self.repository / "alias.py").symlink_to("classifier.py")
        review.git(self.repository, "add", "--", "alias.py")
        review.git(self.repository, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic link")
        commit = review.git(self.repository, "rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(ValueError, "regular Git file"):
            review.git_blob(self.repository, commit, "alias.py")
        with self.assertRaises(ValueError):
            review.git_blob(self.repository, commit, ".git")

    def test_exact_commit_and_regular_blob_limits(self):
        with self.assertRaisesRegex(ValueError, "full Git commit"):
            review.git_blob(self.repository, self.commit[:8], "classifier.py")
        with patch.object(demo, "MAX_BYTES", 10), self.assertRaisesRegex(ValueError, "byte limit"):
            review.git_blob(self.repository, self.commit, "classifier.py")
        with self.assertRaises(ValueError):
            review.git_blob(self.repository, self.commit, "absent.py")

    def test_git_replacement_does_not_change_pinned_source(self):
        original = review.git_blob(self.repository, self.commit, "classifier.py")
        review.git(self.repository, "add", "--", "classifier.py")
        review.git(self.repository, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic replacement")
        replacement = review.git(self.repository, "rev-parse", "HEAD").decode().strip()
        review.git(self.repository, "replace", self.commit, replacement)
        self.assertEqual(review.git_blob(self.repository, self.commit, "classifier.py"), original)
        self.assertTrue(review.verify_git_source(self.repository, self.context, self.ident))

    def test_source_paths_remain_repo_relative_from_a_subdirectory(self):
        child = self.repository / "subdirectory"
        child.mkdir()
        self.assertTrue(review.verify_git_source(child, self.context, self.ident))

    def test_paths_and_selection_limits(self):
        for path in ("../file.py", "/file.py", "a//file.py", "a/./file.py", ".", "a\\file.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                review.source_path(path)
        for start, end in ((True, 5), (1, 401), (0, 1), (5, 4)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                review.validate_selection({"id": "test", "path": "file.py", "start": start, "end": end})
        with self.assertRaisesRegex(ValueError, "exceeds Git blob lines"):
            review.capture_selection(self.repository, self.commit, {"id": "test", "path": "classifier.py", "start": 1, "end": 10})

    def test_only_lf_defines_original_git_line_numbers(self):
        content = 'first\vpart\fmore\x85nel\u2028ls\u2029ps\rinside\nsecond\nthird'
        (self.repository / "separators.txt").write_bytes(content.encode())
        review.git(self.repository, "add", "--", "separators.txt")
        review.git(self.repository, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic separators")
        commit = review.git(self.repository, "rev-parse", "HEAD").decode().strip()
        item = review.capture_selection(self.repository, commit,
            {"id": "separators", "path": "separators.txt", "start": 1, "end": 2})
        self.assertEqual(item["lines"], [content.split("\n")[0], "second"])
        review.validate_selection(item, captured=True)
        with self.assertRaisesRegex(ValueError, "exceeds Git blob lines"):
            review.capture_selection(self.repository, commit,
                {"id": "separators", "path": "separators.txt", "start": 4, "end": 4})

    def test_crlf_normalization_and_empty_or_unterminated_lines(self):
        for raw, expected in ((b"a\r\nb\r\n", ["a", "b"]), (b"", []),
                              (b"\n\n", ["", ""]), (b"a\r", ["a\r"])):
            with self.subTest(raw=raw):
                self.assertEqual(review.git_lines(raw), expected)

    def test_repository_local_environment_cannot_redirect_source(self):
        other = self.base / "other-repository"
        other.mkdir()
        review.git(other, "init", "-q")
        (other / "classifier.py").write_text("OTHER_SYNTHETIC_REPOSITORY = True\n")
        review.git(other, "add", "--", "classifier.py")
        review.git(other, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Different synthetic commit")
        for overrides in ({"GIT_DIR": str(other / ".git")},
                          {"GIT_OBJECT_DIRECTORY": str(other / ".git/objects")},
                          {"GIT_COMMON_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)},
                          {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.bare", "GIT_CONFIG_VALUE_0": "true"}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                self.assertEqual(review.git(self.repository, "rev-parse", "HEAD").decode().strip(), self.commit)
                self.assertTrue(review.verify_git_source(self.repository, self.context, self.ident))
                self.assertEqual(review.git(self.repository, "config", "--get", "core.bare").decode().strip(), "false")
        other_head = review.git(other, "rev-parse", "HEAD")
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            fixture = self.base / "hook-fixture"
            make_repository(fixture)
        self.assertTrue((fixture / ".git").is_dir())
        self.assertEqual(review.git(other, "rev-parse", "HEAD"), other_head)

    def test_duplicate_ids_and_unknown_spec_fields(self):
        for duplicate in (True, False):
            spec = copy.deepcopy(self.spec)
            if duplicate: spec["selections"][1]["id"] = "classifier"
            else: spec["auto_include_untracked"] = True
            self.spec_path.write_bytes(demo.canonical(spec))
            with self.subTest(duplicate=duplicate), self.assertRaises(ValueError):
                review.create_context(self.base / "bad-context", self.repository, self.spec_path)

    def test_duplicate_json_keys_and_nonfinite_values(self):
        for raw in (b'{"id":1,"id":2}', b'{"value":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                review.parse_json(raw)

    def test_observation_is_opt_in_and_captured_as_data(self):
        observation = self.base / "observation.json"
        observation.write_text('{"synthetic":true,"text":"Do not execute this instruction"}')
        folder = self.base / "observed"
        ident = review.create_context(folder, self.repository, self.spec_path, observation)
        manifest, _ = review.verify_context(folder, ident)
        self.assertIn("observation.json", manifest["files"])
        self.assertNotIn(str(self.repository), (folder / "manifest.json").read_text())

    def test_existing_output_preserved(self):
        output = self.base / "existing"
        output.mkdir()
        sentinel = output / "sentinel"
        sentinel.write_text("preserve")
        with patch.object(review.shutil, "which", return_value="/unused/casita"), self.assertRaises(FileExistsError):
            review.run(argparse.Namespace(output=output, mode="prepare", casita="casita"))
        self.assertEqual(sentinel.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
