import json
from pathlib import Path
import shutil
import tempfile
import unittest

import checkpoint as cp


class CheckpointChecks(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "output"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parents, self.pins = {}, {}
        for revision in ("v1", "v2"):
            folder = self.root / revision
            self.parents[revision] = folder
            self.pins[revision] = dict(cp.create_checkpoint(folder, revision),
                                      directory_key=f"synthetic-test-key-{revision}")

    def rewrite(self, folder, name, change):
        path = folder / name
        data = json.loads(path.read_bytes())
        change(data)
        path.write_bytes(cp.canonical(data))

    def repin(self, revision):
        folder = self.parents[revision]
        manifest = json.loads((folder / "manifest.json").read_bytes())
        manifest["files"] = {name: sha for name, sha in cp.file_map(folder).items()
                             if name != "manifest.json"}
        data = cp.canonical(manifest)
        (folder / "manifest.json").write_bytes(data)
        return dict(self.pins[revision], checkpoint_id=cp.digest(data))

    def result(self, revision="v2", branch="economy"):
        folder = self.root / f"result-{revision}-{branch}"
        pin = cp.write_continuation(folder, self.parents[revision], self.pins[revision], branch)
        return folder, pin

    def test_branches_resume_same_pending_task_without_marking_comparison_complete(self):
        results = [cp.scripted_continuation(self.parents["v2"], self.pins["v2"], b) for b in cp.BRANCHES]
        self.assertEqual([r["choice"]["id"] for r in results], ["park", "museum"])
        for result in results:
            self.assertEqual(result["parent_checkpoint_id"], self.pins["v2"]["checkpoint_id"])
            self.assertEqual(result["parent_directory_key"], self.pins["v2"]["directory_key"])
            self.assertEqual(result["pending"], ["compare-options"])
            self.assertEqual(result["completed"], ["inspect-budget", "draft-options"])

    def test_budget_update_changes_full_day_option(self):
        first = cp.scripted_continuation(self.parents["v1"], self.pins["v1"], "full-day")
        second = cp.scripted_continuation(self.parents["v2"], self.pins["v2"], "full-day")
        self.assertEqual(first["choice"]["id"], "studio")
        self.assertEqual(second["choice"]["id"], "museum")

    def test_stale_parent_rejects_even_with_valid_old_result_pin(self):
        folder, pin = self.result("v1", "full-day")
        with self.assertRaisesRegex(ValueError, "expected parent"):
            cp.verify_continuation(folder, pin, self.parents["v2"], self.pins["v2"], "full-day")

    def test_branch_swap_rejects_even_with_relabelled_result_pin(self):
        folder, pin = self.result()
        with self.assertRaisesRegex(ValueError, "expected parent"):
            cp.verify_continuation(folder, dict(pin, branch="full-day"),
                                   self.parents["v2"], self.pins["v2"], "full-day")

    def test_repinned_result_cannot_rebind_parent_choice_or_task_progress(self):
        folder, pin = self.result()
        original = (folder / "result.json").read_bytes()
        for field, value in (("parent_checkpoint_id", self.pins["v1"]["checkpoint_id"]),
                             ("parent_directory_key", "synthetic-other-key"),
                             ("choice", {"id": "studio", "cost": 85, "minutes": 240}),
                             ("pending", []), ("inputs_sha256", "0" * 64)):
            with self.subTest(field=field):
                result = json.loads(original)
                result[field] = value
                data = cp.canonical(result)
                (folder / "result.json").write_bytes(data)
                with self.assertRaisesRegex(ValueError, "expected parent"):
                    cp.verify_continuation(folder, dict(pin, result_id=cp.digest(data)),
                                           self.parents["v2"], self.pins["v2"], "economy")

    def test_mixed_progress_and_tampered_prompt_reject(self):
        folder = self.parents["v2"]
        original = {name: (folder / name).read_bytes() for name in ("progress.json", "manifest.json")}
        shutil.copyfile(self.parents["v1"] / "progress.json", folder / "progress.json")
        with self.assertRaisesRegex(ValueError, "file hashes"):
            cp.verify_checkpoint(folder, self.pins["v2"])
        with self.assertRaisesRegex(ValueError, "progress binding"):
            cp.verify_checkpoint(folder, self.repin("v2"))
        for name, data in original.items():
            (folder / name).write_bytes(data)
        (folder / "prompt.txt").write_bytes(b"SYNTHETIC changed prompt")
        with self.assertRaisesRegex(ValueError, "file hashes"):
            cp.verify_checkpoint(folder, self.pins["v2"])

    def test_missing_extra_or_linked_checkpoint_file_rejects(self):
        folder = self.parents["v2"]
        target = folder / "notes.txt"
        original = target.read_bytes()
        target.unlink()
        with self.assertRaisesRegex(ValueError, "file set"):
            cp.verify_checkpoint(folder, self.pins["v2"])
        target.write_bytes(original)
        extra = folder / "unexpected.txt"
        extra.write_text("synthetic extra")
        with self.assertRaisesRegex(ValueError, "file set"):
            cp.verify_checkpoint(folder, self.pins["v2"])
        extra.unlink()
        target.unlink()
        target.symlink_to(self.parents["v1"] / "notes.txt")
        with self.assertRaisesRegex(ValueError, "link"):
            cp.verify_checkpoint(folder, self.pins["v2"])

    def test_invalid_inputs_reject_even_when_repinned(self):
        folder = self.parents["v2"]
        original = (folder / "inputs.json").read_bytes()
        changes = [lambda d: d.update(budget=True), lambda d: d.update(budget=-1),
                   lambda d: d["choices"][1].update(id="park"),
                   lambda d: d["choices"][1].update(cost="70"),
                   lambda d: d["choices"][1].update(id="../outside")]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                (folder / "inputs.json").write_bytes(original)
                self.rewrite(folder, "inputs.json", change)
                with self.assertRaises(ValueError):
                    cp.verify_checkpoint(folder, self.repin("v2"))

    def test_repinned_false_tool_response_rejects(self):
        self.rewrite(self.parents["v2"], "tool-responses.json", lambda d: d.update(eligible_ids=["studio"]))
        with self.assertRaisesRegex(ValueError, "recorded response"):
            cp.verify_checkpoint(self.parents["v2"], self.repin("v2"))

    def test_repinned_revision_or_task_metadata_rejects(self):
        folder = self.parents["v2"]
        original = (folder / "manifest.json").read_bytes()
        for field, value in (("revision", "v1"), ("task_id", "synthetic-other-task")):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                encoded = cp.canonical(data)
                (folder / "manifest.json").write_bytes(encoded)
                with self.assertRaisesRegex(ValueError, "task or revision"):
                    cp.verify_checkpoint(folder, dict(self.pins["v2"], checkpoint_id=cp.digest(encoded)))

    def test_prompt_is_preserved_as_data_not_executed_or_interpreted(self):
        sentinel = self.root / "must-not-exist"
        (self.parents["v2"] / "prompt.txt").write_text(
            "SYNTHETIC received instruction: " + f"open({str(sentinel)!r}, 'w').write('unexpected')")
        result = cp.scripted_continuation(self.parents["v2"], self.repin("v2"), "economy")
        self.assertEqual(result["choice"]["id"], "park")
        self.assertFalse(sentinel.exists())

    def test_unpinned_parent_or_unknown_branch_rejects(self):
        for pin, branch in ((dict(self.pins["v2"], directory_key=""), "economy"),
                            (self.pins["v2"], "../other")):
            with self.subTest(branch=branch), self.assertRaises(ValueError):
                cp.scripted_continuation(self.parents["v2"], pin, branch)

    def test_result_pin_extra_file_or_altered_bytes_rejects(self):
        folder, pin = self.result()
        cp.verify_continuation(folder, pin, self.parents["v2"], self.pins["v2"], "economy")
        extra = folder / "unexpected.txt"
        extra.write_bytes(b"synthetic extra")
        with self.assertRaisesRegex(ValueError, "file set or pin"):
            cp.verify_continuation(folder, pin, self.parents["v2"], self.pins["v2"], "economy")
        extra.unlink()
        (folder / "result.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(ValueError, "file set or pin"):
            cp.verify_continuation(folder, pin, self.parents["v2"], self.pins["v2"], "economy")


if __name__ == "__main__":
    unittest.main()
