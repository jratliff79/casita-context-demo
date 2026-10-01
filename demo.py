#!/usr/bin/env python3
"""Store and hand off two synthetic investigation contexts using real Casita."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from fixtures.source.verify import verify as trusted_verify

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MAX_BYTES = 1_000_000


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_map(folder):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("context must be a regular directory")
    files = {}
    total = 0
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("context contains a link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("context contains a special file")
        size = path.stat().st_size
        total += size
        if total > MAX_BYTES:
            raise ValueError("demo context exceeds its byte limit")
        data = path.read_bytes()
        if len(data) != size:
            raise ValueError("context changed while reading")
        files[path.relative_to(folder).as_posix()] = digest(data)
    return files


def create_context(folder, version):
    if version not in ("v1", "v2"):
        raise ValueError("unknown fixture version")
    selected = {
        "source/verify.py": FIXTURES / "source" / "verify.py",
        "observation.json": FIXTURES / version / "observation.json",
        "task.md": FIXTURES / version / "task.md",
    }
    for source in selected.values():
        ancestors = [FIXTURES / parent for parent in source.relative_to(FIXTURES).parents]
        if source.is_symlink() or any(parent.is_symlink() for parent in ancestors) or not source.is_file():
            raise ValueError("fixture input must be a regular file without links")
    folder.mkdir(parents=True)
    (folder / "source").mkdir()
    for name, source in selected.items():
        shutil.copyfile(source, folder / name)
    manifest = {
        "schema": "casita-context-demo.v1",
        "version": version,
        "scope": "synthetic fixture; execution unattested",
        "files": file_map(folder),
    }
    encoded = canonical(manifest)
    (folder / "manifest.json").write_bytes(encoded)
    return digest(encoded)


def verify_context(folder, expected_id):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_id):
        raise ValueError("expected context ID must be a SHA-256 digest")
    actual = file_map(folder)
    if "manifest.json" not in actual:
        raise ValueError("missing manifest")
    if actual.pop("manifest.json") != expected_id:
        raise ValueError("context ID mismatch")
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if manifest.get("schema") != "casita-context-demo.v1" or actual != manifest.get("files"):
        raise ValueError("context file set or hashes do not match")
    return manifest


class Casita:
    def __init__(self, binary, repository, commands):
        self.binary, self.repository, self.commands = binary, repository, commands

    def run(self, *args):
        argv = [self.binary, "--repository", str(self.repository), *map(str, args)]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        self.commands.append({"argv": argv, "exit_code": result.returncode,
                              "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError(f"Casita {' '.join(map(str, args))} failed: {result.stderr}")
        return result.stdout

    def roots(self):
        return {parts[1]: parts[0] for line in self.run("root", "ls").splitlines()
                if len(parts := line.split()) == 2}

    def entries(self, key):
        return {parts[-2]: parts[-1] for line in self.run("tree", "list", key).splitlines()
                if len(parts := line.split()) >= 3}


def checked_archive(archive, expected_sha):
    if digest(archive.read_bytes()) != expected_sha:
        raise ValueError("archive SHA-256 mismatch")


def expected_result(context, context_id, directory_key):
    """Use the repository's trusted checker; received source is data only."""
    verify_context(context, context_id)
    checker_sha = digest((FIXTURES / "source/verify.py").read_bytes())
    if digest((context / "source/verify.py").read_bytes()) != checker_sha:
        raise ValueError("received checker differs from trusted local checker")
    observation = json.loads((context / "observation.json").read_bytes())
    return {
        "schema": "casita-context-demo.result.v1",
        "scope": "synthetic deterministic check; host execution unattested",
        "context_id": context_id,
        "context_directory_key": directory_key,
        "checker_sha256": checker_sha,
        "observation_sha256": digest((context / "observation.json").read_bytes()),
        "verdict": trusted_verify(observation["source_packets"], observation["output_audio_duration"]),
    }


def verify_result(folder, result_id, context, context_id, directory_key):
    if not re.fullmatch(r"[0-9a-f]{64}", result_id):
        raise ValueError("expected result ID must be a SHA-256 digest")
    actual = file_map(folder)
    if actual != {"result.json": result_id}:
        raise ValueError("result file set or pin mismatch")
    expected = canonical(expected_result(context, context_id, directory_key))
    if (folder / "result.json").read_bytes() != expected:
        raise ValueError("result does not match original context and trusted checker")


def return_results(binary, output, receiver, pins, commands):
    returned = Casita(binary, output / "returned-store", commands)
    returned.run("init")
    result_pins = {}
    verdicts = {}
    for version in ("v1", "v2"):
        pin = pins[version]
        result = expected_result(output / "received" / version, pin["context_id"], pin["directory_key"])
        folder = output / "results" / version
        folder.mkdir(parents=True)
        data = canonical(result)
        (folder / "result.json").write_bytes(data)
        receiver.run("import", folder, "--root", f"result/{version}")
        result_pins[version] = {"result_id": digest(data),
                                "directory_key": receiver.roots()[f"result/{version}"]}
        verdicts[version] = result["verdict"]
    archive = output / "results.casitar"
    creation = json.loads(receiver.run("archive", "create", "--root", "result/v1",
                                      "--root", "result/v2", "--output", archive, "--json"))
    result_pins["archive_sha256"] = digest(archive.read_bytes())
    (output / "result-pins.json").write_bytes(canonical(result_pins))
    expected = json.loads((output / "result-pins.json").read_bytes())
    checked_archive(archive, expected["archive_sha256"])
    verification = json.loads(receiver.run("archive", "verify", archive, "--json"))
    imported = json.loads(returned.run("archive", "import", archive, "--root-prefix", "returned", "--json"))
    roots = returned.roots()
    if len(roots) != 2 or set(roots.values()) != {expected[v]["directory_key"] for v in ("v1", "v2")}:
        raise ValueError("returned roots do not match expected result keys")
    for version in ("v1", "v2"):
        folder = output / "returned" / version
        folder.parent.mkdir(exist_ok=True)
        returned.run("checkout", expected[version]["directory_key"], folder, "--no-root")
        # Validate against original sender evidence, not a receiver's claim.
        verify_result(folder, expected[version]["result_id"], output / "contexts" / version,
                      pins[version]["context_id"], pins[version]["directory_key"])
    altered = output / "tampered-result"
    altered.mkdir()
    data = json.loads((output / "returned/v1/result.json").read_bytes())
    data["verdict"] = "accepted"
    altered_bytes = canonical(data)
    (altered / "result.json").write_bytes(altered_bytes)
    controls = {}
    for label, result_id, context_version in (
        ("altered_result_rejected", expected["v1"]["result_id"], "v1"),
        ("rebound_result_rejected", digest(altered_bytes), "v1"),
    ):
        try:
            verify_result(altered, result_id, output / "contexts" / context_version,
                          pins[context_version]["context_id"], pins[context_version]["directory_key"])
        except ValueError:
            controls[label] = True
        else:
            raise ValueError("altered result was accepted")
    try:
        verify_result(output / "returned/v1", expected["v1"]["result_id"], output / "contexts/v2",
                      pins["v2"]["context_id"], pins["v2"]["directory_key"])
    except ValueError:
        controls["result_for_other_context_rejected"] = True
    else:
        raise ValueError("result for a different context was accepted")
    receiver.run("fsck", "--dry-run")
    returned.run("fsck", "--dry-run")
    return {"pins": expected, "verdicts": verdicts, "archive_bytes": archive.stat().st_size,
            "archive_create": creation, "archive_verification": verification, "archive_import": imported,
            "negative_controls": controls, "execution": "trusted local Python function",
            "received_code_executed": False, "sandbox_tested": False, "execution_attested": False}


def run_demo(binary, output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commands = []
    receipt = {"schema": "casita-context-demo.receipt.v1", "commands": commands}
    try:
        sender = Casita(binary, output / "sender-store", commands)
        receiver = Casita(binary, output / "receiver-store", commands)
        receipt["casita_version"] = sender.run("--version").strip()
        receipt["casita_binary_sha256"] = digest(Path(binary).read_bytes())
        sender.run("init")
        pins = {}
        for version in ("v1", "v2"):
            context = output / "contexts" / version
            context_id = create_context(context, version)
            sender.run("import", context, "--root", f"demo/{version}")
            key = sender.roots()[f"demo/{version}"]
            pins[version] = {"context_id": context_id, "directory_key": key}
            sender.run("root", "set", "demo/current", key)
        if pins["v1"]["directory_key"] == pins["v2"]["directory_key"]:
            raise ValueError("changed context unexpectedly has the same directory key")
        first = sender.entries(pins["v1"]["directory_key"])
        second = sender.entries(pins["v2"]["directory_key"])
        if first["source"] != second["source"] or first["observation.json"] == second["observation.json"]:
            raise ValueError("expected shared source and distinct observations")
        receipt["shared_source_directory_key"] = first["source"]
        receipt["sender_roots"] = sender.roots()
        archive = output / "handoff.casitar"
        creation = json.loads(sender.run("archive", "create", "--root", "demo/v1",
                                         "--root", "demo/v2", "--output", archive, "--json"))
        if [x["name"] for x in creation["named_roots"]] != ["demo/v1", "demo/v2"]:
            raise ValueError("unexpected archive root ordering")
        pins["archive_sha256"] = digest(archive.read_bytes())
        (output / "pins.json").write_bytes(canonical(pins))
        # In a real handoff, obtain these expected pins independently of the archive.
        expected = json.loads((output / "pins.json").read_bytes())
        checked_archive(archive, expected["archive_sha256"])
        receipt["archive_verification"] = json.loads(sender.run("archive", "verify", archive, "--json"))
        receiver.run("init")
        receipt["archive_import"] = json.loads(receiver.run("archive", "import", archive,
                                                            "--root-prefix", "received", "--json"))
        roots = receiver.roots()
        expected_keys = {expected[version]["directory_key"] for version in ("v1", "v2")}
        if len(roots) != 2 or set(roots.values()) != expected_keys:
            raise ValueError("receiver roots do not match expected directory keys")
        # Import indices follow archive ordering, not the requested version order.
        for version in ("v1", "v2"):
            key = expected[version]["directory_key"]
            restored = output / "received" / version
            restored.parent.mkdir(parents=True, exist_ok=True)
            receiver.run("checkout", key, restored, "--no-root")
            verify_context(restored, expected[version]["context_id"])
            if file_map(restored) != file_map(output / "contexts" / version):
                raise ValueError("restored context differs from sender")
        sender.run("fsck", "--dry-run")
        receiver.run("fsck", "--dry-run")
        # Alter only an expendable copy, never the verified receiver evidence.
        altered = output / "tampered-copy"
        shutil.copytree(output / "received/v1", altered)
        with (altered / "observation.json").open("a") as stream:
            stream.write("\nchanged after handoff\n")
        try:
            verify_context(altered, expected["v1"]["context_id"])
        except ValueError:
            receipt["tampered_context_rejected"] = True
        else:
            raise ValueError("altered evidence was accepted")
        receipt["result_return"] = return_results(binary, output, receiver, expected, commands)
        receipt.update({"ok": True, "pins": expected, "archive_bytes": archive.stat().st_size,
                        "scope": "local synthetic context/result transport and trusted deterministic check; no AI or media execution",
                        "next": "Read received/v1/task.md, source/verify.py and observation.json as evidence."})
    except Exception as error:
        receipt.update({"ok": False, "error": str(error)})
        raise
    finally:
        (output / "receipt.json").write_bytes(canonical(receipt))
    print("PASS: two versions saved; unchanged source shares one Casita identity")
    print("PASS: Casitar verified and restored in a fresh receiver store")
    print("PASS: pinned directory keys and context file hashes match")
    print("PASS: altered evidence rejected; both stores pass integrity audit")
    print("PASS: trusted local check results returned to a fresh store and matched original contexts")
    print("PASS: altered, rebound and wrong-context results rejected; result stores pass integrity audit")
    print(f"Receiver task: {output / 'received/v1/task.md'}")
    print(f"Receipt: {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita", help="Casita executable or PATH name")
    parser.add_argument("--output", type=Path, default=Path("output/demo"), help="new output directory")
    args = parser.parse_args()
    binary = shutil.which(args.casita)
    if not binary:
        parser.error("Casita executable not found; see README.md for the tested build")
    try:
        run_demo(str(Path(binary).resolve()), args.output)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
