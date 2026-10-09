import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import team_handoff as team


class TeamHandoffChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "parent"
        self.parent_pin = dict(team.initial_snapshot(self.parent), directory_key="synthetic-parent-key")
        self.proposals, self.pins = {}, {}
        for person in team.PEOPLE:
            folder = self.root / person
            self.proposals[person] = folder
            self.pins[person] = team.write_proposal(folder, self.parent, self.parent_pin, person)

    def decision(self, person="bob"):
        return {"owner": "alice", "proposal_id": self.pins[person]["proposal_id"],
                "proposer": person, "kind": "explicit-scripted-owner-selection"}

    def accept(self, person="bob"):
        folder = self.root / "accepted"
        pin = team.accept_proposal(folder, self.proposals[person], self.pins[person],
                                   self.parent, self.parent_pin, self.decision(person))
        return folder, dict(pin, directory_key="synthetic-next-key")

    def mutate_proposal(self, change, person="bob"):
        folder = self.proposals[person]
        data = json.loads((folder / "proposal.json").read_bytes())
        change(data)
        raw = team.canonical(data)
        (folder / "proposal.json").write_bytes(raw)
        return dict(self.pins[person], proposal_id=team.digest(raw))

    def verify(self, pin=None):
        return team.verify_proposal(self.proposals["bob"], pin or self.pins["bob"],
                                    self.parent, self.parent_pin)

    def test_competing_returns_remain_proposals_until_exact_owner_selection(self):
        original = team.file_map(self.parent)
        results = [team.verify_proposal(self.proposals[p], self.pins[p], self.parent, self.parent_pin)
                   for p in team.PEOPLE]
        self.assertEqual(team.file_map(self.parent), original)
        self.assertEqual(results[0]["parent"], results[1]["parent"])
        self.assertNotEqual(results[0]["replacement"], results[1]["replacement"])
        for person in team.PEOPLE:
            self.assertTrue((self.proposals[person] / "proposal.json").is_file())

    def test_acceptance_changes_only_selected_knowledge_and_records_lineage(self):
        original = team.file_map(self.parent)
        folder, pin = self.accept("carol")
        verified = team.verify_snapshot(folder, pin)
        self.assertEqual(verified["knowledge"]["entries"], [team.entry(team.STATEMENTS["carol"])])
        manifest = team.read_json(folder / "manifest.json")
        self.assertEqual(manifest["parent_snapshot_id"], self.parent_pin["snapshot_id"])
        self.assertEqual(manifest["decision"], self.decision("carol"))
        self.assertEqual(team.file_map(self.parent), original)
        self.assertEqual(team.read_json(folder / "task.json")["pending"], ["test-in-a-real-workflow"])

    def test_missing_wrong_owner_or_different_proposal_decision_writes_nothing(self):
        for decision in (None, {}, dict(self.decision(), owner="bob"), self.decision("carol"),
                         dict(self.decision(), proposal_id="0" * 64)):
            with self.subTest(decision=decision):
                folder = self.root / "unapproved"
                with self.assertRaisesRegex(ValueError, "explicit owner"):
                    team.accept_proposal(folder, self.proposals["bob"], self.pins["bob"],
                                         self.parent, self.parent_pin, decision)
                self.assertFalse(folder.exists())

    def test_both_old_returns_reject_against_new_selected_snapshot(self):
        folder, pin = self.accept()
        for person in team.PEOPLE:
            with self.subTest(person=person), self.assertRaisesRegex(ValueError, "stale"):
                team.verify_proposal(self.proposals[person], self.pins[person], folder, pin)
        with self.assertRaises(ValueError):
            team.accept_proposal(self.root / "overwritten", self.proposals["carol"], self.pins["carol"],
                                 folder, pin, self.decision("carol"))
        self.assertFalse((self.root / "overwritten").exists())

    def test_rehashed_wrong_parent_task_team_or_owner_is_still_invalid(self):
        original = (self.proposals["bob"] / "proposal.json").read_bytes()
        changes = [lambda p: p["parent"].update(snapshot_id="0" * 64),
                   lambda p: p["parent"].update(directory_key="other-key"),
                   lambda p: p["parent"].update(knowledge_sha256="0" * 64),
                   lambda p: p.update(task_id="other-task"), lambda p: p.update(team_id="other-team"),
                   lambda p: p.update(proposer="carol"),
                   lambda p: p["replacement"].update(owner="bob"),
                   lambda p: p.update(approved=True)]
        for change in changes:
            (self.proposals["bob"] / "proposal.json").write_bytes(original)
            with self.assertRaises(ValueError):
                self.verify(self.mutate_proposal(change))

    def test_citations_reject_escape_bad_ranges_hash_and_invented_text(self):
        original = (self.proposals["bob"] / "proposal.json").read_bytes()
        for fields in ({"path": "../outside.txt"}, {"start_line": 0}, {"start_line": True},
                       {"end_line": 9}, {"end_line": 1}, {"file_sha256": "0" * 64},
                       {"excerpt": ["made up"]}):
            (self.proposals["bob"] / "proposal.json").write_bytes(original)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.verify(self.mutate_proposal(lambda p: p["citation"].update(fields)))

    def test_linked_extra_or_changed_proposal_rejects(self):
        folder = self.proposals["bob"]
        path = folder / "proposal.json"
        original = path.read_bytes()
        path.write_bytes(b"{}\n")
        with self.assertRaises(ValueError):
            self.verify()
        path.write_bytes(original)
        extra = folder / "extra.txt"
        extra.write_text("synthetic")
        with self.assertRaises(ValueError):
            self.verify()
        extra.unlink()
        path.unlink()
        path.symlink_to(self.proposals["carol"] / "proposal.json")
        with self.assertRaisesRegex(ValueError, "link"):
            self.verify()

    def test_rehashed_snapshot_with_false_progress_or_source_rejects(self):
        for name, value in (("task.json", b"{}\n"), (team.SOURCE, b"different source\n")):
            path = self.parent / name
            original = path.read_bytes()
            manifest_path = self.parent / "manifest.json"
            original_manifest = manifest_path.read_bytes()
            path.write_bytes(value)
            manifest = json.loads(original_manifest)
            manifest["files"][name] = team.digest(value)
            raw = team.canonical(manifest)
            manifest_path.write_bytes(raw)
            with self.assertRaises(ValueError):
                team.verify_snapshot(self.parent, dict(self.parent_pin, snapshot_id=team.digest(raw)))
            path.write_bytes(original)
            manifest_path.write_bytes(original_manifest)

    def test_received_knowledge_text_is_data_and_not_automatically_judged(self):
        sentinel = self.root / "must-not-exist"
        text = f"open({str(sentinel)!r}, 'w').write('unexpected')"
        pin = self.mutate_proposal(lambda p: p["replacement"].update(statement=text))
        proposal = self.verify(pin)
        self.assertEqual(proposal["replacement"]["statement"], text)
        self.assertFalse(sentinel.exists())
        self.assertEqual(team.verify_snapshot(self.parent, self.parent_pin)["knowledge"]["entries"][0]["owner"],
                         "alice")

    def test_changed_proposal_between_hashing_and_reading_rejects(self):
        real_read = team.read_regular
        def replace(path, limit):
            if path == self.proposals["bob"] / "proposal.json":
                return b"{}\n"
            return real_read(path, limit)
        with patch.object(team, "read_regular", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                self.verify()

    def test_changed_snapshot_between_hashing_and_reading_rejects(self):
        real_read = team.read_regular
        def replace(path, limit):
            if path == self.parent / "knowledge.json":
                return b"{}\n"
            return real_read(path, limit)
        with patch.object(team, "read_regular", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "changed while reading"):
                team.verify_snapshot(self.parent, self.parent_pin)

    def test_existing_output_is_preserved_and_invalid_choice_creates_nothing(self):
        output = Path(__file__).resolve().parents[1] / "output"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output) as existing:
            relative = Path("output") / Path(existing).name
            sentinel = Path(existing) / "keep.txt"
            sentinel.write_text("keep")
            with self.assertRaises(FileExistsError):
                team.run_demo("unused", relative, "bob")
            self.assertEqual(sentinel.read_text(), "keep")
        with patch.object(team, "new_output") as create:
            with self.assertRaises(ValueError):
                team.run_demo("unused", Path("output/unused"), "unknown")
            create.assert_not_called()

    def test_unexpected_imported_graph_or_replaced_existing_root_prevents_checkout(self):
        archive = self.root / "synthetic.casitar"
        archive.write_bytes(b"synthetic archive fixture")
        original = {"existing": "old-key"}
        for after in ({"existing": "old-key", "received": "expected-key", "extra": "other-key"},
                      {"existing": "changed-key", "received": "expected-key"},
                      {"existing": "old-key", "received": "other-key"}):
            with self.subTest(after=after):
                store = Mock()
                store.roots.side_effect = [original, after]
                with self.assertRaisesRegex(ValueError, "imported roots"):
                    team.restore(store, archive, team.digest(archive.read_bytes()), "expected-key",
                                 self.root / "restored", "received")
                self.assertFalse(any(call.args[0] == "checkout" for call in store.run.call_args_list))
                self.assertFalse((self.root / "restored").exists())

    def test_wrong_archive_pin_rejects_before_any_store_command(self):
        archive = self.root / "synthetic.casitar"
        archive.write_bytes(b"synthetic archive fixture")
        store = Mock()
        with self.assertRaisesRegex(ValueError, "archive SHA"):
            team.restore(store, archive, "0" * 64, "expected-key", self.root / "restored", "received")
        store.run.assert_not_called()
        store.roots.assert_not_called()


if __name__ == "__main__":
    unittest.main()
