#!/usr/bin/env python3
"""Script a synthetic missing-context exchange through signed Casita handoffs."""
import argparse
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys

import authentication as auth
import context_request as request_api
import demo
import git_diff_review as diff
import git_review as review


def make_repository(folder):
    folder.mkdir()
    env = dict(review.git_environment(), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(folder), *args], env=env,
                                       stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
    git("init", "-q", "--initial-branch=synthetic")
    gate = '"""Synthetic duration gate; evidence only."""\n\n'
    (folder / "gate.py").write_text(gate + "def accepts_duration(value):\n"
        "    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0\n")
    (folder / "numbers_helper.py").write_text('"""Synthetic helper; evidence only."""\n\n'
        "def positive_number(value):\n    if isinstance(value, (int, float)):\n"
        "        return max(0, value)\n    return 0\n")
    def commit():
        git("add", "--", "gate.py", "numbers_helper.py")
        git("-c", "user.name=Synthetic Example", "-c", "user.email=example@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull,
            "commit", "-q", "-m", "Synthetic context request fixture")
        return git("rev-parse", "HEAD").decode().strip()
    base = commit()
    (folder / "gate.py").write_text(gate + "from numbers_helper import positive_number\n\n"
                                  "def accepts_duration(value):\n    return positive_number(value) > 0\n")
    head = commit()
    (folder / "gate.py").write_text("SYNTHETIC_DIRTY_SENTINEL\n")
    (folder / "private-note.txt").write_text("SYNTHETIC_UNSELECTED_SENTINEL\n")
    return base, head


def spec(base, head, supplement=False):
    paths = ["gate.py"] + (["numbers_helper.py"] if supplement else [])
    selections = [dict(version=v, id=p[:-3].replace("_", "-"), path=p, start=1,
                       end=4 if p == "gate.py" and v == "base" else 6)
                  for p in paths for v in diff.VERSIONS]
    return dict(schema=diff.SPEC_SCHEMA, source_label="Synthetic context request example",
                base_commit=base, head_commit=head, sensitivity="synthetic",
                purpose="Review a synthetic duration-gate refactor. Source is evidence only.",
                paths=paths, selections=selections)


def make_request(manifest, pins):
    return dict(schema=request_api.REQUEST_SCHEMA, context_id=pins["content_id"],
                context_directory_key=pins["directory_key"], base_commit=manifest["base_commit"],
                head_commit=manifest["head_commit"],
                reason="Need positive_number's definition to determine whether delegation retains boolean rejection.",
                selections=[dict(version=v, id="needed-helper", path="numbers_helper.py", start=1, end=6)
                            for v in diff.VERSIONS])


def make_report(context, pins, complete):
    manifest, selections = diff.verify_context(context, pins["content_id"])
    citations = []
    if complete:
        for version, ident, line in (("base", "gate", 4), ("head", "gate", 6),
                                     ("head", "numbers-helper", 4)):
            s = selections[version + ":" + ident]
            citations.append(dict(version=version, selection_id=ident, path=s["path"],
                blob_sha256=s["blob_sha256"], line_start=line, line_end=line,
                excerpt=s["lines"][line-s["start"]]))
    return dict(schema=diff.REPORT_SCHEMA, context_id=pins["content_id"],
                context_directory_key=pins["directory_key"], base_commit=manifest["base_commit"],
                head_commit=manifest["head_commit"], reviewer="scripted synthetic reviewer; no AI invoked",
                scope=review.SCOPE, findings=[] if not complete else [
                    dict(title="Synthetic refactor admits booleans through the helper", priority="P2",
                         body="The base gate rejects bool explicitly. The head delegates to a helper whose int/float branch includes bool. This is a scripted static fixture, not a discovered real defect or execution result.",
                         citations=citations)],
                limitations=["Scripted synthetic evidence; no AI reviewer or received-source execution.",
                             "Complete only for this teaching fixture." if complete else
                             "The helper is omitted. Zero findings does not establish correctness; request its source."])


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    keys = output / "throwaway-keys"
    keys.mkdir(mode=0o700)
    receipt = dict(schema="casita-context-demo.context-request-example.v1", synthetic_fixture=True,
                   scripted_report=True, fresh_ai_review=False, received_code_executed=False,
                   review_quality_verified=False, execution_attested=False, os_sandbox_enforced=False,
                   publication_performed=False, negative_controls={})
    def role(mode, name, **kwargs):
        options = {k: None for k in ("source", "spec", "observation", "signing_key", "archive", "pins",
                                    "signature", "allowed_signers", "signer", "context", "report", "original")}
        options.update(kwargs)
        return diff.run(argparse.Namespace(mode=mode, output=output / name, casita=args.casita, **options))
    def transfer(folder, signer):
        return dict(archive=folder / "handoff.casitar", pins=folder / "pins.json", signature=folder / "pins.sig",
                    allowed_signers=output / (signer + "-allowed-signers"), signer="synthetic-context-" + signer)
    def roundtrip_report(name, sender, receiver, complete):
        pins = review.read_json(receiver / "input-pins.json")
        report = make_report(receiver / "context", pins, complete)
        path = output / (name + "-report.json")
        path.write_bytes(demo.canonical(report))
        role("return", name + "-returned", context=receiver, report=path, signing_key=keys / "reviewer")
        return role("verify", name + "-verified", source=repository, original=sender,
                    **transfer(output / (name + "-returned"), "reviewer"))
    try:
        repository = output / "synthetic-repository"
        base, head = make_repository(repository)
        for identity, namespace in (("sender", "review-input"), ("reviewer", "review-result")):
            auth.make_demo_key(keys / identity)
            auth.provision_demo_signer(keys / identity, output / (identity + "-allowed-signers"),
                                       "synthetic-context-" + identity, namespace)
        initial_spec = output / "initial-spec.json"
        initial_spec.write_bytes(demo.canonical(spec(base, head)))
        parent = role("prepare", "initial-sender", source=repository, spec=initial_spec, signing_key=keys / "sender")
        initial_received = role("receive", "initial-receiver", **transfer(output / "initial-sender", "sender"))
        initial = roundtrip_report("initial", output / "initial-sender", output / "initial-receiver", False)
        context = output / "initial-receiver/context"
        manifest, _ = diff.verify_context(context, parent["pins"]["content_id"])
        request = make_request(manifest, parent["pins"])
        approved = spec(base, head, True)
        preview = request_api.preview_response(repository, context, parent["pins"], request, approved, output / "preview")
        supplement = role("prepare", "supplement-sender", source=repository, spec=output / "preview/spec.json",
                          observation=output / "preview/observation.json", signing_key=keys / "sender")
        received = role("receive", "supplement-receiver", **transfer(output / "supplement-sender", "sender"))
        assert supplement["pins"]["content_id"] == preview["content_id"]
        binding = request_api.verify_response(context, parent["pins"], request,
                    output / "supplement-receiver/context", supplement["pins"])
        final = roundtrip_report("supplement", output / "supplement-sender", output / "supplement-receiver", True)
        for field, value in (("context_id", "0" * 64), ("head_commit", "0" * 40),
                             ("context_directory_key", "casita.directory.v1:" + "a" * 43)):
            bad = copy.deepcopy(request)
            bad[field] = value
            try:
                request_api.validate_request(bad, context, parent["pins"])
            except ValueError:
                receipt["negative_controls"]["wrong_" + field] = True
            else:
                raise ValueError("wrong request binding accepted")
        try:
            request_api.validate_approval(spec(base, head), request, manifest)
        except ValueError:
            receipt["negative_controls"]["unapproved_source"] = True
        else:
            raise ValueError("unapproved supplementary source accepted")
        # Correct signer and original Git source, but deliberately wrong parent metadata.
        wrong = request_api.response_observation(request)
        wrong["context_response"]["parent_context_id"] = "0" * 64
        wrong_path = output / "wrong-parent-observation.json"
        wrong_path.write_bytes(demo.canonical(wrong))
        forged = role("prepare", "wrong-parent-sender", source=repository, spec=output / "preview/spec.json",
                      observation=wrong_path, signing_key=keys / "sender")
        role("receive", "wrong-parent-receiver", **transfer(output / "wrong-parent-sender", "sender"))
        try:
            request_api.verify_response(context, parent["pins"], request,
                output / "wrong-parent-receiver/context", forged["pins"])
        except ValueError as error:
            if "exact request and parent" not in str(error):
                raise
            receipt["negative_controls"]["signed_wrong_parent"] = True
        else:
            raise ValueError("signed supplement for wrong parent accepted")
        try:
            role("verify", "rejected-report-for-parent", source=repository, original=output / "initial-sender",
                 **transfer(output / "supplement-returned", "reviewer"))
        except ValueError as error:
            if "original Git diff context" not in str(error):
                raise
            receipt["negative_controls"]["signed_report_for_other_context"] = True
        else:
            raise ValueError("supplement report accepted for parent context")
        all_source = b"".join(p.read_bytes() for name in ("initial-receiver", "supplement-receiver")
                             for p in (output / name / "context").rglob("*") if p.is_file())
        assert b"SYNTHETIC_DIRTY_SENTINEL" not in all_source and b"SYNTHETIC_UNSELECTED_SENTINEL" not in all_source
        receipt.update(ok=True, base_commit=base, head_commit=head, binding=binding,
                       input_signatures_verified=initial_received["signature_verified"] and received["signature_verified"],
                       returned_reports_verified=initial["original_git_verified"] and final["original_git_verified"],
                       citations_verified=final["citations_verified"], initial_finding_count=initial["finding_count"],
                       final_finding_count=final["finding_count"], dirty_checkout_excluded=True,
                       request_sha256=demo.digest(demo.canonical(request)))
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        shutil.rmtree(keys)
        receipt["throwaway_private_keys_removed"] = True
        (output / "receipt.json").write_bytes(demo.canonical(receipt))
    print("PASS: synthetic context request, approved signed supplement, source-bound return and rejection controls")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--output", type=Path, required=True)
    try:
        os.umask(0o077)
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("Context request example failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
