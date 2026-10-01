#!/usr/bin/env python3
"""Transport a reviewed public OCI image alongside synthetic context graphs."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import uuid

import demo
import handoff

HERE = Path(__file__).resolve().parent
MAX_IMAGE_BYTES = 25_000_000
MAX_ARCHIVE_BYTES = 30_000_000
MAX_JSON_BYTES = 65_536
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
WORKER_CODE = """
import json, pathlib, runpy
d = runpy.run_path('/trusted/demo.py')
pins = json.loads(pathlib.Path('/trusted/pins.json').read_bytes())
for version in ('v1', 'v2'):
    p = pins[version]
    result = d['expected_result'](pathlib.Path('/contexts') / version,
        p['context_id'], p['directory_key'])
    folder = pathlib.Path('/results') / version
    folder.mkdir()
    (folder / 'result.json').write_bytes(d['canonical'](result))
print('PASS: trusted checker produced both synthetic results')
"""


def approved_image(selected_platform):
    fixture = read_json(HERE / "fixtures/environment.json")
    if fixture["schema"] != "casita-context-demo.environment.v1" or fixture["repository"] != "docker.io/library/python":
        raise ValueError("unexpected reviewed image fixture")
    if selected_platform not in fixture["platforms"]:
        raise ValueError("platform has no reviewed image pin")
    pin = dict(fixture["platforms"][selected_platform], platform=selected_platform)
    if not all(isinstance(pin.get(k), str) and DIGEST.fullmatch(pin[k]) for k in ("manifest_digest", "config_digest")):
        raise ValueError("reviewed image must use full SHA-256 digests")
    pin["reference"] = fixture["repository"] + "@" + pin["manifest_digest"]
    return pin


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("JSON must be a regular file without links")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("image JSON exceeds limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("image JSON must be an object")
    return value


def file_sha(path, limit=MAX_ARCHIVE_BYTES):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(65_536):
            total += len(chunk)
            if limit is not None and total > limit:
                raise ValueError("file exceeds byte limit")
            digest.update(chunk)
    return digest.hexdigest()


def verify_layout(folder, pin):
    """Verify selected image and every reachable blob before runtime loading."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("image layout must be a regular directory")
    files, total = set(), 0
    for count, path in enumerate(folder.rglob("*"), 1):
        if count > 64:
            raise ValueError("image layout exceeds entry limit")
        if path.is_symlink():
            raise ValueError("image layout contains a link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("image layout contains a special file")
        total += path.stat().st_size
        if total > MAX_IMAGE_BYTES:
            raise ValueError("image layout exceeds byte limit")
        files.add(path.relative_to(folder).as_posix())
    if read_json(folder / "oci-layout") != {"imageLayoutVersion": "1.0.0"}:
        raise ValueError("unexpected OCI layout version")
    index = read_json(folder / "index.json")
    descriptors = index.get("manifests")
    if index.get("schemaVersion") != 2 or not isinstance(descriptors, list) or len(descriptors) != 1:
        raise ValueError("expected exactly one image manifest")
    selected = descriptors[0]
    if not isinstance(selected, dict) or selected.get("digest") != pin["manifest_digest"]:
        raise ValueError("image manifest pin mismatch")
    expected = {"index.json", "oci-layout"}

    def blob(descriptor):
        if not isinstance(descriptor, dict):
            raise ValueError("invalid image blob descriptor")
        digest, size = descriptor.get("digest"), descriptor.get("size")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest) or type(size) is not int or not 0 <= size <= MAX_IMAGE_BYTES:
            raise ValueError("invalid image blob digest or size")
        name = "blobs/sha256/" + digest.removeprefix("sha256:")
        path = folder / name
        if name not in files or path.stat().st_size != size or "sha256:" + file_sha(path) != digest:
            raise ValueError("image blob digest or size mismatch")
        expected.add(name)
        return path

    manifest = read_json(blob(selected))
    layers = manifest.get("layers")
    if manifest.get("schemaVersion") != 2 or not isinstance(layers, list) or not 1 <= len(layers) <= 16:
        raise ValueError("unexpected image manifest")
    config_descriptor = manifest.get("config")
    if not isinstance(config_descriptor, dict) or config_descriptor.get("digest") != pin["config_digest"]:
        raise ValueError("image config pin mismatch")
    config = read_json(blob(config_descriptor))
    if f"{config.get('os')}/{config.get('architecture')}" != pin["platform"]:
        raise ValueError("image platform mismatch")
    for layer in layers:
        blob(layer)
    if files != expected:
        raise ValueError("unexpected files in image layout")
    return sorted(files)


def runtime_run(argv, commands):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=180)
    commands.append({"argv": list(map(str, argv)), "exit_code": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode:
        raise RuntimeError(f"container command failed: {result.stderr}")
    return result.stdout


def verify_runtime_image(inspected, name, pin, config):
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(inspected[0], dict):
        raise ValueError("unexpected runtime image readback")
    image = inspected[0]
    variants = image.get("variants")
    configuration = image.get("configuration")
    if (not isinstance(configuration, dict) or configuration.get("name") != name
            or not isinstance(variants, list) or len(variants) != 1 or not isinstance(variants[0], dict)):
        raise ValueError("unexpected runtime image name or variants")
    variant = variants[0]
    selected_platform = variant.get("platform", {})
    if (not isinstance(selected_platform, dict) or variant.get("digest") != pin["manifest_digest"]
            or f"{selected_platform.get('os')}/{selected_platform.get('architecture')}" != pin["platform"]
            or variant.get("config") != config):
        raise ValueError("loaded runtime image identity mismatch")


def load_apple_image(folder, pin, output, commands):
    files = verify_layout(folder, pin)
    name = "localhost/casita-context-demo-worker:" + uuid.uuid4().hex[:12]
    # Tag only the transport index for the runtime adapter. Image blobs stay exact.
    index = read_json(folder / "index.json")
    index["manifests"][0]["annotations"] = {"org.opencontainers.image.ref.name": name}
    archive = output / "worker-image.tar"
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as tar:
        for relative in files:
            data = demo.canonical(index) if relative == "index.json" else (folder / relative).read_bytes()
            info = tarfile.TarInfo(relative)
            info.mode, info.size = 0o644, len(data)
            tar.addfile(info, io.BytesIO(data))
    runtime_run(["container", "image", "load", "--input", archive], commands)
    inspected = json.loads(runtime_run(["container", "image", "inspect", name], commands))
    config = read_json(folder / "blobs/sha256" / pin["config_digest"].removeprefix("sha256:"))
    verify_runtime_image(inspected, name, pin, config)
    return name


def run(args):
    pin = approved_image(args.platform)
    binary = shutil.which(args.casita)
    if not binary:
        raise ValueError("Casita executable not found")
    if args.runtime == "apple":
        arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine())
        if platform.system() != "Darwin" or args.platform != f"linux/{arch}":
            raise ValueError("Apple trial requires the host's native Linux image architecture")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "casita-context-demo.environment-receipt.v1", "image": pin,
               "commands": [], "runtime_commands": [], "runtime": args.runtime,
               "scope": "synthetic evidence and reviewed public image transport; execution unattested"}
    try:
        binary = str(Path(binary).resolve())
        sender = demo.Casita(binary, output / "sender-store", receipt["commands"])
        receiver = demo.Casita(binary, output / "receiver-store", receipt["commands"])
        returned = demo.Casita(binary, output / "return-store", receipt["commands"])
        receipt["casita_version"] = sender.run("--version").strip()
        receipt["casita_binary_sha256"] = file_sha(Path(binary), limit=None)
        sender.run("init")
        sender.run("import", "-i", "oci", pin["reference"], "--root", "image/python",
                   "--oci-platform", pin["platform"], "--oci-max-blob-bytes", MAX_IMAGE_BYTES,
                   "--oci-max-total-blob-bytes", MAX_IMAGE_BYTES)
        context_pins = {}
        for version in handoff.VERSIONS:
            context_id = demo.create_context(output / "contexts" / version, version)
            sender.run("import", output / "contexts" / version, "--root", f"input/{version}")
            context_pins[version] = {"context_id": context_id, "directory_key": sender.roots()[f"input/{version}"]}
        image_key = sender.roots()["image/python"]
        archive = output / "environment.casitar"
        sender.run("archive", "create", "--root", "image/python", "--root", "input/v1",
                   "--root", "input/v2", "--output", archive, "--json")
        sender.run("archive", "verify", archive, "--json")
        pins = {"schema": "casita-context-demo.environment-pins.v1", "image": dict(pin, directory_key=image_key),
                "contexts": context_pins, "archive_sha256": file_sha(archive)}
        (output / "pins.json").write_bytes(demo.canonical(pins))
        receipt["pins"] = pins
        if archive.stat().st_size > MAX_ARCHIVE_BYTES or file_sha(archive) != pins["archive_sha256"]:
            raise ValueError("environment archive size or digest mismatch")
        receiver.run("init")
        receiver.run("archive", "import", archive, "--root-prefix", "received",
                     "--max-archive-bytes", MAX_ARCHIVE_BYTES, "--json")
        roots = receiver.roots()
        expected_keys = {image_key, *(p["directory_key"] for p in context_pins.values())}
        if len(roots) != 3 or set(roots.values()) != expected_keys:
            raise ValueError("received image/context roots do not match expected pins")
        layout = output / "restored-image"
        receiver.run("checkout", image_key, layout, "--no-root")
        for version, p in context_pins.items():
            folder = output / "received" / version
            folder.parent.mkdir(exist_ok=True)
            receiver.run("checkout", p["directory_key"], folder, "--no-root")
            demo.verify_context(folder, p["context_id"])
        verify_layout(layout, pin)
        wrong = dict(pin, manifest_digest="sha256:" + "0" * 64)
        before = len(receipt["runtime_commands"])
        try:
            load_apple_image(layout, wrong, output, receipt["runtime_commands"])
        except ValueError as error:
            if str(error) != "image manifest pin mismatch":
                raise
        else:
            raise ValueError("wrong image pin was accepted")
        assert len(receipt["runtime_commands"]) == before
        receipt["wrong_image_pin_rejected_before_runtime"] = True
        results_folder = output / "results"
        results_folder.mkdir()
        if args.runtime == "apple":
            name = load_apple_image(layout, pin, output, receipt["runtime_commands"])
            trusted = output / "trusted"
            (trusted / "fixtures/source").mkdir(parents=True)
            shutil.copyfile(HERE / "demo.py", trusted / "demo.py")
            shutil.copyfile(demo.TRUSTED_CHECKER, trusted / "fixtures/source/verify.py")
            (trusted / "pins.json").write_bytes(demo.canonical(context_pins))
            try:
                runtime_run(["container", "run", "--rm", "--cpus", "1", "--memory", "512M",
                             "--read-only", "--network", "none", "--entrypoint", "python",
                             "--mount", f"type=bind,source={trusted},target=/trusted,readonly",
                             "--mount", f"type=bind,source={output / 'received'},target=/contexts,readonly",
                             "--mount", f"type=bind,source={results_folder},target=/results",
                             name, "-I", "-B", "-c", WORKER_CODE], receipt["runtime_commands"])
            finally:
                runtime_run(["container", "image", "delete", name], receipt["runtime_commands"])
        else:
            for version, p in context_pins.items():
                folder = results_folder / version
                folder.mkdir()
                result = demo.expected_result(output / "received" / version, p["context_id"], p["directory_key"])
                (folder / "result.json").write_bytes(demo.canonical(result))
        if {p.name for p in results_folder.iterdir()} != set(handoff.VERSIONS):
            raise ValueError("unexpected worker output")
        result_pins = {}
        for version, p in context_pins.items():
            folder = results_folder / version
            files = demo.file_map(folder)
            if set(files) != {"result.json"}:
                raise ValueError("unexpected result file set")
            result_id = files["result.json"]
            demo.verify_result(folder, result_id, output / "contexts" / version, p["context_id"], p["directory_key"])
            receipt.setdefault("verdicts", {})[version] = json.loads((folder / "result.json").read_bytes())["verdict"]
            receiver.run("import", folder, "--root", f"result/{version}")
            result_pins[version] = {"result_id": result_id, "directory_key": receiver.roots()[f"result/{version}"]}
        return_folder = output / "result-return"
        return_folder.mkdir()
        handoff.export(receiver, return_folder, "result", result_pins, "result", {})
        receipt["result_pins"] = result_pins
        handoff.receive(returned, return_folder / "handoff.casitar", result_pins, return_folder, {})
        for version, p in context_pins.items():
            demo.verify_result(return_folder / "received" / version, result_pins[version]["result_id"],
                               output / "contexts" / version, p["context_id"], p["directory_key"])
        for store in (sender, receiver, returned):
            store.run("fsck", "--dry-run")
        receipt.update(ok=True, image_layout_verified=True, archive_bytes=archive.stat().st_size,
                       original_contexts_verified=True, received_checker_executed=False,
                       worker_executed_in_container=args.runtime == "apple", execution_attested=False)
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print(f"PASS: pinned image and contexts restored; results verified; runtime={args.runtime}")
    print(f"Receipt: {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita", help="CLI built with --features oci")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=("linux/arm64", "linux/amd64"), default="linux/arm64")
    parser.add_argument("--runtime", choices=("none", "apple"), default="none",
                        help="none checks transport and uses the host checker; apple runs the checker in the restored image")
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Environment handoff failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
