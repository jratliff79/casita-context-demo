#!/usr/bin/env python3
"""Replay a recorded AI report through the signed protocol; does not invoke AI."""
import argparse
import copy
from pathlib import Path
import shutil
import subprocess
import sys

import authentication as auth
import demo
import review_handoff as review


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    keys = output / "throwaway-keys"
    keys.mkdir(mode=0o700)
    receipt = {"schema": "casita-context-demo.review-replay.v1", "fresh_ai_review": False,
               "negative_controls": {}, "execution_attested": False, "review_quality_verified": False}
    def role(mode, name, **kwargs):
        options = {key: None for key in ("source", "signing_key", "archive", "pins", "signature",
                                       "allowed_signers", "signer", "context", "report", "original")}
        options.update(kwargs)
        return review.run(argparse.Namespace(mode=mode, casita=args.casita, output=output / name, **options))
    def transfer(folder, name):
        return {"archive": folder / "handoff.casitar", "pins": folder / "pins.json",
                "signature": folder / "pins.sig", "allowed_signers": output / f"{name}-allowed-signers",
                "signer": f"public-demo-{name}"}
    try:
        for name, namespace in (("sender", "review-input"), ("reviewer", "review-result")):
            auth.make_demo_key(keys / name)
            auth.provision_demo_signer(keys / name, output / f"{name}-allowed-signers", f"public-demo-{name}", namespace)
        sender = role("prepare", "sender", source=args.source, signing_key=keys / "sender")
        receiver = role("receive", "receiver", **transfer(output / "sender", "sender"))
        returned = role("return", "reviewer-return", context=output / "receiver", report=args.report, signing_key=keys / "reviewer")
        verified = role("verify", "verified-return", original=output / "sender", **transfer(output / "reviewer-return", "reviewer"))
        report = review.read_json(args.report)
        # Even a valid signature and rebound report hash cannot authorize false
        # source bindings or invented excerpts. Construct signed bad results to
        # exercise the final receiver, without relying on producer validation.
        variants = {}
        wrong_context = copy.deepcopy(report)
        wrong_context["context_id"] = "0" * 64
        variants["wrong_context"] = wrong_context
        invented = copy.deepcopy(report)
        if not invented["findings"]:
            path = "source/authentication.py"
            invented["findings"] = [{"path": path, "file_sha256": review.SOURCE["files"]["authentication.py"],
                "line_start": 1, "line_end": 1, "excerpt": "invented excerpt", "priority": "P2",
                "title": "Invalid synthetic control", "body": "Not an actual finding."}]
        else:
            invented["findings"][0]["excerpt"] = "invented excerpt"
        variants["invented_excerpt"] = invented
        for name, bad_report in variants.items():
            folder = output / f"forged-{name}"
            result = folder / "result"
            result.mkdir(parents=True)
            raw = demo.canonical(bad_report)
            (result / "report.json").write_bytes(raw)
            commands = []
            store = demo.Casita(str(Path(shutil.which(args.casita)).resolve()), folder / "store", commands)
            review.export(store, folder, result, demo.digest(raw), "review-result", keys / "reviewer", {})
            store.run("fsck", "--dry-run")
            expected = "original source context" if name == "wrong_context" else "excerpt differs"
            try:
                role("verify", f"rejected-{name}", original=output / "sender", **transfer(folder, "reviewer"))
            except ValueError as error:
                if expected not in str(error):
                    raise
            else:
                raise ValueError("signed invalid report accepted")
            receipt["negative_controls"][name] = True
        receipt.update(ok=True, source_commit=review.SOURCE["commit"], context_pins=sender["pins"],
            result_pins=returned["pins"], input_signature_verified=receiver["signature_verified"],
            result_signature_verified=verified["signature_verified"], citations_verified=verified["citations_verified"],
            original_context_verified=verified["original_context_verified"], finding_count=verified["finding_count"])
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        shutil.rmtree(keys)
        receipt["throwaway_private_keys_removed"] = True
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print("PASS: recorded AI report transported and cited against original source; signed invalid reports rejected")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Review replay failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
