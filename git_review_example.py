#!/usr/bin/env python3
"""Exercise the Git review protocol with a synthetic report, without invoking AI."""
import argparse
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys

import authentication as auth
import demo
import git_review as review
import review_handoff as transport


def make_repository(folder):
    folder.mkdir()
    classifier = b'''"""Synthetic performance gate; this example does not execute it."""

PERFORMANCE_PAGES = {"HomePage", "PricingPage"}

def needs_performance_check(page):
    return page in PERFORMANCE_PAGES
'''
    (folder / "classifier.py").write_bytes(classifier)
    (folder / "pages.json").write_bytes(b'[\n  "HomePage",\n  "PricingPage",\n  "ChartsPage"\n]\n')
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(folder), *args], env=env, timeout=15,
                                       stdin=subprocess.DEVNULL, stderr=subprocess.PIPE)
    git("init", "-q")
    git("add", "--", "classifier.py", "pages.json")
    git("-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Synthetic review fixture")
    commit = git("rev-parse", "HEAD").decode().strip()
    # Neither dirty tracked bytes nor an untracked note belong in the capsule.
    (folder / "classifier.py").write_text("SYNTHETIC_DIRTY_CHECKOUT = True\n")
    (folder / ".env").write_text("SYNTHETIC_UNSELECTED_NOTE=must-not-be-packaged\n")
    return commit


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    keys = output / "throwaway-keys"
    keys.mkdir(mode=0o700)
    receipt = {"schema": "casita-context-demo.git-review-example.v1", "synthetic_fixture": True,
               "fresh_ai_review": False, "received_code_executed": False, "execution_attested": False,
               "review_quality_verified": False, "os_sandbox_enforced": False, "negative_controls": {}}
    def role(mode, name, **kwargs):
        options = {k: None for k in ("source", "spec", "observation", "signing_key", "archive", "pins", "signature",
                                    "allowed_signers", "signer", "context", "report", "original")}
        options.update(kwargs)
        return review.run(argparse.Namespace(mode=mode, output=output / name, casita=args.casita, **options))
    def transfer(folder, signer):
        return {"archive": folder / "handoff.casitar", "pins": folder / "pins.json", "signature": folder / "pins.sig",
                "allowed_signers": output / (signer + "-allowed-signers"), "signer": "synthetic-" + signer}
    try:
        repository = output / "synthetic-repository"
        commit = make_repository(repository)
        spec = {"schema": review.SPEC_SCHEMA, "source_label": "synthetic-performance-example", "commit": commit,
                "sensitivity": "synthetic", "purpose": "Review a synthetic page inventory mismatch. No real CI incident or runtime measurement.",
                "selections": [{"id": "classifier", "path": "classifier.py", "start": 3, "end": 6},
                               {"id": "inventory", "path": "pages.json", "start": 1, "end": 5}]}
        (output / "spec.json").write_bytes(demo.canonical(spec))
        (output / "observation.json").write_bytes(demo.canonical({"synthetic": True,
            "assertion": "ChartsPage should trigger performance checks", "expected": True, "actual": False,
            "runtime_execution_verified": False}))
        for signer, namespace in (("sender", "review-input"), ("reviewer", "review-result")):
            auth.make_demo_key(keys / signer)
            auth.provision_demo_signer(keys / signer, output / (signer + "-allowed-signers"), "synthetic-" + signer, namespace)
        prepared = role("prepare", "sender", source=repository, spec=output / "spec.json",
                        observation=output / "observation.json", signing_key=keys / "sender")
        received = role("receive", "receiver", **transfer(output / "sender", "sender"))
        context = output / "receiver/context"
        _, selections = review.verify_context(context, prepared["pins"]["content_id"])
        citations = []
        for ident, line in (("classifier", 3), ("inventory", 4)):
            item = selections[ident]
            citations.append({"selection_id": ident, "path": item["path"], "blob_sha256": item["blob_sha256"],
                              "line_start": line, "line_end": line, "excerpt": item["lines"][line-item["start"]]})
        report = {"schema": review.REPORT_SCHEMA, "context_id": prepared["pins"]["content_id"],
                  "context_directory_key": prepared["pins"]["directory_key"], "source_commit": commit,
                  "reviewer": "synthetic scripted report; no AI invocation", "scope": review.SCOPE,
                  "findings": [{"priority": "P2", "title": "Synthetic inventory page omitted from performance set",
                                "body": "ChartsPage appears in the synthetic inventory but not in PERFORMANCE_PAGES. Include it if this inventory defines the intended performance scope.",
                                "citations": citations}],
                  "limitations": ["Scripted synthetic diagnosis, not a fresh AI review. No received code or tests executed; the observation is fabricated for this example."]}
        (output / "report.json").write_bytes(demo.canonical(report))
        role("return", "reviewer-return", context=output / "receiver", report=output / "report.json", signing_key=keys / "reviewer")
        verified = role("verify", "verified-return", source=repository, original=output / "sender",
                        **transfer(output / "reviewer-return", "reviewer"))
        # Valid signatures must not make false source bindings acceptable.
        variants = {}
        for name in ("wrong_context", "wrong_commit", "invented_excerpt", "wrong_blob", "uncaptured_lines"):
            bad = copy.deepcopy(report)
            citation = bad["findings"][0]["citations"][0]
            if name == "wrong_context": bad["context_id"] = "0" * 64
            if name == "wrong_commit": bad["source_commit"] = "0" * 40
            if name == "invented_excerpt": citation["excerpt"] = "invented synthetic source"
            if name == "wrong_blob": citation["blob_sha256"] = "0" * 64
            if name == "uncaptured_lines": citation["line_start"] = citation["line_end"] = 1
            variants[name] = bad
        for name, bad in variants.items():
            folder = output / ("forged-" + name)
            result = folder / "result"
            result.mkdir(parents=True)
            raw = demo.canonical(bad)
            (result / "report.json").write_bytes(raw)
            store = demo.Casita(str(Path(shutil.which(args.casita)).resolve()), folder / "store", [])
            transport.export(store, folder, result, demo.digest(raw), "review-result", keys / "reviewer", {})
            expected = "original Git review context" if name in ("wrong_context", "wrong_commit") else "citation"
            try:
                role("verify", "rejected-" + name, source=repository, original=output / "sender", **transfer(folder, "reviewer"))
            except ValueError as error:
                if expected not in str(error):
                    raise
            else:
                raise ValueError("signed invalid report accepted")
            receipt["negative_controls"][name] = True
        altered = output / "modified-pins.json"
        altered.write_bytes((output / "sender/pins.json").read_bytes() + b" ")
        options = transfer(output / "sender", "sender")
        options["pins"] = altered
        try:
            role("receive", "rejected-modified-pins", **options)
        except ValueError as error:
            if "signature rejected" not in str(error):
                raise
        else:
            raise ValueError("modified signed pins accepted")
        if (output / "rejected-modified-pins/store").exists():
            raise ValueError("store initialized before signature rejection")
        receipt["negative_controls"]["modified_pins_before_import"] = True
        if set(demo.file_map(context)) != {"manifest.json", "task.json", "observation.json", "source/classifier.json", "source/inventory.json"}:
            raise ValueError("unexpected restored file set")
        if selections["classifier"]["lines"][0] != 'PERFORMANCE_PAGES = {"HomePage", "PricingPage"}':
            raise ValueError("dirty working-tree data included")
        receipt.update(ok=True, source_commit=commit, original_git_verified=verified["original_git_verified"],
                       citations_verified=verified["citations_verified"], finding_count=verified["finding_count"],
                       input_signature_verified=received["signature_verified"], result_signature_verified=verified["signature_verified"],
                       working_tree_data_excluded=True)
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        shutil.rmtree(keys)
        receipt["throwaway_private_keys_removed"] = True
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print("PASS: synthetic Git-range review handoff, exact citations and signed rejection controls")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--output", type=Path, required=True)
    try:
        os.umask(0o077)
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Git review example failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
