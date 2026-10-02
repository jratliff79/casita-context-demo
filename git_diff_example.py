#!/usr/bin/env python3
"""Demonstrate signed base/head review transport using allowlisted public PR 15."""
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
import git_diff_review as diff
import git_review as review
import review_handoff as transport

SOURCE = json.loads((Path(__file__).parent / "fixtures/git-diff-source.json").read_bytes())


def verify_public_source(repository):
    spec = SOURCE["spec"]
    if SOURCE["license"] != "MIT" or set(SOURCE["files"]) != set(diff.VERSIONS):
        raise ValueError("invalid public diff allowlist")
    for version in diff.VERSIONS:
        if set(SOURCE["files"][version]) != set(spec["paths"]):
            raise ValueError("diff paths differ from public source allowlist")
        for path, expected in SOURCE["files"][version].items():
            if demo.digest(review.git_blob(repository, spec[version + "_commit"], path)) != expected:
                raise ValueError("source differs from allowlisted public Git blob")


def run(args):
    verify_public_source(args.source)
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    keys = output / "throwaway-keys"
    keys.mkdir(mode=0o700)
    receipt = {"schema": "casita-context-demo.git-diff-example.v1", "public_source_snapshot": True,
               "synthetic_fixture": False, "scripted_report": True, "fresh_ai_review": False,
               "received_code_executed": False, "execution_attested": False, "review_quality_verified": False,
               "os_sandbox_enforced": False, "patch_applied": False, "negative_controls": {}}

    def role(mode, name, **kwargs):
        options = {key: None for key in ("source", "spec", "observation", "signing_key", "archive", "pins", "signature",
                                        "allowed_signers", "signer", "context", "report", "original")}
        options.update(kwargs)
        return diff.run(argparse.Namespace(mode=mode, output=output / name, casita=args.casita, **options))

    def transfer(folder, identity):
        return {"archive": folder / "handoff.casitar", "pins": folder / "pins.json", "signature": folder / "pins.sig",
                "allowed_signers": output / (identity + "-allowed-signers"), "signer": "public-git-diff-" + identity}

    try:
        (output / "spec.json").write_bytes(demo.canonical(SOURCE["spec"]))
        for identity, namespace in (("sender", "review-input"), ("return-controller", "review-result")):
            auth.make_demo_key(keys / identity)
            auth.provision_demo_signer(keys / identity, output / (identity + "-allowed-signers"),
                                       "public-git-diff-" + identity, namespace)
        prepared = role("prepare", "sender", source=args.source, spec=output / "spec.json", signing_key=keys / "sender")
        received = role("receive", "receiver", **transfer(output / "sender", "sender"))
        manifest, selections = diff.verify_context(output / "receiver/context", prepared["pins"]["content_id"])
        citations = []
        for version in diff.VERSIONS:
            selected = selections[version + ":budget"]
            # A known scripted citation control, not an AI finding.
            start = 306
            citations.append({"version": version, "selection_id": "budget", "path": selected["path"],
                              "blob_sha256": selected["blob_sha256"], "line_start": start, "line_end": start + 2,
                              "excerpt": "\n".join(selected["lines"][start-selected["start"]:start-selected["start"]+3])})
        report = {"schema": diff.REPORT_SCHEMA, "context_id": prepared["pins"]["content_id"],
                  "context_directory_key": prepared["pins"]["directory_key"],
                  "base_commit": manifest["base_commit"], "head_commit": manifest["head_commit"],
                  "reviewer": "scripted public change comparison; no AI review", "scope": review.SCOPE,
                  "findings": [{"title": "Synthetic citation control: outgoing size guard added", "priority": "P3",
                                "body": "Protocol fixture only, not a defect or a new review finding. The selected public change adds a canonical report size guard before result files are created.",
                                "citations": citations}],
                  "limitations": ["Scripted citation control; no AI model invoked and no review quality measurement.",
                                  "Only three explicitly allowlisted paths are supplied, not the complete PR.",
                                  "No received code executed, runtime reproduction performed or patch applied."]}
        (output / "report.json").write_bytes(demo.canonical(report))
        returned = role("return", "reviewer-return", context=output / "receiver", report=output / "report.json",
                        signing_key=keys / "return-controller")
        verified = role("verify", "verified-return", source=args.source, original=output / "sender",
                        **transfer(output / "reviewer-return", "return-controller"))
        for name in ("wrong_base", "wrong_head", "wrong_version", "invented_excerpt", "wrong_blob", "uncaptured_lines"):
            bad = copy.deepcopy(report)
            citation = bad["findings"][0]["citations"][0]
            if name == "wrong_base": bad["base_commit"] = "0" * 40
            if name == "wrong_head": bad["head_commit"] = "0" * 40
            if name == "wrong_version": citation["version"] = "head"
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
            expected = "original Git diff context" if name in ("wrong_base", "wrong_head") else "citation"
            try:
                role("verify", "rejected-" + name, source=args.source, original=output / "sender",
                     **transfer(folder, "return-controller"))
            except ValueError as error:
                if expected not in str(error):
                    raise
            else:
                raise ValueError("signed invalid report accepted")
            receipt["negative_controls"][name] = True
        # A legitimate signature does not prove that a sender-derived diff is true.
        forged = output / "forged-diff"
        context = forged / "context"
        shutil.copytree(output / "sender/context", context)
        changes = review.read_json(context / "changes.json", demo.MAX_BYTES)
        changes["files"][0]["diff"] = "invented synthetic diff"
        (context / "changes.json").write_bytes(demo.canonical(changes))
        altered = review.read_json(context / "manifest.json")
        altered["files"] = demo.file_map(context)
        altered["files"].pop("manifest.json")
        raw = demo.canonical(altered)
        (context / "manifest.json").write_bytes(raw)
        ident = demo.digest(raw)
        store = demo.Casita(str(Path(shutil.which(args.casita)).resolve()), forged / "store", [])
        transport.export(store, forged, context, ident, "review-input", keys / "sender", {})
        role("receive", "received-forged-diff", **transfer(forged, "sender"))
        try:
            diff.verify_git_source(args.source, output / "received-forged-diff/context", ident)
        except ValueError as error:
            if "original Git blobs" not in str(error):
                raise
        else:
            raise ValueError("signed forged diff accepted against original Git")
        receipt["negative_controls"]["rebound_signed_diff"] = True
        receipt.update(ok=True, source_repository=SOURCE["repository"], source_license=SOURCE["license"],
                       base_commit=manifest["base_commit"], head_commit=manifest["head_commit"],
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
    print("PASS: public base/head diff, signed scripted return, versioned citations and rejection controls")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        os.umask(0o077)
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Public Git diff example failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
