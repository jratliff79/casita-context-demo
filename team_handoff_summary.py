#!/usr/bin/env python3
"""Inspect a local synthetic handoff without running Casita or received evidence."""
import argparse
from pathlib import Path
import sys

from demo import canonical
from git_review import read_json
import team_handoff as team


def local_path(run, relative):
    """Use fixed demo paths and reject links in their directory components."""
    path = run
    if path.is_symlink() or not path.is_dir():
        raise ValueError("run must be a directory without a link")
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("summary inputs must not be linked")
    return path


def summarize(run):
    run = Path(run)
    receipt = read_json(local_path(run, "receipt.json"), 1_000_000)
    if (not isinstance(receipt, dict)
            or receipt.get("schema") != "casita-team-handoff.receipt.v1"
            or receipt.get("synthetic") is not True):
        raise ValueError("expected a local synthetic teammate handoff receipt")
    selected = receipt.get("selected_proposer")
    proposal_pins = receipt.get("proposal_pins")
    parent_pin, next_pin = receipt.get("input_pin"), receipt.get("next_pin")
    if (selected not in team.PEOPLE or not isinstance(proposal_pins, dict)
            or set(proposal_pins) != set(team.PEOPLE)
            or not isinstance(parent_pin, dict) or parent_pin.get("revision") != 1
            or not isinstance(next_pin, dict) or next_pin.get("revision") != 2):
        raise ValueError("missing or invalid expected handoff pins")
    parent = local_path(run, "owner/context-v1")
    team.verify_snapshot(parent, parent_pin)
    accepted = team.verify_snapshot(local_path(run, "owner/context-v2"), next_pin)
    proposals = {}
    for person in team.PEOPLE:
        pin = proposal_pins[person]
        if not isinstance(pin, dict) or pin.get("proposer") != person:
            raise ValueError("proposal pin differs from its expected teammate")
        proposals[person] = team.verify_proposal(
            local_path(run, f"owner/proposals/{person}"), pin, parent, parent_pin)
    decision = {"owner": "alice", "proposal_id": proposal_pins[selected]["proposal_id"],
                "proposer": selected, "kind": "explicit-scripted-owner-selection"}
    if (receipt.get("decision") != decision
            or accepted["manifest"]["decision"] != decision
            or accepted["manifest"]["parent_snapshot_id"] != parent_pin["snapshot_id"]
            or accepted["knowledge"]["entries"] != [proposals[selected]["replacement"]]):
        raise ValueError("selected knowledge, proposal or owner decision differs")
    # The receipt provides expected pins, not authority. Display only the frozen
    # bytes returned by the validators, never reopen their knowledge or task files.
    return {"schema": "casita-team-handoff.summary.v1", "synthetic": True,
            "selected_proposer": selected, "selected_proposal_id": decision["proposal_id"],
            "parent_snapshot_id": parent_pin["snapshot_id"],
            "selected_snapshot_id": next_pin["snapshot_id"],
            "knowledge": accepted["knowledge"]["entries"],
            "completed": accepted["task"]["completed"],
            "pending": accepted["task"]["pending"],
            "proposals": [{"proposer": person,
                           "proposal_id": proposal_pins[person]["proposal_id"],
                           "statement": proposals[person]["replacement"]["statement"],
                           "citation": proposals[person]["citation"],
                           "status": "selected" if person == selected else "not selected; needs fresh review"}
                          for person in team.PEOPLE],
            "limits": ["Expected pins come from this trusted local run receipt; they are unsigned.",
                       "Owner selection is scripted, not authenticated human approval.",
                       "Content and citation checks do not establish reasoning quality or execution."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path,
                        help="completed local team_handoff.py output directory")
    args = parser.parse_args()
    try:
        result = summarize(args.run)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Teammate summary failed: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(canonical(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
