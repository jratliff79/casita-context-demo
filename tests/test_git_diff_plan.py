import argparse
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import demo
import git_diff_plan as plan
import git_diff_review as diff
import git_review as review
from git_review_example import make_repository


class GitDiffPlanChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.base = make_repository(self.repo)
        (self.repo / "helper.py").write_text("PASSIVE_HELPER = True\n")
        self.base = self.commit("helper.py")
        (self.repo / "classifier.py").write_bytes(b"FIRST = True\r\nSECOND = False")
        (self.repo / "added.txt").write_text("added\n")
        (self.repo / "omitted.txt").write_text("OMITTED_CONTENT_SENTINEL\n")
        review.git(self.repo, "rm", "-q", "--", "pages.json")
        self.head = self.commit()
        (self.repo / "classifier.py").write_text("DIRTY_SENTINEL\n")
        (self.repo / "untracked.txt").write_text("UNTRACKED_SENTINEL\n")

    def commit(self, *paths):
        review.git(self.repo, "add", "--", *(paths or ("classifier.py", "added.txt", "omitted.txt")))
        review.git(self.repo, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic plan fixture")
        return review.git(self.repo, "rev-parse", "HEAD").decode().strip()

    def args(self, **updates):
        values = dict(source=self.repo, output=self.root / "preview", base=self.base, head=self.head,
                      path=["classifier.py", "added.txt", "pages.json"], source_label="synthetic scope test",
                      purpose="Synthetic range selection test", sensitivity="synthetic", context_lines=2,
                      select=[], observation=None)
        values.update(updates)
        return argparse.Namespace(**values)

    def spec(self, **updates):
        args = self.args(**updates)
        return plan.plan(args.source, args.base, args.head, args.path, args.source_label,
                         args.purpose, args.sensitivity, args.context_lines, args.select)[0]

    def test_preview_captures_exact_committed_scope_and_omits_dirty_source(self):
        args = self.args()
        summary = plan.run(args)
        spec = review.read_json(args.output / "spec.json")
        self.assertEqual(summary["omitted_changed_paths"], ["omitted.txt"])
        self.assertEqual(summary["changed_path_count"], 4)
        raw = b"".join(p.read_bytes() for p in args.output.rglob("*") if p.is_file())
        for sentinel in (b"DIRTY_SENTINEL", b"UNTRACKED_SENTINEL", b"OMITTED_CONTENT_SENTINEL",
                         str(self.root).encode()):
            self.assertNotIn(sentinel, raw)
        self.assertTrue(diff.verify_git_source(self.repo, args.output / "context", summary["content_id"]))
        self.assertTrue(summary["files"][1]["full_added_or_deleted_file"])
        self.assertTrue(summary["files"][2]["full_added_or_deleted_file"])
        # The preview context is exactly what the normal sender would capture.
        ident = diff.create_context(self.root / "prepared", self.repo, args.output / "spec.json")
        self.assertEqual(ident, summary["content_id"])
        self.assertEqual(summary["context_bytes"], sum(p.stat().st_size for p in
                         (args.output / "context").rglob("*") if p.is_file()))
        self.assertEqual(spec["paths"], args.path)

    def test_crlf_and_missing_newline_use_original_lf_numbers(self):
        spec = self.spec(path=["classifier.py"])
        head = next(s for s in spec["selections"] if s["version"] == "head")
        self.assertEqual((head["start"], head["end"]), (1, 2))
        summary, files = plan.preview(self.repo, spec)
        captured = review.parse_json(files["source/head/" + head["id"] + ".json"])
        self.assertEqual(captured["lines"], ["FIRST = True", "SECOND = False"])
        self.assertIn(b"No newline at end of file", files["changes.json"])
        self.assertEqual(summary["selected_line_count"], sum(s["end"]-s["start"]+1 for s in spec["selections"]))

    def test_range_windows_merge_split_and_ignore_unicode_line_separators(self):
        self.assertEqual(plan.suggested_ranges(b"a\xc2\x85b\r\nc", b"a\xc2\x85b\r\nd", 0),
                         {"base": [(2, 2)], "head": [(2, 2)]})
        added = b"x\n" * 801
        self.assertEqual(plan.suggested_ranges(None, added, 0)["head"], [(1, 400), (401, 800), (801, 801)])
        old = b"\n".join(str(i).encode() for i in range(100)) + b"\n"
        new = old.replace(b"20\n", b"twenty\n").replace(b"25\n", b"twenty-five\n")
        self.assertEqual(plan.suggested_ranges(old, new, 3)["head"], [(18, 29)])

    def test_explicit_ranges_replace_suggestions_only_for_named_path(self):
        spec = self.spec(select=["head:classifier.py:1:1"])
        code = [s for s in spec["selections"] if s["path"] == "classifier.py"]
        self.assertEqual(len(code), 1)
        self.assertEqual((code[0]["version"], code[0]["start"], code[0]["end"]), ("head", 1, 1))
        self.assertTrue(any(s["path"] == "added.txt" for s in spec["selections"]))
        summary, files = plan.preview(self.repo, spec)
        # Selecting one citation line does not hide another changed diff line.
        self.assertIn(b"SECOND = False", files["changes.json"])
        self.assertTrue(summary["warnings"])

    def test_invalid_selection_never_creates_output(self):
        for selected in ("head:outside.txt:1:1", "head:added.txt:1:2", "base:added.txt:1:1",
                         "other:classifier.py:1:1", "head:classifier.py:1:401", "bad"):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                plan.run(self.args(select=[selected]))
            self.assertFalse((self.root / "preview").exists())

    def test_over_twenty_ranges_reject_without_truncation_or_output(self):
        with self.assertRaisesRegex(ValueError, "no ranges were omitted"):
            plan.run(self.args(select=["head:classifier.py:1:1"] * 21))
        self.assertFalse((self.root / "preview").exists())
        (self.repo / "large.txt").write_bytes(b"x\n" * 8001)
        head = self.commit("large.txt")
        with self.assertRaisesRegex(ValueError, "large.txt=21"):
            plan.run(self.args(head=head, path=["large.txt"]))
        self.assertFalse((self.root / "preview").exists())

    def test_unchanged_helper_and_mode_only_change_are_explicit_and_complete(self):
        spec = self.spec(path=["classifier.py", "helper.py"])
        helper_ranges = [s for s in spec["selections"] if s["path"] == "helper.py"]
        self.assertEqual({s["version"] for s in helper_ranges}, {"base", "head"})
        review.git(self.repo, "checkout", self.head, "--", "classifier.py")
        review.git(self.repo, "update-index", "--chmod=+x", "classifier.py")
        review.git(self.repo, "-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                   "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic mode change")
        head = review.git(self.repo, "rev-parse", "HEAD").decode().strip()
        spec = self.spec(base=self.head, head=head, path=["classifier.py"])
        summary, files = plan.preview(self.repo, spec)
        self.assertEqual(summary["files"][0]["diff_utf8_bytes"], 0)
        self.assertEqual(summary["files"][0]["head"]["mode"], "100755")
        self.assertEqual(len(spec["selections"]), 2)

    def test_existing_directory_and_symlink_are_preserved(self):
        args = self.args(); args.output.mkdir(); (args.output / "sentinel").write_text("preserve")
        with self.assertRaises(FileExistsError):
            plan.run(args)
        self.assertEqual((args.output / "sentinel").read_text(), "preserve")
        link = self.root / "link"; link.symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            plan.run(self.args(output=link))
        self.assertTrue(link.is_symlink())

    def test_exact_optional_observation_and_context_budget(self):
        observation = self.root / "observation.json"
        observation.write_text('{"synthetic":true}')
        spec = self.spec()
        summary, files = plan.preview(self.repo, spec, observation)
        self.assertTrue(summary["observation_included"])
        self.assertEqual(files["observation.json"], demo.canonical({"synthetic": True}))
        with patch.object(demo, "MAX_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "byte"):
                plan.run(self.args(observation=observation))
        self.assertFalse((self.root / "preview").exists())

    def test_empty_file_change_needs_an_explicit_nonempty_helper(self):
        (self.repo / "empty.txt").write_bytes(b"")
        head = self.commit("empty.txt")
        with self.assertRaisesRegex(ValueError, "0 ranges"):
            plan.run(self.args(base=self.head, head=head, path=["empty.txt"]))
        self.assertFalse((self.root / "preview").exists())
        spec = self.spec(base=self.head, head=head, path=["empty.txt", "helper.py"])
        summary, _ = plan.preview(self.repo, spec)
        self.assertTrue(summary["files"][0]["full_added_or_deleted_file"])
        self.assertEqual(summary["files"][0]["diff_utf8_bytes"], 0)
        self.assertEqual({s["path"] for s in spec["selections"]}, {"helper.py"})

    def test_non_utf8_omitted_name_is_escaped_and_preserved_without_source(self):
        name = b"omitted-\xff.txt"
        # Create the tree directly: some host filesystems cannot represent this
        # valid Git path. No omitted blob needs to be checked out or read.
        def plumbing(*args, data):
            return subprocess.check_output(["git", "-C", str(self.repo), *args],
                       input=data, timeout=15, env=review.git_environment()).strip()
        blob = plumbing("hash-object", "-w", "--stdin", data=b"NON_UTF8_OMITTED_CONTENT_SENTINEL\n")
        code = plumbing("hash-object", "-w", "--stdin", data=b"FIRST = False\n")
        entries = review.git(self.repo, "ls-tree", "-z", self.head).split(b"\x00")
        entries = [b"100644 blob " + code + b"\tclassifier.py" if e.endswith(b"\tclassifier.py") else e
                   for e in entries if e]
        entries.append(b"100644 blob " + blob + b"\t" + name)
        literal_name = b"omitted-\\xff.txt"
        entries.append(b"100644 blob " + blob + b"\t" + literal_name)
        tree = plumbing("mktree", "-z", data=b"\x00".join(entries) + b"\x00")
        head = plumbing("-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
                        "-c", "commit.gpgsign=false", "commit-tree", tree.decode(), "-p", self.head,
                        data=b"Synthetic non-UTF-8 omitted path\n").decode()
        args = self.args(head=head)
        summary = plan.run(args)
        self.assertIn("omitted-\\xff.txt", summary["omitted_changed_paths"])
        index = summary["omitted_changed_paths_raw_hex"].index(name.hex())
        self.assertEqual(summary["omitted_changed_paths"][index], "omitted-\\xff.txt")
        self.assertEqual(bytes.fromhex(summary["omitted_changed_paths_raw_hex"][index]), name)
        literal_index = summary["omitted_changed_paths_raw_hex"].index(literal_name.hex())
        self.assertEqual(summary["omitted_changed_paths"][literal_index], summary["omitted_changed_paths"][index])
        raw = b"".join(p.read_bytes() for p in args.output.rglob("*") if p.is_file())
        self.assertNotIn(b"NON_UTF8_OMITTED_CONTENT_SENTINEL", raw)
        self.assertEqual(review.read_json(args.output / "preview.json"), summary)
        self.assertTrue(diff.verify_git_source(self.repo, args.output / "context", summary["content_id"]))

    def test_unsafe_paths_binary_links_and_bad_commits_reject(self):
        for updates in (dict(path=["../outside"]), dict(path=["classifier.py"] * 2),
                        dict(base=self.base[:7]), dict(head=self.base), dict(context_lines=-1)):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.spec(**updates)
        (self.repo / "binary.bin").write_bytes(b"a\x00b")
        (self.repo / "alias.txt").symlink_to("added.txt")
        head = self.commit("binary.bin", "alias.txt")
        for path in ("binary.bin", "alias.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.spec(head=head, path=[path])


if __name__ == "__main__":
    unittest.main()
