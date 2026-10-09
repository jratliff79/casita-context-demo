#!/usr/bin/env python3
"""Replay a recorded capsule-only review of allowlisted public MIT source."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import authentication as auth
import demo
import git_review as review
import review_handoff as transport

SOURCE = json.loads((Path(__file__).parent / "fixtures/git-review-source.json").read_bytes())


def verify_public_source(repository, source=None):
    source = SOURCE if source is None else source
    if source["commit"] != source["spec"]["commit"] or source["license"] != "MIT":
        raise ValueError("invalid public source allowlist")
    if {item["path"] for item in source["spec"]["selections"]} != set(source["files"]):
        raise ValueError("selection paths differ from public source allowlist")
    for path, expected in source["files"].items():
        if demo.digest(review.git_blob(repository, source["commit"], path)) != expected:
            raise ValueError("source differs from allowlisted public Git blob")


def run(args):
    case = getattr(args, "case", "git-review")
    if case not in ("git-review", "docs-checkpoint"):
        raise ValueError("unknown recorded public case")
    source = SOURCE if case == "git-review" else review.read_json(
        Path(__file__).parent / "fixtures/docs-checkpoint-source.json")
    # Fail before preparing any artifact from a caller-selected source.
    verify_public_source(args.source, source)
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    keys = output / "throwaway-keys"
    keys.mkdir(mode=0o700)
    receipt = {"schema": "casita-context-demo.public-git-review-replay.v1",
               "public_source_snapshot": True, "synthetic_fixture": False, "fresh_ai_review": False,
               "received_code_executed": False, "execution_attested": False,
               "review_quality_verified": False, "os_sandbox_enforced": False,
               "return_signer_role": "local controller; not independently attested model identity",
               "recorded_case": case,
               "negative_controls": {}}
    def role(mode, name, **kwargs):
        options = {key: None for key in ("source", "spec", "observation", "signing_key", "archive", "pins", "signature",
                                        "allowed_signers", "signer", "context", "report", "original")}
        options.update(kwargs)
        return review.run(argparse.Namespace(mode=mode, output=output / name, casita=args.casita, **options))
    def transfer(folder, identity):
        return {"archive": folder / "handoff.casitar", "pins": folder / "pins.json", "signature": folder / "pins.sig",
                "allowed_signers": output / (identity + "-allowed-signers"), "signer": "public-git-review-" + identity}
    try:
        (output / "spec.json").write_bytes(demo.canonical(source["spec"]))
        (output / "observation.json").write_bytes(demo.canonical(source["observation"]))
        for identity, namespace in (("sender", "review-input"), ("return-controller", "review-result")):
            auth.make_demo_key(keys / identity)
            auth.provision_demo_signer(keys / identity, output / (identity + "-allowed-signers"),
                                       "public-git-review-" + identity, namespace)
        prepared = role("prepare", "sender", source=args.source, spec=output / "spec.json",
                        observation=output / "observation.json", signing_key=keys / "sender")
        received = role("receive", "receiver", **transfer(output / "sender", "sender"))
        returned = role("return", "reviewer-return", context=output / "receiver", report=args.report,
                        signing_key=keys / "return-controller")
        verified = role("verify", "verified-return", source=args.source, original=output / "sender",
                        **transfer(output / "reviewer-return", "return-controller"))
        report = review.read_json(args.report, demo.MAX_BYTES)
        _, selections = review.verify_context(output / "receiver/context", prepared["pins"]["content_id"])
        # These are deliberately invalid synthetic controls, not reviewer findings.
        controls = ["wrong_context", "wrong_commit", "invented_excerpt", "wrong_blob", "uncaptured_lines"]
        if case == "docs-checkpoint":
            controls.append("wrong_directory_key")
        for name in controls:
            bad = copy.deepcopy(report)
            if not bad["findings"]:
                selection = next(iter(selections.values()))
                bad["findings"] = [{"title": "Invalid synthetic control", "body": "Not a reviewer finding.", "priority": "P2",
                    "citations": [{"selection_id": selection["id"], "path": selection["path"],
                        "blob_sha256": selection["blob_sha256"], "line_start": selection["start"], "line_end": selection["start"],
                        "excerpt": selection["lines"][0]}]}]
            citation = bad["findings"][0]["citations"][0]
            if name == "wrong_context": bad["context_id"] = "0" * 64
            if name == "wrong_directory_key": bad["context_directory_key"] = "incorrect synthetic key"
            if name == "wrong_commit": bad["source_commit"] = "0" * 40
            if name == "invented_excerpt": citation["excerpt"] = "invented synthetic source"
            if name == "wrong_blob": citation["blob_sha256"] = "0" * 64
            if name == "uncaptured_lines": citation["line_start"] = citation["line_end"] = 1000000
            folder = output / ("forged-" + name)
            result = folder / "result"
            result.mkdir(parents=True)
            raw = demo.canonical(bad)
            (result / "report.json").write_bytes(raw)
            store = demo.Casita(str(Path(shutil.which(args.casita)).resolve()), folder / "store", [])
            transport.export(store, folder, result, demo.digest(raw), "review-result", keys / "return-controller", {})
            expected = "original Git review context" if name in ("wrong_context", "wrong_commit", "wrong_directory_key") else "citation"
            try:
                role("verify", "rejected-" + name, source=args.source, original=output / "sender", **transfer(folder, "return-controller"))
            except ValueError as error:
                if expected not in str(error):
                    raise
            else:
                raise ValueError("signed invalid report accepted")
            receipt["negative_controls"][name] = True
        if case == "docs-checkpoint":
            mixed = output / "mixed-progress"
            shutil.copytree(output / "receiver/context", mixed)
            state = review.read_json(mixed / "observation.json")
            state["visible_task_state"]["pending"] = []
            (mixed / "observation.json").write_bytes(demo.canonical(state))
            manifest = review.read_json(mixed / "manifest.json")
            manifest["files"]["observation.json"] = demo.digest((mixed / "observation.json").read_bytes())
            (mixed / "manifest.json").write_bytes(demo.canonical(manifest))
            try:
                review.validate_report(report, mixed, prepared["pins"])
            except ValueError as error:
                if "context ID mismatch" not in str(error):
                    raise
            else:
                raise ValueError("altered progress accepted against original context pins")
            receipt["negative_controls"]["rehashed_mixed_progress"] = True
        receipt.update(ok=True, source_repository=source["repository"], source_commit=source["commit"], source_license=source["license"],
                       context_pins=prepared["pins"], result_pins=returned["pins"],
                       input_signature_verified=received["signature_verified"], result_signature_verified=verified["signature_verified"],
                       original_git_verified=verified["original_git_verified"], citations_verified=verified["citations_verified"],
                       finding_count=verified["finding_count"])
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        shutil.rmtree(keys)
        receipt["throwaway_private_keys_removed"] = True
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print("PASS: recorded public Git review, signed return, original Git citations and rejection controls")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--case", choices=("git-review", "docs-checkpoint"), default="git-review")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    try:
        os.umask(0o077)
        args = parser.parse_args()
        if args.report is None:
            args.report = Path(__file__).parent / "docs" / (args.case + "-report.json")
        run(args)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Public Git review replay failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
