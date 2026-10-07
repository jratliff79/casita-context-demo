#!/usr/bin/env python3
"""Build two synthetic sites locally, then restore exact artifacts without rebuilding."""
import argparse
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from demo import Casita, canonical, checked_archive, digest, file_map

TRUSTED_BUILDER = Path(__file__).resolve().parent / "fixtures/artifacts/build.py"
OUTPUT_FILES = {"outputs/index.html", "outputs/style.css"}
SCHEMA = "casita-artifact-demo.bundle.v1"


def new_output(path):
    path = Path(path)
    if len(path.parts) != 2 or path.parts[0] != "output" or path.parts[1] in (".", ".."):
        raise ValueError("choose a fresh relative output/<directory> from the demo root")
    parent = Path("output")
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ValueError("output must be a directory, not a link")
    parent.mkdir(exist_ok=True)
    path.mkdir()  # Never reuse an existing directory, file or symlink.
    return path.resolve()


def load_builder():
    # Execute exactly the trusted local bytes whose digest goes in the manifest.
    if TRUSTED_BUILDER.is_symlink() or TRUSTED_BUILDER.parent.is_symlink():
        raise ValueError("trusted local builder must not be linked")
    data = TRUSTED_BUILDER.read_bytes()
    namespace = {"__name__": "casita_demo_trusted_artifact_builder"}
    exec(compile(data, "trusted synthetic artifact builder", "exec"), namespace)
    return namespace["render"], digest(data)


def toolchain():
    return {"implementation": sys.implementation.name,
            "python": platform.python_version(),
            "system": platform.system(), "machine": platform.machine(),
            "dependencies": "Python standard library only"}


def create_source(folder, version):
    if version not in ("v1", "v2"):
        raise ValueError("unknown synthetic version")
    folder.mkdir(parents=True)
    page = {"title": "Synthetic event", "body": {
        "v1": "Doors open at 18:00.", "v2": "Doors open at 18:30."}[version]}
    (folder / "page.json").write_bytes(canonical(page))
    (folder / "style.css").write_bytes(b"body { font-family: sans-serif; color: #234; }\n")


def build_bundle(source, folder, version, render, recipe_sha):
    source_files = file_map(source)
    if set(source_files) != {"page.json", "style.css"}:
        raise ValueError("unexpected synthetic source file set")
    inputs = {"source_files": source_files, "recipe_sha256": recipe_sha,
              "toolchain": toolchain()}
    captured = {name: (source / name).read_bytes() for name in source_files}
    if {name: digest(data) for name, data in captured.items()} != source_files:
        raise ValueError("synthetic source changed while capturing build inputs")
    outputs = render(captured["page.json"], captured["style.css"])
    if set(outputs) != {"index.html", "style.css"}:
        raise ValueError("unexpected trusted builder output file set")
    (folder / "outputs").mkdir(parents=True)
    for name, data in outputs.items():
        (folder / "outputs" / name).write_bytes(data)
    manifest = {"schema": SCHEMA, "synthetic": True, "version": version,
                "build_inputs": inputs, "build_id": digest(canonical(inputs)),
                "output_files": file_map(folder)}
    data = canonical(manifest)
    (folder / "manifest.json").write_bytes(data)
    return {"version": version, "build_id": manifest["build_id"], "manifest_id": digest(data)}


def verify_bundle(folder, pin):
    for field in ("manifest_id", "build_id"):
        if not isinstance(pin.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", pin[field]):
            raise ValueError("expected build and manifest IDs must be SHA-256 digests")
    actual = file_map(folder)
    if set(actual) != OUTPUT_FILES | {"manifest.json"}:
        raise ValueError("artifact file set does not match the synthetic bundle")
    if actual.pop("manifest.json") != pin["manifest_id"]:
        raise ValueError("artifact manifest pin mismatch")
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema", "synthetic", "version", "build_inputs", "build_id", "output_files"}
            or manifest["schema"] != SCHEMA or manifest["synthetic"] is not True):
        raise ValueError("unexpected artifact manifest schema")
    if manifest["version"] != pin.get("version"):
        raise ValueError("artifact version mismatch")
    inputs = manifest["build_inputs"]
    if (not isinstance(inputs, dict) or set(inputs) != {"source_files", "recipe_sha256", "toolchain"}
            or not isinstance(inputs["source_files"], dict)
            or set(inputs["source_files"]) != {"page.json", "style.css"}
            or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in [*inputs["source_files"].values(), inputs["recipe_sha256"]])
            or not isinstance(inputs["toolchain"], dict)
            or set(inputs["toolchain"]) != {"implementation", "python", "system", "machine", "dependencies"}
            or not all(isinstance(value, str) for value in inputs["toolchain"].values())):
        raise ValueError("unexpected build input schema")
    if manifest["build_id"] != pin["build_id"] or digest(canonical(manifest["build_inputs"])) != pin["build_id"]:
        raise ValueError("artifact build input binding mismatch")
    if actual != manifest["output_files"]:
        raise ValueError("artifact output hashes mismatch")
    return manifest


def rejected(action):
    try:
        action()
    except ValueError:
        return True
    raise ValueError("negative control unexpectedly accepted")


def run_demo(binary, destination):
    output = new_output(destination)
    commands = []
    receipt = {"schema": "casita-artifact-demo.receipt.v1", "commands": commands,
               "synthetic": True, "receiver_rebuilt": False, "received_code_executed": False,
               "execution_attested": False, "build_provenance_attested": False}
    try:
        sender = Casita(binary, output / "sender-store", commands)
        receiver = Casita(binary, output / "receiver-store", commands)
        receipt["casita_version"] = sender.run("--version").strip()
        receipt["casita_binary_sha256"] = digest(Path(binary).read_bytes())
        sender.run("init")
        render, recipe_sha = load_builder()
        pins, original_files = {}, {}
        for version in ("v1", "v2"):
            source, bundle = output / "sources" / version, output / "builds" / version
            create_source(source, version)
            pins[version] = build_bundle(source, bundle, version, render, recipe_sha)
            verify_bundle(bundle, pins[version])
            original_files[version] = file_map(bundle)
            sender.run("import", bundle, "--root", f"artifacts/{version}")
            pins[version]["directory_key"] = sender.roots()[f"artifacts/{version}"]
        if pins["v1"]["build_id"] == pins["v2"]["build_id"]:
            raise ValueError("changed source must have a different build input ID")
        if pins["v1"]["directory_key"] == pins["v2"]["directory_key"]:
            raise ValueError("changed output must have a different artifact directory key")
        trees = [sender.entries(sender.entries(pins[v]["directory_key"])["outputs"])
                 for v in ("v1", "v2")]
        if trees[0]["style.css"] != trees[1]["style.css"] or trees[0]["index.html"] == trees[1]["index.html"]:
            raise ValueError("expected shared CSS and distinct HTML object identities")
        receipt["shared_stylesheet_key"] = trees[0]["style.css"]
        receipt["local_build_count"] = 2
        archive = output / "artifacts.casitar"
        receipt["archive_create"] = json.loads(sender.run(
            "archive", "create", "--root", "artifacts/v1", "--root", "artifacts/v2",
            "--output", archive, "--json"))
        pins["archive_sha256"] = digest(archive.read_bytes())
        (output / "pins.json").write_bytes(canonical(pins))
        expected = json.loads((output / "pins.json").read_bytes())
        checked_archive(archive, expected["archive_sha256"])
        controls = {"wrong_archive_pin_before_import": rejected(lambda: checked_archive(archive, "0" * 64))}
        receipt["archive_verification"] = json.loads(sender.run("archive", "verify", archive, "--json"))

        # Remove only our newly generated synthetic inputs and build directories.
        shutil.rmtree(output / "sources")
        shutil.rmtree(output / "builds")
        if (output / "sources").exists() or (output / "builds").exists():
            raise ValueError("original source or build directory still exists")
        receipt["original_sources_and_builds_removed"] = True
        receiver.run("init")
        receipt["archive_import"] = json.loads(receiver.run(
            "archive", "import", archive, "--root-prefix", "received", "--json"))
        roots = receiver.roots()
        if len(roots) != 2 or set(roots.values()) != {expected[v]["directory_key"] for v in ("v1", "v2")}:
            raise ValueError("received artifact roots differ from expected sender pins")
        for version in ("v1", "v2"):
            restored = output / "received" / version
            restored.parent.mkdir(exist_ok=True)
            receiver.run("checkout", expected[version]["directory_key"], restored, "--no-root")
            verify_bundle(restored, expected[version])
            if file_map(restored) != original_files[version]:
                raise ValueError("restored files differ from the original build")
        receiver.run("root", "set", "artifacts/current", expected["v2"]["directory_key"])
        receiver.run("root", "set", "artifacts/current", expected["v1"]["directory_key"])
        if receiver.roots()["artifacts/current"] != expected["v1"]["directory_key"]:
            raise ValueError("rollback root did not select v1")
        receiver.run("checkout", receiver.roots()["artifacts/current"], output / "received/rollback", "--no-root")
        verify_bundle(output / "received/rollback", expected["v1"])
        receipt["rollback_verified"] = True

        altered = output / "altered-artifact"
        shutil.copytree(output / "received/v1", altered)
        (altered / "outputs/index.html").write_bytes(b"synthetic altered output\n")
        controls["altered_output"] = rejected(lambda: verify_bundle(altered, expected["v1"]))
        extra = output / "extra-file"
        shutil.copytree(output / "received/v1", extra)
        (extra / "unexpected.txt").write_text("synthetic unexpected file\n")
        controls["extra_file"] = rejected(lambda: verify_bundle(extra, expected["v1"]))
        rebound = output / "rebound-metadata"
        shutil.copytree(output / "received/v1", rebound)
        path = rebound / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["build_inputs"]["recipe_sha256"] = "0" * 64
        manifest["build_id"] = digest(canonical(manifest["build_inputs"]))
        data = canonical(manifest)
        path.write_bytes(data)
        rebound_pin = dict(expected["v1"], manifest_id=digest(data))
        controls["rebound_build_inputs"] = rejected(lambda: verify_bundle(rebound, rebound_pin))
        wrong_version = dict(expected["v1"], version="v2")
        controls["wrong_version"] = rejected(lambda: verify_bundle(output / "received/v1", wrong_version))
        sender.run("fsck", "--dry-run")
        receiver.run("fsck", "--dry-run")
        receipt.update(ok=True, pins=expected, original_file_hashes=original_files,
                       negative_controls=controls, integrity_audits_passed=True)
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        (output / "receipt.json").write_bytes(canonical(receipt))
    print("PASS: two site builds record source and recipe hashes, toolchain metadata and output hashes")
    print("PASS: changed HTML has a new identity; unchanged CSS shares one identity")
    print("PASS: original source and build directories removed before receiver import")
    print("PASS: both versions restored exactly in a fresh store without rebuilding")
    print("PASS: current root rolled back from v2 to v1 with verified output")
    print("PASS: wrong archive pin, altered output, extra file and rebound metadata rejected")
    print("PASS: wrong version rejected; sender and receiver pass integrity audits")
    print(f"Restored artifacts: {output / 'received'}")
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
        print(f"Artifact demo failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
