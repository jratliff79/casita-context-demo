import argparse
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_request as api
import context_request_example as example
import demo
import git_diff_review as diff
import git_review as review


class ContextRequestChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repository = self.root / "repo"
        self.base, self.head = example.make_repository(self.repository)
        self.parent = self.root / "parent"
        self.spec = example.spec(self.base, self.head)
        spec_path = self.root / "initial.json"
        spec_path.write_bytes(demo.canonical(self.spec))
        ident = diff.create_context(self.parent, self.repository, spec_path)
        self.pins = dict(schema="casita-context-demo.review-input-pins.v1", archive_sha256="0" * 64,
                         content_id=ident, directory_key="casita.directory.v1:" + "a" * 43)
        self.manifest, _ = diff.verify_context(self.parent, ident)
        self.request = example.make_request(self.manifest, self.pins)
        self.approved = example.spec(self.base, self.head, True)

    def supplement(self, observation=None, spec=None):
        path = self.root / "supplement-spec.json"
        path.write_bytes(demo.canonical(spec or self.approved))
        obs = self.root / "observation.json"
        obs.write_bytes(demo.canonical(observation or api.response_observation(self.request)))
        context = self.root / "supplement"
        ident = diff.create_context(context, self.repository, path, obs)
        return context, dict(self.pins, content_id=ident,
                             directory_key="casita.directory.v1:" + "b" * 43)

    def test_approved_supplement_and_source_bound_finding(self):
        summary = api.preview_response(self.repository, self.parent, self.pins, self.request,
                                       self.approved, self.root / "preview")
        self.assertEqual(summary["paths"], ["gate.py", "numbers_helper.py"])
        context, pins = self.supplement()
        binding = api.verify_response(self.parent, self.pins, self.request, context, pins)
        self.assertTrue(binding["request_binding_verified"])
        self.assertTrue(diff.verify_git_source(self.repository, context, pins["content_id"]))
        report = example.make_report(context, pins, True)
        diff.validate_report(report, context, pins)
        self.assertEqual(len(report["findings"][0]["citations"]), 3)
        self.assertNotEqual(self.pins["content_id"], pins["content_id"])
        with self.assertRaisesRegex(ValueError, "original Git diff context"):
            diff.validate_report(report, self.parent, self.pins)

    def test_request_binding_schema_and_pin_schema(self):
        for field, value in (("context_id", "0" * 64), ("context_directory_key", "wrong"),
                             ("base_commit", "0" * 40), ("head_commit", "0" * 40),
                             ("schema", "wrong"), ("extra", True)):
            request = dict(self.request, **{field: value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "parent context"):
                api.validate_request(request, self.parent, self.pins)
        with self.assertRaisesRegex(ValueError, "pin schema"):
            api.validate_request(self.request, self.parent, dict(self.pins, extra=True))

    def test_unsafe_unbounded_duplicate_and_boolean_ranges_reject(self):
        for update in (dict(path="../outside"), dict(start=True), dict(end=401),
                       dict(version="other"), dict(extra=True)):
            request = copy.deepcopy(self.request)
            request["selections"][0].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                api.validate_request(request, self.parent, self.pins)
        for items in ([], self.request["selections"] * 11, self.request["selections"] * 2):
            with self.assertRaises(ValueError):
                api.validate_request(dict(self.request, selections=items), self.parent, self.pins)

    def test_unapproved_source_rejects_before_supplementary_reads_or_output(self):
        output = self.root / "no-output"
        with patch.object(api.diff, "verify_git_source") as read_parent, \
                patch.object(api.plan, "preview") as read_supplement:
            with self.assertRaisesRegex(ValueError, "explicit approved spec"):
                api.preview_response(self.repository, self.parent, self.pins, self.request, self.spec, output)
            read_parent.assert_not_called()
            read_supplement.assert_not_called()
        self.assertFalse(output.exists())

    def test_approval_cannot_change_commits_sensitivity_or_allowlist(self):
        for update in (dict(head_commit=self.base), dict(sensitivity="restricted"),
                       dict(paths=["gate.py"]), dict(extra=True),
                       dict(selections=self.approved["selections"] * 2)):
            with self.subTest(update=update), self.assertRaises(ValueError):
                api.validate_approval(dict(self.approved, **update), self.request, self.manifest)

    def test_rebound_metadata_cannot_answer_a_different_parent_or_request(self):
        for field in ("parent_context_id", "parent_directory_key", "request_sha256", "head_commit"):
            with self.subTest(field=field):
                observation = api.response_observation(self.request)
                observation["context_response"][field] = "wrong"
                context, pins = self.supplement(observation)
                with self.assertRaisesRegex(ValueError, "exact request and parent"):
                    api.verify_response(self.parent, self.pins, self.request, context, pins)
                # Metadata can be false even when all captured source matches original Git.
                self.assertTrue(diff.verify_git_source(self.repository, context, pins["content_id"]))
                import shutil
                shutil.rmtree(context)
        context, pins = self.supplement()
        request = dict(self.request, reason="Changed after sender approval")
        with self.assertRaisesRegex(ValueError, "exact request and parent"):
            api.verify_response(self.parent, self.pins, request, context, pins)

    def test_matching_metadata_still_needs_requested_ranges_and_same_commits(self):
        context, pins = self.supplement(spec=self.spec)
        with self.assertRaisesRegex(ValueError, "cover the requested"):
            api.verify_response(self.parent, self.pins, self.request, context, pins)
        import shutil
        shutil.rmtree(context)
        # Both commits remain valid but the supplement reverses the comparison.
        spec = example.spec(self.head, self.base, True)
        spec["selections"][0]["end"] = 6
        spec["selections"][1]["end"] = 4
        context, pins = self.supplement(spec=spec)
        with self.assertRaisesRegex(ValueError, "parent commits"):
            api.verify_response(self.parent, self.pins, self.request, context, pins)

    def test_existing_output_and_linked_request_preserved_or_rejected(self):
        output = self.root / "existing"
        output.mkdir()
        (output / "sentinel").write_text("preserve")
        with self.assertRaises(FileExistsError):
            api.preview_response(self.repository, self.parent, self.pins, self.request, self.approved, output)
        self.assertEqual((output / "sentinel").read_text(), "preserve")
        link = self.root / "request-link.json"
        link.symlink_to(self.root / "initial.json")
        with self.assertRaises((ValueError, OSError)):
            review.read_json(link)

    def test_dirty_checkout_and_local_paths_are_excluded(self):
        output = self.root / "preview"
        api.preview_response(self.repository, self.parent, self.pins, self.request, self.approved, output)
        raw = b"".join(p.read_bytes() for p in (output / "context").rglob("*") if p.is_file())
        self.assertNotIn(b"SYNTHETIC_DIRTY_SENTINEL", raw)
        self.assertNotIn(b"SYNTHETIC_UNSELECTED_SENTINEL", raw)
        self.assertNotIn(str(self.root).encode(), raw)
        self.assertEqual([f["path"] for f in review.read_json(self.parent / "changes.json")["files"]], ["gate.py"])

    def test_failure_removes_throwaway_keys(self):
        output = self.root / "failed-demo"
        with patch.object(example, "make_repository", side_effect=RuntimeError("controlled failure")):
            with self.assertRaisesRegex(RuntimeError, "controlled failure"):
                example.run(argparse.Namespace(output=output, casita="unused"))
        self.assertFalse((output / "throwaway-keys").exists())
        self.assertTrue(review.read_json(output / "receipt.json")["throwaway_private_keys_removed"])


if __name__ == "__main__":
    unittest.main()
