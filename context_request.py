#!/usr/bin/env python3
"""Preview explicitly approved supplements and verify their request/parent binding."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import demo
import git_diff_plan as plan
import git_diff_review as diff
import git_review as review
import review_handoff as transport

REQUEST_SCHEMA = "casita-context-demo.context-request.v1"
RESPONSE_SCHEMA = diff.RESPONSE_SCHEMA
REQUEST_LIMIT = 64_000


def checked_pins(pins):
    return transport.parse_pins(demo.canonical(pins), "review-input")


def validate_request(request, parent, pins):
    pins = checked_pins(pins)
    manifest, _ = diff.verify_context(parent, pins["content_id"])
    fields = {"schema", "context_id", "context_directory_key", "base_commit", "head_commit",
              "reason", "selections"}
    if (not isinstance(request, dict) or set(request) != fields
            or request["schema"] != REQUEST_SCHEMA
            or request["context_id"] != pins["content_id"]
            or request["context_directory_key"] != pins["directory_key"]
            or any(request[v + "_commit"] != manifest[v + "_commit"] for v in diff.VERSIONS)):
        raise ValueError("request differs from parent context or commits")
    review.text(request["reason"], "missing-context reason", 2000)
    selections = request["selections"]
    if not isinstance(selections, list) or not 1 <= len(selections) <= 20:
        raise ValueError("request 1 to 20 source ranges")
    for item in selections:
        diff.selection(item)
    if len({(s["version"], s["id"]) for s in selections}) != len(selections):
        raise ValueError("duplicate requested selection")
    if len(demo.canonical(request)) > REQUEST_LIMIT:
        raise ValueError("request exceeds byte limit")
    return manifest


def covers(selections, wanted):
    return any(s["version"] == wanted["version"] and s["path"] == wanted["path"]
               and s["start"] <= wanted["start"] <= wanted["end"] <= s["end"]
               for s in selections)


def validate_approval(spec, request, manifest):
    fields = {"schema", "source_label", "base_commit", "head_commit", "sensitivity",
              "purpose", "paths", "selections"}
    if (not isinstance(spec, dict) or set(spec) != fields or spec["schema"] not in (diff.SPEC_SCHEMA, diff.SUPPLEMENT_SPEC_SCHEMA)
            or spec["sensitivity"] != manifest["sensitivity"]
            or any(spec[v + "_commit"] != manifest[v + "_commit"] for v in diff.VERSIONS)):
        raise ValueError("approved spec differs from parent commits or sensitivity")
    review.text(spec["source_label"], "source label", 120)
    review.text(spec["purpose"], "purpose", 1000)
    diff.validate_paths(spec["paths"])
    selected = spec["selections"]
    if not isinstance(selected, list) or not 1 <= len(selected) <= 20:
        raise ValueError("approve 1 to 20 source ranges")
    for item in selected:
        diff.selection(item)
        if item["path"] not in spec["paths"]:
            raise ValueError("approved selection is outside explicit path allowlist")
    if len({(s["version"], s["id"]) for s in selected}) != len(selected):
        raise ValueError("duplicate approved selection")
    if any(not covers(selected, s) for s in request["selections"]):
        raise ValueError("requested source is not covered by the explicit approved spec")
    return spec


def response_observation(request):
    return {"context_response": {"schema": RESPONSE_SCHEMA,
            "parent_context_id": request["context_id"],
            "parent_directory_key": request["context_directory_key"],
            "request_sha256": demo.digest(demo.canonical(request)),
            "base_commit": request["base_commit"], "head_commit": request["head_commit"]}}


def preview_response(repository, parent, pins, request, spec, output):
    if output.exists() or output.is_symlink():
        raise FileExistsError("use a new output directory")
    manifest = validate_request(request, parent, pins)
    # Check explicit operator approval before reading any supplementary Git blob.
    validate_approval(spec, request, manifest)
    diff.verify_git_source(repository, parent, pins["content_id"])
    observation = response_observation(request)
    with tempfile.TemporaryDirectory(prefix="context-response-") as name:
        observation_path = Path(name) / "observation.json"
        observation_path.write_bytes(demo.canonical(observation))
        summary, files = plan.preview(repository, spec, observation_path)
    output.mkdir(parents=True, mode=0o700)
    for name, value in (("request.json", request), ("spec.json", spec),
                        ("observation.json", observation), ("preview.json", summary)):
        (output / name).write_bytes(demo.canonical(value))
    (output / "PREVIEW.md").write_text(plan.preview_markdown(summary), encoding="utf-8")
    for name, raw in files.items():
        target = output / "context" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return summary


def verify_response(parent, parent_pins, request, supplement, supplement_pins):
    manifest = validate_request(request, parent, parent_pins)
    pins = checked_pins(supplement_pins)
    response, selections = diff.verify_context(supplement, pins["content_id"])
    if (any(response[v + "_commit"] != manifest[v + "_commit"] for v in diff.VERSIONS)
            or response["sensitivity"] != manifest["sensitivity"]):
        raise ValueError("supplement differs from parent commits or sensitivity")
    observation = review.read_json(supplement / "observation.json", demo.MAX_BYTES)
    if observation != response_observation(request):
        raise ValueError("supplement does not bind the exact request and parent")
    if any(not covers(selections.values(), s) for s in request["selections"]):
        raise ValueError("supplement does not cover the requested source")
    return {"parent_context_id": parent_pins["content_id"],
            "supplement_context_id": pins["content_id"],
            "request_sha256": demo.digest(demo.canonical(request)),
            "request_binding_verified": True, "received_code_executed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "verify"))
    for name in ("parent-context", "parent-pins", "request"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source", "approved-spec", "output", "context", "pins"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    required = ("source", "approved_spec", "output") if args.mode == "plan" else ("context", "pins")
    if any(getattr(args, name) is None for name in required):
        parser.error("missing arguments for " + args.mode)
    try:
        os.umask(0o077)
        pins = review.read_json(args.parent_pins)
        request = review.read_json(args.request, REQUEST_LIMIT)
        if args.mode == "plan":
            preview_response(args.source, args.parent_context, pins, request,
                             review.read_json(args.approved_spec), args.output)
            print("PASS: unsigned approved supplement preview; inspect all exposed source before signing")
        else:
            verify_response(args.parent_context, pins, request, args.context, review.read_json(args.pins))
            print("PASS: supplement binds the exact request and parent; not Git or signature proof")
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("Context request failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
