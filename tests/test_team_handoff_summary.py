import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import team_handoff as team
import team_handoff_summary as summary


class TeamSummaryChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_run(self, selected="bob"):
        run = self.root / selected
        parent = run / "owner/context-v1"
        parent_pin = dict(team.initial_snapshot(parent), directory_key="synthetic-parent-key")
        pins = {}
        for person in team.PEOPLE:
            pins[person] = team.write_proposal(run / f"owner/proposals/{person}",
                                               parent, parent_pin, person)
        decision = {"owner": "alice", "proposal_id": pins[selected]["proposal_id"],
                    "proposer": selected, "kind": "explicit-scripted-owner-selection"}
        next_pin = team.accept_proposal(run / "owner/context-v2", run / f"owner/proposals/{selected}",
                                        pins[selected], parent, parent_pin, decision)
        receipt = {"schema": "casita-team-handoff.receipt.v1", "synthetic": True,
                   "input_pin": parent_pin, "next_pin": dict(next_pin, directory_key="synthetic-next-key"),
                   "proposal_pins": pins, "selected_proposer": selected, "decision": decision}
        self.save_receipt(run, receipt)
        return run, receipt

    def save_receipt(self, run, receipt):
        (run / "receipt.json").write_bytes(team.canonical(receipt))

    def repin_selected(self, run, receipt, mutate):
        path = run / "owner/context-v2/manifest.json"
        manifest = team.read_json(path)
        mutate(manifest)
        raw = team.canonical(manifest)
        path.write_bytes(raw)
        receipt["next_pin"]["snapshot_id"] = team.digest(raw)
        self.save_receipt(run, receipt)

    def test_both_choices_show_exact_selected_knowledge_and_retained_advice_without_writes(self):
        for person in team.PEOPLE:
            with self.subTest(person=person):
                run, receipt = self.make_run(person)
                before = team.file_map(run)
                result = summary.summarize(run)
                self.assertEqual(result["knowledge"], [team.entry(team.STATEMENTS[person])])
                self.assertEqual(result["pending"], ["test-in-a-real-workflow"])
                self.assertEqual(result["selected_proposal_id"], receipt["proposal_pins"][person]["proposal_id"])
                self.assertEqual([p["proposer"] for p in result["proposals"] if p["status"] == "selected"], [person])
                self.assertEqual(team.file_map(run), before)

    def test_success_flags_do_not_bypass_changed_proposal(self):
        run, receipt = self.make_run()
        receipt.update(ok=True, integrity_audits_passed=True, accepted_proposal_shared_back=True)
        self.save_receipt(run, receipt)
        (run / "owner/proposals/bob/proposal.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "proposal file set or pin mismatch"):
            summary.summarize(run)

    def test_valid_but_competing_selected_knowledge_rejects_even_with_fresh_hashes(self):
        run, receipt = self.make_run()
        path = run / "owner/context-v2/knowledge.json"
        knowledge = team.read_json(path)
        knowledge["entries"] = [team.entry(team.STATEMENTS["carol"])]
        path.write_bytes(team.canonical(knowledge))
        self.repin_selected(run, receipt, lambda m: m["files"].update(
            {"knowledge.json": team.digest(path.read_bytes())}))
        with self.assertRaisesRegex(ValueError, "selected knowledge"):
            summary.summarize(run)

    def test_rehashed_wrong_lineage_and_receipt_selected_person_reject(self):
        run, receipt = self.make_run()
        self.repin_selected(run, receipt, lambda m: m.update(parent_snapshot_id="0" * 64))
        with self.assertRaisesRegex(ValueError, "owner decision differs"):
            summary.summarize(run)
        other, receipt = self.make_run("carol")
        receipt["selected_proposer"] = "bob"
        receipt["decision"].update(proposer="bob", proposal_id=receipt["proposal_pins"]["bob"]["proposal_id"])
        self.save_receipt(other, receipt)
        with self.assertRaisesRegex(ValueError, "owner decision differs"):
            summary.summarize(other)

    def test_unselected_invalid_citation_rejects_with_fresh_proposal_hash(self):
        run, receipt = self.make_run()
        path = run / "owner/proposals/carol/proposal.json"
        proposal = team.read_json(path)
        proposal["citation"]["excerpt"] = ["invented policy"]
        raw = team.canonical(proposal)
        path.write_bytes(raw)
        receipt["proposal_pins"]["carol"]["proposal_id"] = team.digest(raw)
        self.save_receipt(run, receipt)
        with self.assertRaisesRegex(ValueError, "citation differs"):
            summary.summarize(run)

    def test_linked_directory_and_linked_receipt_reject(self):
        run, _ = self.make_run()
        (run / "owner").rename(run / "relocated-owner")
        (run / "owner").symlink_to(run / "relocated-owner", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "linked"):
            summary.summarize(run)
        other, _ = self.make_run("carol")
        (other / "receipt.json").rename(other / "relocated-receipt.json")
        (other / "receipt.json").symlink_to(other / "relocated-receipt.json")
        with self.assertRaisesRegex(ValueError, "linked"):
            summary.summarize(other)

    def test_verified_selected_task_is_not_reopened(self):
        run, _ = self.make_run()
        task = run / "owner/context-v2/task.json"
        real_read = team.read_regular
        reads = []
        def read(path, limit):
            if path == task:
                reads.append(path)
                if len(reads) > 1:
                    return b'{"pending": ["unverified"]}'
            return real_read(path, limit)
        with patch.object(team, "read_regular", side_effect=read):
            result = summary.summarize(run)
        self.assertEqual(result["pending"], ["test-in-a-real-workflow"])
        self.assertEqual(len(reads), 1)

    def test_bad_input_cli_returns_failure_without_summary_stdout(self):
        run, _ = self.make_run()
        (run / "receipt.json").write_text('{"synthetic":true,"synthetic":false}')
        result = subprocess.run([sys.executable, str(Path(summary.__file__)), "--run", str(run)],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"duplicate JSON key", result.stderr)


if __name__ == "__main__":
    unittest.main()
