#!/usr/bin/env python3
"""Separate sender, worker and return-verifier processes for synthetic contexts."""
import argparse
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

import demo

VERSIONS = ("v1", "v2")
MAX_TRANSFER_BYTES = 1_000_000


def read_pins(path, role):
    if path.is_symlink() or not path.is_file():
        raise ValueError("pin input must be a regular file without links")
    with path.open("rb") as stream:
        raw = stream.read(10_001)
    if len(raw) > 10_000:
        raise ValueError("pin file exceeds limit")
    pins = json.loads(raw)
    if not isinstance(pins, dict) or set(pins) != {"schema", "archive_sha256", *VERSIONS} or pins["schema"] != f"casita-context-demo.{role}-pins.v1":
        raise ValueError("unexpected pin schema")
    if not isinstance(pins["archive_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", pins["archive_sha256"]):
        raise ValueError("invalid archive pin")
    identifier = "context_id" if role == "input" else "result_id"
    for version in VERSIONS:
        pin = pins[version]
        if not isinstance(pin, dict) or set(pin) != {identifier, "directory_key"}:
            raise ValueError("unexpected version pin fields")
        if not isinstance(pin[identifier], str) or not re.fullmatch(r"[0-9a-f]{64}", pin[identifier]):
            raise ValueError("invalid content pin")
        if not isinstance(pin["directory_key"], str) or not re.fullmatch(r"casita\.directory\.v1:[A-Za-z0-9_-]{43}", pin["directory_key"]):
            raise ValueError("invalid directory key")
    return pins


def export(store, output, prefix, pins, role, receipt):
    archive = output / "handoff.casitar"
    receipt["archive_create"] = json.loads(store.run("archive", "create", "--root", f"{prefix}/v1",
        "--root", f"{prefix}/v2", "--output", archive, "--json"))
    pins.update(schema=f"casita-context-demo.{role}-pins.v1", archive_sha256=demo.digest(archive.read_bytes()))
    (output / "pins.json").write_bytes(demo.canonical(pins))
    receipt["pins"] = pins
    receipt["archive_bytes"] = archive.stat().st_size
    receipt["archive_verify"] = json.loads(store.run("archive", "verify", archive, "--json"))


def receive(store, archive, pins, output, receipt):
    # Pins must arrive through a trusted channel outside the archive.
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("archive input must be a regular file without links")
    if archive.stat().st_size > MAX_TRANSFER_BYTES:
        raise ValueError("archive exceeds demo transfer limit")
    demo.checked_archive(archive, pins["archive_sha256"])
    store.run("init")
    receipt["archive_import"] = json.loads(store.run("archive", "import", archive,
        "--root-prefix", "received", "--max-archive-bytes", "1000000", "--json"))
    roots = store.roots()
    if len(roots) != 2 or set(roots.values()) != {pins[v]["directory_key"] for v in VERSIONS}:
        raise ValueError("received roots do not match expected pins")
    for version in VERSIONS:
        folder = output / "received" / version
        folder.parent.mkdir(exist_ok=True)
        store.run("checkout", pins[version]["directory_key"], folder, "--no-root")


def prepare(store, output, receipt):
    store.run("init")
    pins = {}
    for version in VERSIONS:
        folder = output / "contexts" / version
        context_id = demo.create_context(folder, version)
        store.run("import", folder, "--root", f"input/{version}")
        pins[version] = {"context_id": context_id, "directory_key": store.roots()[f"input/{version}"]}
    export(store, output, "input", pins, "input", receipt)


def work(store, args, output, receipt):
    pins = read_pins(args.pins, "input")
    receive(store, args.archive, pins, output, receipt)
    results = {}
    for version in VERSIONS:
        result = demo.expected_result(output / "received" / version,
            pins[version]["context_id"], pins[version]["directory_key"])
        folder = output / "results" / version
        folder.mkdir(parents=True)
        raw = demo.canonical(result)
        (folder / "result.json").write_bytes(raw)
        store.run("import", folder, "--root", f"result/{version}")
        results[version] = {"result_id": demo.digest(raw), "directory_key": store.roots()[f"result/{version}"]}
        receipt.setdefault("verdicts", {})[version] = result["verdict"]
    export(store, output, "result", results, "result", receipt)
    receipt.update(received_code_executed=False, execution_attested=False, sandbox_tested=False)


def verify_return(store, args, output, receipt):
    original = read_pins(args.original / "pins.json", "input")
    results = read_pins(args.pins, "result")
    receive(store, args.archive, results, output, receipt)
    for version in VERSIONS:
        demo.verify_result(output / "received" / version, results[version]["result_id"],
            args.original / "contexts" / version, original[version]["context_id"], original[version]["directory_key"])
    receipt["original_contexts_verified"] = True


def run(args):
    binary = shutil.which(args.casita)
    if not binary:
        raise ValueError("Casita executable not found")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "casita-context-demo.worker-receipt.v1", "mode": args.mode,
               "scope": "synthetic context/result transport; execution unattested",
               "platform": platform.system(), "architecture": platform.machine(),
               "python_version": platform.python_version(), "commands": []}
    try:
        binary = str(Path(binary).resolve())
        store = demo.Casita(binary, output / "store", receipt["commands"])
        receipt["casita_version"] = store.run("--version").strip()
        receipt["casita_binary_sha256"] = demo.digest(Path(binary).read_bytes())
        if args.mode == "prepare":
            prepare(store, output, receipt)
        elif args.mode == "work":
            work(store, args, output, receipt)
        else:
            verify_return(store, args, output, receipt)
        store.run("fsck", "--dry-run")
        receipt["ok"] = True
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print(f"PASS: {args.mode}; receipt in {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "work", "verify"))
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--pins", type=Path)
    parser.add_argument("--original", type=Path, help="trusted sender output for return verification")
    args = parser.parse_args()
    if args.mode != "prepare" and (args.archive is None or args.pins is None):
        parser.error("work and verify require --archive and --pins")
    if args.mode == "verify" and args.original is None:
        parser.error("verify requires --original")
    try:
        run(args)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Handoff failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
