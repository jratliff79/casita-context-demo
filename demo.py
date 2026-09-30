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
        for i, version in enumerate(("v1", "v2")):
            key = roots[f"received/{i}"]
            if key != expected[version]["directory_key"]:
                raise ValueError("receiver root does not match expected directory key")
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
        receipt.update({"ok": True, "pins": expected, "archive_bytes": archive.stat().st_size,
                        "scope": "local synthetic packaging/restore; no AI or media execution",
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
