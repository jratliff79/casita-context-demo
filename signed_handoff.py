#!/usr/bin/env python3
"""Synthetic sender/worker signature round trip with pre-provisioned demo trust."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

import authentication as auth
import demo
import handoff


def run_role(binary, output, mode, archive=None, pins=None, signature=None,
             allowed=None, signer=None, original=None, reject=False):
    argv = [sys.executable, str(Path(__file__).with_name("handoff.py")), mode,
            "--casita", binary, "--output", str(output)]
    for flag, value in (("archive", archive), ("pins", pins), ("signature", signature),
                        ("allowed-signers", allowed), ("signer", signer), ("original", original)):
        if value is not None:
            argv.extend([f"--{flag}", str(value)])
    result = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    receipt = json.loads((output / "receipt.json").read_bytes())
    if reject:
        if (result.returncode == 0 or receipt["ok"] or
                receipt.get("error") != "pin signature rejected for expected signer and namespace" or
                (output / "store").exists() or
                any(c["argv"][3:] != ["--version"] for c in receipt["commands"])):
            raise RuntimeError("signature control did not reject before store initialization")
    elif result.returncode != 0 or not receipt["ok"]:
        raise RuntimeError(f"signed demo {mode} failed: {result.stderr}")
    return receipt


def replacement_archive(binary, output, sender):
    """Produce valid, self-consistent replacement evidence with new hashes."""
    output.mkdir()
    receipt = {"commands": []}
    store = demo.Casita(binary, output / "store", receipt["commands"])
    store.run("init")
    pins = {}
    for version in handoff.VERSIONS:
        folder = output / "contexts" / version
        shutil.copytree(sender / "contexts" / version, folder)
        (folder / "task.md").write_bytes((folder / "task.md").read_bytes() + b"\nSynthetic replacement task.\n")
        manifest = json.loads((folder / "manifest.json").read_bytes())
        (folder / "manifest.json").unlink()
        manifest["files"] = demo.file_map(folder)
        raw = demo.canonical(manifest)
        (folder / "manifest.json").write_bytes(raw)
        store.run("import", folder, "--root", f"input/{version}")
        pins[version] = {"context_id": demo.digest(raw), "directory_key": store.roots()[f"input/{version}"]}
    handoff.export(store, output, "input", pins, "input", receipt)
    store.run("fsck", "--dry-run")
    (output / "receipt.json").write_bytes(demo.canonical(receipt))


def run(args):
    binary = shutil.which(args.casita)
    if not binary:
        raise ValueError("Casita executable not found")
    binary = str(Path(binary).resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    keys = output / "throwaway-keys"
    keys.mkdir(mode=0o700)
    receipt = {"schema": "casita-context-demo.signed-handoff.v1",
        "scope": "synthetic local trust provisioning; no execution attestation or freshness proof",
        "negative_controls": {}, "ok": False}
    try:
        for name, identity, role in (("sender", "synthetic-sender", "input"),
                                     ("worker", "synthetic-worker", "result"),
                                     ("outsider", "synthetic-sender", "input")):
            auth.make_demo_key(keys / name)
            if name != "outsider":
                auth.provision_demo_signer(keys / name, output / f"{name}-allowed-signers", identity, role)
        sender = output / "sender"
        worker = output / "worker"
        returned = output / "returned"
        run_role(binary, sender, "prepare")
        auth.sign_demo_pins(sender / "pins.json", keys / "sender", sender / "pins.sig", "input")
        worked = run_role(binary, worker, "work", sender / "handoff.casitar", sender / "pins.json",
            sender / "pins.sig", output / "sender-allowed-signers", "synthetic-sender")
        auth.sign_demo_pins(worker / "pins.json", keys / "worker", worker / "pins.sig", "result")
        verified = run_role(binary, returned, "verify", worker / "handoff.casitar", worker / "pins.json",
            worker / "pins.sig", output / "worker-allowed-signers", "synthetic-worker", sender)
        receipt.update(input_authentication=worked["authentication"],
            result_authentication=verified["authentication"], verdicts=worked["verdicts"],
            original_contexts_verified=verified["original_contexts_verified"], received_code_executed=False,
            execution_attested=False)

        replacement = output / "replacement"
        replacement_archive(binary, replacement, sender)
        # Hashes alone accept this internally consistent replacement. This
        # unsigned control illustrates why the earlier demo needs trusted pins.
        unsigned = run_role(binary, output / "unsigned-replacement", "work",
            replacement / "handoff.casitar", replacement / "pins.json")
        receipt["unsigned_replacement_accepted"] = unsigned["ok"]
        auth.sign_demo_pins(replacement / "pins.json", keys / "outsider", replacement / "outsider.sig", "input")
        auth.sign_demo_pins(sender / "pins.json", keys / "sender", sender / "wrong-namespace.sig", "result")
        cases = (("replaced_archive_and_pins", replacement, sender / "pins.sig", "synthetic-sender"),
                 ("unknown_signing_key", replacement, replacement / "outsider.sig", "synthetic-sender"),
                 ("wrong_namespace", sender, sender / "wrong-namespace.sig", "synthetic-sender"),
                 ("wrong_identity", sender, sender / "pins.sig", "synthetic-other"))
        for name, source, signature, identity in cases:
            run_role(binary, output / f"rejected-{name}", "work", source / "handoff.casitar",
                source / "pins.json", signature, output / "sender-allowed-signers", identity, reject=True)
            receipt["negative_controls"][name] = True
        receipt["ok"] = True
    except Exception as error:
        receipt["error"] = str(error)
        raise
    finally:
        shutil.rmtree(keys)
        receipt["throwaway_private_keys_removed"] = True
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print(f"PASS: signed input/result handoffs; four signature controls rejected before import\nReceipt: {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--output", type=Path, required=True)
    try:
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Signed handoff failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
