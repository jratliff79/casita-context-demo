#!/usr/bin/env python3
"""Exchange synthetic teammate proposals and explicitly select a knowledge update."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

from artifacts import new_output, rejected
from authentication import read_regular
from checkpoint import sha
from demo import Casita, canonical, checked_archive, digest, file_map

TEAM = "synthetic-venue-team"
TASK = "document-retry-limit"
SOURCE = "source/retry-policy.txt"
POLICY = (b"SYNTHETIC VENUE KIT POLICY\n"
          b"Retries must not exceed two additional attempts.\n"
          b"The operator may choose a lower retry limit.\n")
SNAPSHOT_FILES = {SOURCE, "task.json", "knowledge.json", "manifest.json"}
PEOPLE = ("bob", "carol")
STATEMENTS = {"bob": "Allow at most two additional attempts.",
              "carol": "Default to one additional attempt, within the two-attempt limit."}


def read_json(path):
    return json.loads(read_regular(path, 100_000))


def entry(statement):
    return {"id": "retry-limit", "statement": statement, "owner": "alice"}


def write_snapshot(folder, knowledge, revision, parent_id=None, decision=None):
    folder.mkdir(parents=True)
    (folder / "source").mkdir()
    (folder / SOURCE).write_bytes(POLICY)
    task = {"schema": "casita-team-task.v1", "synthetic": True,
            "team_id": TEAM, "task_id": TASK, "completed": ["read-policy"],
            "pending": ["choose-retry-guidance", "test-in-a-real-workflow"]}
    if decision is not None:
        task["completed"].append("choose-retry-guidance")
        task["pending"].remove("choose-retry-guidance")
    (folder / "task.json").write_bytes(canonical(task))
    (folder / "knowledge.json").write_bytes(canonical(knowledge))
    manifest = {"schema": "casita-team-snapshot.v1", "synthetic": True,
                "team_id": TEAM, "task_id": TASK, "revision": revision,
                "parent_snapshot_id": parent_id, "decision": decision,
                "files": file_map(folder)}
    raw = canonical(manifest)
    (folder / "manifest.json").write_bytes(raw)
    return {"snapshot_id": digest(raw), "revision": revision}


def initial_snapshot(folder):
    knowledge = {"schema": "casita-team-knowledge.v1", "synthetic": True,
                 "entries": [entry("Choose retry guidance after reviewing the policy.")]}
    return write_snapshot(folder, knowledge, 1)


def verify_snapshot(folder, pin):
    if (not isinstance(pin, dict) or not sha(pin.get("snapshot_id"))
            or type(pin.get("revision")) is not int or pin["revision"] not in (1, 2)):
        raise ValueError("invalid expected snapshot pin")
    actual = file_map(folder)
    if set(actual) != SNAPSHOT_FILES or actual.pop("manifest.json") != pin["snapshot_id"]:
        raise ValueError("snapshot file set or pin mismatch")
    captured = {name: read_regular(folder / name, 100_000) for name in SNAPSHOT_FILES}
    if ({name: digest(raw) for name, raw in captured.items() if name != "manifest.json"} != actual
            or digest(captured["manifest.json"]) != pin["snapshot_id"]):
        raise ValueError("snapshot changed while reading")
    manifest = json.loads(captured["manifest.json"])
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema", "synthetic", "team_id", "task_id", "revision",
                                 "parent_snapshot_id", "decision", "files"}
            or manifest["schema"] != "casita-team-snapshot.v1"
            or manifest["synthetic"] is not True or manifest["team_id"] != TEAM
            or manifest["task_id"] != TASK or type(manifest["revision"]) is not int
            or manifest["revision"] != pin["revision"] or manifest["files"] != actual):
        raise ValueError("snapshot metadata or file hashes mismatch")
    # This is a bounded synthetic fixture, not an arbitrary chat export reader.
    if captured[SOURCE] != POLICY:
        raise ValueError("unexpected synthetic source")
    knowledge = json.loads(captured["knowledge.json"])
    if (not isinstance(knowledge, dict)
            or set(knowledge) != {"schema", "synthetic", "entries"}
            or knowledge["schema"] != "casita-team-knowledge.v1"
            or knowledge["synthetic"] is not True or not isinstance(knowledge["entries"], list)
            or len(knowledge["entries"]) != 1
            or not isinstance(knowledge["entries"][0], dict)
            or set(knowledge["entries"][0]) != {"id", "statement", "owner"}
            or knowledge["entries"][0]["id"] != "retry-limit"
            or knowledge["entries"][0]["owner"] != "alice"
            or not isinstance(knowledge["entries"][0]["statement"], str)
            or not 0 < len(knowledge["entries"][0]["statement"]) <= 500):
        raise ValueError("invalid synthetic knowledge entry")
    decision = manifest["decision"]
    if pin["revision"] == 1:
        if manifest["parent_snapshot_id"] is not None or decision is not None:
            raise ValueError("initial snapshot has unexpected lineage")
    else:
        if (not sha(manifest["parent_snapshot_id"]) or not isinstance(decision, dict)
                or set(decision) != {"owner", "proposal_id", "proposer", "kind"}
                or decision["owner"] != "alice" or not sha(decision["proposal_id"])
                or decision["proposer"] not in PEOPLE
                or decision["kind"] != "explicit-scripted-owner-selection"):
            raise ValueError("invalid recorded owner decision")
    expected_task = {"schema": "casita-team-task.v1", "synthetic": True,
                     "team_id": TEAM, "task_id": TASK, "completed": ["read-policy"],
                     "pending": ["choose-retry-guidance", "test-in-a-real-workflow"]}
    if decision is not None:
        expected_task["completed"].append("choose-retry-guidance")
        expected_task["pending"].remove("choose-retry-guidance")
    task = json.loads(captured["task.json"])
    if task != expected_task:
        raise ValueError("task progress mismatch")
    return {"knowledge": knowledge, "knowledge_sha256": digest(captured["knowledge.json"]),
            "task": task, "manifest": manifest}


def parent_binding(folder, pin):
    verified = verify_snapshot(folder, pin)
    if not isinstance(pin.get("directory_key"), str) or not pin["directory_key"]:
        raise ValueError("missing trusted parent directory key")
    return {"snapshot_id": pin["snapshot_id"], "directory_key": pin["directory_key"],
            "knowledge_sha256": verified["knowledge_sha256"]}


def write_proposal(folder, parent, pin, proposer):
    if proposer not in PEOPLE:
        raise ValueError("unknown scripted teammate")
    proposal = {"schema": "casita-team-proposal.v1", "synthetic": True, "scripted": True,
                "team_id": TEAM, "task_id": TASK, "proposer": proposer,
                "parent": parent_binding(parent, pin),
                "replacement": entry(STATEMENTS[proposer]),
                "citation": {"path": SOURCE, "file_sha256": digest(POLICY),
                             "start_line": 2, "end_line": 3,
                             "excerpt": POLICY.decode().splitlines()[1:3]}}
    folder.mkdir(parents=True)
    raw = canonical(proposal)
    (folder / "proposal.json").write_bytes(raw)
    return {"proposal_id": digest(raw), "proposer": proposer}


def verify_proposal(folder, pin, parent, parent_pin):
    if (not isinstance(pin, dict) or not sha(pin.get("proposal_id"))
            or pin.get("proposer") not in PEOPLE):
        raise ValueError("invalid expected proposal pin")
    if file_map(folder) != {"proposal.json": pin["proposal_id"]}:
        raise ValueError("proposal file set or pin mismatch")
    # Freeze exact pinned bytes before parsing so a later file replacement cannot
    # turn a verified proposal into different text during acceptance.
    raw = read_regular(folder / "proposal.json", 100_000)
    if digest(raw) != pin["proposal_id"]:
        raise ValueError("proposal changed while reading")
    proposal = json.loads(raw)
    if (not isinstance(proposal, dict)
            or set(proposal) != {"schema", "synthetic", "scripted", "team_id", "task_id",
                                 "proposer", "parent", "replacement", "citation"}
            or proposal["schema"] != "casita-team-proposal.v1"
            or proposal["synthetic"] is not True or proposal["scripted"] is not True
            or proposal["team_id"] != TEAM or proposal["task_id"] != TASK
            or proposal["proposer"] != pin["proposer"]):
        raise ValueError("proposal task, team or schema mismatch")
    if proposal["parent"] != parent_binding(parent, parent_pin):
        raise ValueError("stale or incorrectly bound proposal parent")
    replacement = proposal["replacement"]
    if (not isinstance(replacement, dict) or set(replacement) != {"id", "statement", "owner"}
            or replacement["id"] != "retry-limit" or replacement["owner"] != "alice"
            or not isinstance(replacement["statement"], str)
            or not 0 < len(replacement["statement"]) <= 500):
        raise ValueError("invalid proposed knowledge update")
    citation = proposal["citation"]
    if (not isinstance(citation, dict)
            or set(citation) != {"path", "file_sha256", "start_line", "end_line", "excerpt"}
            or citation["path"] != SOURCE or not sha(citation["file_sha256"])
            or type(citation["start_line"]) is not int or type(citation["end_line"]) is not int):
        raise ValueError("invalid source citation")
    source = POLICY  # The verified parent requires these exact synthetic bytes.
    lines = source.decode().splitlines()
    start, end = citation["start_line"], citation["end_line"]
    if (not 1 <= start <= end <= len(lines) or citation["file_sha256"] != digest(source)
            or citation["excerpt"] != lines[start - 1:end]):
        raise ValueError("citation differs from original source")
    return proposal


def accept_proposal(folder, proposal_folder, proposal_pin, current, current_pin, decision):
    # The decision comes from this trusted caller, never from the received proposal.
    if (not isinstance(decision, dict)
            or decision != {"owner": "alice", "proposal_id": proposal_pin.get("proposal_id"),
                            "proposer": proposal_pin.get("proposer"),
                            "kind": "explicit-scripted-owner-selection"}):
        raise ValueError("explicit owner selection for this exact proposal required")
    if current_pin.get("revision") != 1:
        raise ValueError("this fixture supports a single reviewed update")
    proposal = verify_proposal(proposal_folder, proposal_pin, current, current_pin)
    knowledge = copy.deepcopy(verify_snapshot(current, current_pin)["knowledge"])
    knowledge["entries"] = [proposal["replacement"]]
    return write_snapshot(folder, knowledge, 2, current_pin["snapshot_id"], dict(decision))


def publish(store, folder, root, pin, key_field):
    store.run("import", folder, "--root", root)
    pin[key_field] = store.roots()[root]


def export(store, root, archive):
    store.run("archive", "create", "--root", root, "--output", archive, "--json")
    return digest(archive.read_bytes())


def restore(store, archive, archive_sha, key, folder, prefix, extra_keys=()):
    checked_archive(archive, archive_sha)
    store.run("archive", "verify", archive, "--json")
    before = store.roots()
    store.run("archive", "import", archive, "--root-prefix", prefix, "--json")
    after = store.roots()
    expected_keys = {key, *extra_keys}
    if (len(after) != len(before) + len(expected_keys)
            or set(after.values()) != set(before.values()) | expected_keys
            or any(after.get(name) != value for name, value in before.items())):
        raise ValueError("imported roots differ from expected pinned graph")
    folder.parent.mkdir(parents=True, exist_ok=True)
    store.run("checkout", key, folder, "--no-root")


def run_demo(binary, destination, selection):
    if selection not in PEOPLE:
        raise ValueError("choose bob or carol explicitly for the synthetic owner decision")
    output = new_output(destination)
    commands = []
    receipt = {"schema": "casita-team-handoff.receipt.v1", "synthetic": True,
               "scripted_teammates": True, "fresh_ai_run": False,
               "model_internal_state_restored": False, "received_instructions_executed": False,
               "identity_authenticated": False, "access_control_tested": False,
               "execution_attested": False, "network_isolation_enforced": False,
               "concurrent_writes_tested": False, "commands": commands}
    try:
        stores = {name: Casita(binary, output / f"{name}-store", commands)
                  for name in ("alice", *PEOPLE, "owner-return")}
        for store in stores.values():
            store.run("init")
        receipt["casita_version"] = stores["alice"].run("--version").strip()
        receipt["casita_binary_sha256"] = digest(Path(binary).read_bytes())
        original = output / "original"
        parent_pin = initial_snapshot(original)
        original_hashes = file_map(original)
        publish(stores["alice"], original, "team/v1", parent_pin, "directory_key")
        archive = output / "handoff.casitar"
        archive_sha = export(stores["alice"], "team/v1", archive)
        # Expected IDs are provisioned separately by the trusted coordinator.
        # Hashes are integrity pins, not signatures or teammate authentication.
        (output / "input-pins.json").write_bytes(canonical(dict(parent_pin, archive_sha256=archive_sha)))
        shutil.rmtree(original)
        stores["alice"].repository.rename(output / "alice-store-unavailable")
        controls = {"wrong_archive_hash": rejected(lambda: checked_archive(archive, "0" * 64))}
        proposal_pins, proposal_archives = {}, {}
        for name in PEOPLE:
            parent = output / name / "context"
            restore(stores[name], archive, archive_sha, parent_pin["directory_key"], parent, "received")
            if file_map(parent) != original_hashes:
                raise ValueError("teammate snapshot differs from original")
            verify_snapshot(parent, parent_pin)
            proposal = output / name / "proposal"
            proposal_pins[name] = write_proposal(proposal, parent, parent_pin, name)
            publish(stores[name], proposal, f"proposal/{name}", proposal_pins[name], "directory_key")
            returned = output / f"{name}-return.casitar"
            proposal_archives[name] = {"path": returned, "sha256": export(stores[name], f"proposal/{name}", returned)}
        owner = stores["owner-return"]
        current = output / "owner/context-v1"
        restore(owner, archive, archive_sha, parent_pin["directory_key"], current, "parent")
        verified = {}
        for name in PEOPLE:
            data = proposal_archives[name]
            folder = output / "owner/proposals" / name
            restore(owner, data["path"], data["sha256"], proposal_pins[name]["directory_key"], folder, name)
            verified[name] = verify_proposal(folder, proposal_pins[name], current, parent_pin)
        if file_map(current) != original_hashes:
            raise ValueError("receiving proposals changed current knowledge")
        if verified["bob"]["replacement"] == verified["carol"]["replacement"]:
            raise ValueError("expected distinct competing proposals")
        selected_pin = proposal_pins[selection]
        selected_folder = output / "owner/proposals" / selection
        decision = {"owner": "alice", "proposal_id": selected_pin["proposal_id"],
                    "proposer": selection, "kind": "explicit-scripted-owner-selection"}
        controls["missing_owner_selection"] = rejected(lambda: accept_proposal(
            output / "unapproved", selected_folder, selected_pin, current, parent_pin, None))
        wrong_decision = dict(decision, proposal_id="0" * 64)
        controls["selection_for_other_proposal"] = rejected(lambda: accept_proposal(
            output / "wrong-selection", selected_folder, selected_pin, current, parent_pin, wrong_decision))
        next_snapshot = output / "owner/context-v2"
        next_pin = accept_proposal(next_snapshot, selected_folder, selected_pin, current, parent_pin, decision)
        publish(owner, next_snapshot, "team/v2", next_pin, "directory_key")
        verify_snapshot(next_snapshot, next_pin)
        selected_archive = output / "selected-knowledge.casitar"
        owner.run("root", "set", "team/selected-proposal", selected_pin["directory_key"])
        owner.run("archive", "create", "--root", "team/v2", "--root", "team/selected-proposal",
                  "--output", selected_archive, "--json")
        selected_archive_sha = digest(selected_archive.read_bytes())
        next_hashes = file_map(next_snapshot)
        for name in PEOPLE:
            shared = output / name / "selected-context"
            restore(stores[name], selected_archive, selected_archive_sha,
                    next_pin["directory_key"], shared, "selected", (selected_pin["directory_key"],))
            verify_snapshot(shared, next_pin)
            if file_map(shared) != next_hashes:
                raise ValueError("selected knowledge differs at teammate")
            shared_proposal = output / name / "accepted-proposal"
            stores[name].run("checkout", selected_pin["directory_key"], shared_proposal, "--no-root")
            verified_proposal = verify_proposal(shared_proposal, selected_pin,
                                               output / name / "context", parent_pin)
            if (verify_snapshot(shared, next_pin)["knowledge"]["entries"]
                    != [verified_proposal["replacement"]]):
                raise ValueError("shared knowledge differs from the accepted proposal")
        for name in PEOPLE:
            controls[f"stale_{name}_return"] = rejected(lambda name=name: verify_proposal(
                output / "owner/proposals" / name, proposal_pins[name], next_snapshot, next_pin))
        original_proposal = read_json(selected_folder / "proposal.json")
        mutations = {
            "wrong_parent_key": lambda p: p["parent"].update(directory_key="synthetic-wrong-key"),
            "wrong_base_knowledge": lambda p: p["parent"].update(knowledge_sha256="0" * 64),
            "wrong_task": lambda p: p.update(task_id="synthetic-other-task"),
            "invented_citation": lambda p: p["citation"].update(excerpt=["invented source"]),
            "outside_source": lambda p: p["citation"].update(path="../outside.txt"),
            "changed_owner": lambda p: p["replacement"].update(owner="bob"),
        }
        for label, change in mutations.items():
            proposal = copy.deepcopy(original_proposal)
            change(proposal)
            folder = output / "controls" / label
            folder.mkdir(parents=True)
            raw = canonical(proposal)
            (folder / "proposal.json").write_bytes(raw)
            changed_pin = dict(selected_pin, proposal_id=digest(raw))
            controls[label] = rejected(lambda: verify_proposal(folder, changed_pin, current, parent_pin))
        if not all(controls.values()):
            raise ValueError("negative control unexpectedly accepted")
        if (output / "unapproved").exists() or (output / "wrong-selection").exists():
            raise ValueError("unapproved snapshot was written")
        for name in (*PEOPLE, "owner-return"):
            stores[name].run("fsck", "--dry-run")
        receipt.update(ok=True, input_pin=parent_pin, input_archive_sha256=archive_sha,
                       proposal_pins=proposal_pins, original_file_hashes=original_hashes,
                       negative_controls=controls, selected_proposer=selection, decision=decision,
                       next_pin=next_pin, next_file_hashes=next_hashes,
                       selected_archive_sha256=selected_archive_sha,
                       selected_snapshot_shared_back=True,
                       accepted_proposal_shared_back=True,
                       proposals_changed_current_knowledge=False, competing_proposals_retained=True,
                       original_snapshot_removed=not original.exists(),
                       sender_path_unavailable=not stores["alice"].repository.exists(),
                       integrity_audits_passed=True)
        (output / "selected-pins.json").write_bytes(canonical(next_pin))
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        (output / "receipt.json").write_bytes(canonical(receipt))
    print("PASS: Bob and Carol restored the same task, source and knowledge in separate stores")
    print("PASS: both source-cited proposals verified; receiving advice changed no knowledge")
    print(f"PASS: explicit synthetic owner selection accepted {selection}; both proposals retained")
    print("PASS: both teammates restored selected knowledge and its exact accepted proposal")
    print("PASS: stale returns, missing selection, wrong bindings and invented citations rejected")
    print("PASS: receiver stores pass integrity audits; no model or received code executed")
    print(f"Selected knowledge: {next_snapshot / 'knowledge.json'}")
    print(f"Receipt: {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--output", required=True, type=Path, help="fresh output/<directory>")
    parser.add_argument("--accept", required=True, choices=PEOPLE,
                        help="explicit scripted owner choice; not an authenticated human approval")
    args = parser.parse_args()
    binary = shutil.which(args.casita)
    if not binary:
        parser.error("Casita executable not found; see README.md for the tested build")
    try:
        run_demo(str(Path(binary).resolve()), args.output, args.accept)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Teammate handoff failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
