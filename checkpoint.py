#!/usr/bin/env python3
"""Freeze visible synthetic task state, then verify two scripted continuations."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from artifacts import new_output, rejected
from demo import Casita, canonical, checked_archive, digest, file_map

TASK = "synthetic-day-plan"
BRANCHES = ("economy", "full-day")
FILES = {"inputs.json", "prompt.txt", "tool-responses.json", "notes.txt", "progress.json"}


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def recorded_response(inputs):
    # Synthetic response from a fixed local calculation, not an external tool.
    return {"schema": "casita-checkpoint-tool.v1", "synthetic": True,
            "tool": "scripted-budget-filter", "inputs_sha256": digest(canonical(inputs)),
            "eligible_ids": [choice["id"] for choice in inputs["choices"]
                             if choice["cost"] <= inputs["budget"]]}


def create_checkpoint(folder, revision):
    if revision not in ("v1", "v2"):
        raise ValueError("unknown synthetic checkpoint revision")
    folder.mkdir(parents=True)
    inputs = {"schema": "casita-checkpoint-inputs.v1", "synthetic": True,
              "budget": 90 if revision == "v1" else 75,
              "choices": [{"id": "park", "cost": 40, "minutes": 90},
                          {"id": "museum", "cost": 70, "minutes": 180},
                          {"id": "studio", "cost": 85, "minutes": 240}]}
    (folder / "inputs.json").write_bytes(canonical(inputs))
    (folder / "prompt.txt").write_bytes(
        b"SYNTHETIC TASK: draft economy and full-day options within the recorded budget.\n")
    (folder / "tool-responses.json").write_bytes(canonical(recorded_response(inputs)))
    (folder / "notes.txt").write_bytes(
        b"SYNTHETIC NOTES: budget inspected; option drafting and comparison remain pending.\n")
    progress = {"schema": "casita-checkpoint-progress.v1", "synthetic": True,
                "task_id": TASK, "revision": revision, "completed": ["inspect-budget"],
                "pending": ["draft-options", "compare-options"]}
    (folder / "progress.json").write_bytes(canonical(progress))
    manifest = {"schema": "casita-task-checkpoint.v1", "synthetic": True,
                "task_id": TASK, "revision": revision, "files": file_map(folder)}
    data = canonical(manifest)
    (folder / "manifest.json").write_bytes(data)
    return {"task_id": TASK, "revision": revision, "checkpoint_id": digest(data)}


def verify_checkpoint(folder, pin):
    if not sha(pin.get("checkpoint_id")) or pin.get("task_id") != TASK or pin.get("revision") not in ("v1", "v2"):
        raise ValueError("invalid expected checkpoint pin")
    actual = file_map(folder)
    if set(actual) != FILES | {"manifest.json"}:
        raise ValueError("checkpoint file set mismatch")
    if actual.pop("manifest.json") != pin["checkpoint_id"]:
        raise ValueError("checkpoint pin mismatch")
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema", "synthetic", "task_id", "revision", "files"}
            or manifest["schema"] != "casita-task-checkpoint.v1" or manifest["synthetic"] is not True
            or manifest["task_id"] != pin["task_id"] or manifest["revision"] != pin["revision"]):
        raise ValueError("checkpoint task or revision binding mismatch")
    if manifest["files"] != actual:
        raise ValueError("checkpoint file hashes mismatch")
    inputs = json.loads((folder / "inputs.json").read_bytes())
    if (not isinstance(inputs, dict) or set(inputs) != {"schema", "synthetic", "budget", "choices"}
            or inputs["schema"] != "casita-checkpoint-inputs.v1" or inputs["synthetic"] is not True
            or type(inputs["budget"]) is not int or not 0 < inputs["budget"] <= 200
            or not isinstance(inputs["choices"], list) or len(inputs["choices"]) != 3):
        raise ValueError("invalid synthetic task inputs")
    ids = []
    for choice in inputs["choices"]:
        if (not isinstance(choice, dict) or set(choice) != {"id", "cost", "minutes"}
                or choice["id"] not in ("park", "museum", "studio")
                or any(type(choice[field]) is not int or not 0 < choice[field] <= 500
                       for field in ("cost", "minutes"))):
            raise ValueError("invalid synthetic choice")
        ids.append(choice["id"])
    if len(set(ids)) != 3:
        raise ValueError("duplicate synthetic choice")
    if canonical(json.loads((folder / "tool-responses.json").read_bytes())) != canonical(recorded_response(inputs)):
        raise ValueError("recorded response does not match the synthetic inputs")
    progress = json.loads((folder / "progress.json").read_bytes())
    expected_progress = {"schema": "casita-checkpoint-progress.v1", "synthetic": True,
                         "task_id": TASK, "revision": pin["revision"],
                         "completed": ["inspect-budget"],
                         "pending": ["draft-options", "compare-options"]}
    if canonical(progress) != canonical(expected_progress):
        raise ValueError("checkpoint progress binding mismatch")
    return inputs


def scripted_continuation(folder, pin, branch):
    # Read validated data with fixed local policy. Never evaluate received prompts,
    # notes, tool names, or code. This is not a live AI agent or a model replay.
    if branch not in BRANCHES:
        raise ValueError("unknown continuation branch")
    if not isinstance(pin.get("directory_key"), str) or not pin["directory_key"]:
        raise ValueError("missing expected parent directory key")
    inputs = verify_checkpoint(folder, pin)
    eligible = [choice for choice in inputs["choices"] if choice["cost"] <= inputs["budget"]]
    if not eligible:
        raise ValueError("no eligible synthetic options")
    choice = (min(eligible, key=lambda c: (c["cost"], c["id"])) if branch == "economy"
              else max(eligible, key=lambda c: (c["minutes"], c["id"])))
    return {"schema": "casita-checkpoint-continuation.v1", "synthetic": True,
            "scripted": True, "task_id": TASK, "branch": branch,
            "parent_checkpoint_id": pin["checkpoint_id"],
            "parent_directory_key": pin["directory_key"],
            "inputs_sha256": digest((folder / "inputs.json").read_bytes()),
            "choice": choice, "completed": ["inspect-budget", "draft-options"],
            "pending": ["compare-options"]}


def write_continuation(folder, parent, pin, branch):
    result = scripted_continuation(parent, pin, branch)
    folder.mkdir(parents=True)
    data = canonical(result)
    (folder / "result.json").write_bytes(data)
    return {"branch": branch, "result_id": digest(data)}


def verify_continuation(folder, result_pin, parent, parent_pin, branch):
    if branch not in BRANCHES or result_pin.get("branch") != branch or not sha(result_pin.get("result_id")):
        raise ValueError("invalid expected continuation pin or branch")
    if file_map(folder) != {"result.json": result_pin["result_id"]}:
        raise ValueError("continuation file set or pin mismatch")
    expected = canonical(scripted_continuation(parent, parent_pin, branch))
    if (folder / "result.json").read_bytes() != expected:
        raise ValueError("continuation differs from the expected parent, branch or local policy")


def run_demo(binary, destination):
    output = new_output(destination)
    commands = []
    receipt = {"schema": "casita-checkpoint.receipt.v1", "commands": commands,
               "synthetic": True, "scripted_continuations": True, "fresh_ai_run": False,
               "model_internal_state_restored": False, "received_instructions_executed": False,
               "execution_attested": False, "network_isolation_enforced": False}
    try:
        sender = Casita(binary, output / "sender-store", commands)
        worker = Casita(binary, output / "worker-store", commands)
        returned = Casita(binary, output / "returned-store", commands)
        receipt["casita_version"] = sender.run("--version").strip()
        receipt["casita_binary_sha256"] = digest(Path(binary).read_bytes())
        sender.run("init")
        pins, originals = {}, {}
        for revision in ("v1", "v2"):
            folder = output / "checkpoints" / revision
            pins[revision] = create_checkpoint(folder, revision)
            verify_checkpoint(folder, pins[revision])
            originals[revision] = file_map(folder)
            sender.run("import", folder, "--root", f"checkpoint/{revision}")
            pins[revision]["directory_key"] = sender.roots()[f"checkpoint/{revision}"]
        archive = output / "checkpoints.casitar"
        sender.run("archive", "create", "--root", "checkpoint/v1", "--root", "checkpoint/v2",
                   "--output", archive, "--json")
        pins["archive_sha256"] = digest(archive.read_bytes())
        (output / "pins.json").write_bytes(canonical(pins))
        expected = json.loads((output / "pins.json").read_bytes())
        controls = {"wrong_archive_pin": rejected(lambda: checked_archive(archive, "0" * 64))}
        checked_archive(archive, expected["archive_sha256"])
        sender.run("archive", "verify", archive, "--json")
        shutil.rmtree(output / "checkpoints")
        sender.repository.rename(output / "sender-store-unavailable")
        receipt["worker_command_start"] = len(commands)
        worker.run("init")
        worker.run("archive", "import", archive, "--root-prefix", "received", "--json")
        roots = worker.roots()
        if len(roots) != 2 or set(roots.values()) != {expected[v]["directory_key"] for v in ("v1", "v2")}:
            raise ValueError("received checkpoint roots differ from trusted pins")
        for revision in ("v1", "v2"):
            folder = output / "restored" / revision
            folder.parent.mkdir(exist_ok=True)
            worker.run("checkout", expected[revision]["directory_key"], folder, "--no-root")
            verify_checkpoint(folder, expected[revision])
            if file_map(folder) != originals[revision]:
                raise ValueError("restored visible checkpoint differs from original")
        parent, parent_pin = output / "restored/v2", expected["v2"]
        result_pins = {}
        for branch in BRANCHES:
            folder = output / "continuations" / branch
            result_pins[branch] = write_continuation(folder, parent, parent_pin, branch)
            verify_continuation(folder, result_pins[branch], parent, parent_pin, branch)
            worker.run("import", folder, "--root", f"continuation/{branch}")
            result_pins[branch]["directory_key"] = worker.roots()[f"continuation/{branch}"]
        return_archive = output / "continuations.casitar"
        worker.run("root", "set", "checkpoint/parent", parent_pin["directory_key"])
        worker.run("archive", "create", "--root", "checkpoint/parent",
                   "--root", "continuation/economy", "--root", "continuation/full-day",
                   "--output", return_archive, "--json")
        result_pins["archive_sha256"] = digest(return_archive.read_bytes())
        (output / "result-pins.json").write_bytes(canonical(result_pins))
        checked_archive(return_archive, result_pins["archive_sha256"])
        worker.run("archive", "verify", return_archive, "--json")
        returned.run("init")
        returned.run("archive", "import", return_archive, "--root-prefix", "returned", "--json")
        roots = returned.roots()
        if len(roots) != 3 or set(roots.values()) != {parent_pin["directory_key"],
                                                   *[result_pins[b]["directory_key"] for b in BRANCHES]}:
            raise ValueError("returned roots differ from expected parent and result pins")
        verified_parent = output / "verified-parent"
        returned.run("checkout", parent_pin["directory_key"], verified_parent, "--no-root")
        verify_checkpoint(verified_parent, parent_pin)
        choices = {}
        for branch in BRANCHES:
            folder = output / "verified" / branch
            folder.parent.mkdir(exist_ok=True)
            returned.run("checkout", result_pins[branch]["directory_key"], folder, "--no-root")
            verify_continuation(folder, result_pins[branch], verified_parent, parent_pin, branch)
            choices[branch] = json.loads((folder / "result.json").read_bytes())["choice"]["id"]
        if choices != {"economy": "park", "full-day": "museum"}:
            raise ValueError("unexpected synthetic branch choices")

        for label, name, data in (
            ("mixed_progress", "progress.json", (output / "restored/v1/progress.json").read_bytes()),
            ("altered_prompt", "prompt.txt", b"SYNTHETIC altered prompt\n"),
        ):
            folder = output / label
            shutil.copytree(verified_parent, folder)
            (folder / name).write_bytes(data)
            controls[label] = rejected(lambda: verify_checkpoint(folder, parent_pin))
        missing = output / "missing-response"
        shutil.copytree(verified_parent, missing)
        (missing / "tool-responses.json").unlink()
        controls["missing_tool_response"] = rejected(lambda: verify_checkpoint(missing, parent_pin))
        stale = output / "stale-result"
        stale_pin = write_continuation(stale, output / "restored/v1", expected["v1"], "full-day")
        controls["stale_parent_result"] = rejected(lambda: verify_continuation(
            stale, stale_pin, verified_parent, parent_pin, "full-day"))
        economy = output / "verified/economy"
        controls["swapped_branch"] = rejected(lambda: verify_continuation(
            economy, dict(result_pins["economy"], branch="full-day"), verified_parent, parent_pin, "full-day"))
        for field, value in (("parent_checkpoint_id", expected["v1"]["checkpoint_id"]),
                             ("parent_directory_key", expected["v1"]["directory_key"]),
                             ("choice", {"id": "studio", "cost": 85, "minutes": 240})):
            folder = output / f"rebound-{field}"
            folder.mkdir()
            result = json.loads((economy / "result.json").read_bytes())
            result[field] = value
            data = canonical(result)
            (folder / "result.json").write_bytes(data)
            pin = dict(result_pins["economy"], result_id=digest(data))
            controls[f"rebound_{field}"] = rejected(lambda: verify_continuation(
                folder, pin, verified_parent, parent_pin, "economy"))
        worker.run("fsck", "--dry-run")
        returned.run("fsck", "--dry-run")
        receipt.update(ok=True, pins=expected, result_pins=result_pins,
                       original_file_hashes=originals, branch_choices=choices,
                       negative_controls=controls, visible_checkpoint_restored=True,
                       sender_path_unavailable=not sender.repository.exists(),
                       original_checkpoints_removed=not (output / "checkpoints").exists(),
                       returned_parent_and_results_verified=True, integrity_audits_passed=True)
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        (output / "receipt.json").write_bytes(canonical(receipt))
    print("PASS: visible inputs, prompt, recorded response, notes and pending tasks frozen")
    print("PASS: both checkpoint versions restored exactly with original sender path unavailable")
    print("PASS: two scripted continuations share the v2 parent and choose different options")
    print("PASS: returned parent and both results verified in a fresh store")
    print("PASS: wrong archive pin, mixed progress, altered prompt and missing response rejected")
    print("PASS: stale parent, swapped branch and repinned parent or choice changes rejected")
    print("PASS: worker and return stores pass integrity audits; no live model or received instructions executed")
    print(f"Verified continuations: {output / 'verified'}")
    print(f"Receipt: {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita", help="tested Casita executable or PATH name")
    parser.add_argument("--output", required=True, type=Path, help="fresh output/<directory>")
    args = parser.parse_args()
    binary = shutil.which(args.casita)
    if not binary:
        parser.error("Casita executable not found; see README.md for the tested build")
    try:
        run_demo(str(Path(binary).resolve()), args.output)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Checkpoint demo failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
