#!/usr/bin/env python3
"""Preview an explicit Git diff scope and suggest bounded citation ranges."""
import argparse
import difflib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import demo
import git_diff_review as diff
import git_review as review

PREVIEW_SCHEMA = "casita-context-demo.git-diff-preview.v1"


def suggested_ranges(old, new, context_lines):
    """Use LF-based original line numbers; merge overlapping windows per version."""
    windows = {version: [] for version in diff.VERSIONS}
    lines = [diff.diff_lines(raw or b"") for raw in (old, new)]
    matcher = difflib.SequenceMatcher(None, *lines)
    for group in matcher.get_grouped_opcodes(context_lines):
        for version, index in (("base", 1), ("head", 3)):
            start, end = group[0][index], group[-1][index + 1]
            if end > start:
                windows[version].append((start + 1, end))
    # A permission-only change or an explicitly selected unchanged helper needs
    # surrounding source too. Include the full file, with the same range limit.
    if not any(windows.values()):
        for version, source in zip(diff.VERSIONS, lines):
            if source:
                windows[version] = [(1, len(source))]
    result = {}
    for version, spans in windows.items():
        merged = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        result[version] = [(start, min(start + 399, end))
                           for first, end in merged for start in range(first, end + 1, 400)]
    return result


def explicit_selection(value, index):
    try:
        version, rest = value.split(":", 1)
        path, start, end = rest.rsplit(":", 2)
        item = dict(version=version, id="selection-" + str(index), path=path,
                    start=int(start), end=int(end))
    except (ValueError, AttributeError):
        raise ValueError("use --select VERSION:PATH:START:END") from None
    diff.selection(item)
    return item


def plan(repository, base, head, paths, source_label, purpose, sensitivity,
         context_lines=20, selected=()):
    commits = dict(base_commit=base, head_commit=head)
    diff.validate_commits(commits)
    diff.validate_paths(paths)
    review.text(source_label, "source label", 120)
    review.text(purpose, "purpose", 1000)
    if sensitivity not in ("synthetic", "restricted"):
        raise ValueError("invalid sensitivity")
    if type(context_lines) is not int or not 0 <= context_lines <= 100:
        raise ValueError("context lines must be between 0 and 100")
    changes = diff.make_changes(repository, commits, paths)
    selections = [explicit_selection(value, i + 1) for i, value in enumerate(selected)]
    if any(item["path"] not in paths for item in selections):
        raise ValueError("explicit selection is outside the path allowlist")
    overridden = {item["path"] for item in selections}
    for path in paths:
        if path in overridden:
            continue
        _, old = diff.git_file(repository, base, path)
        _, new = diff.git_file(repository, head, path)
        for version, spans in suggested_ranges(old, new, context_lines).items():
            for start, end in spans:
                selections.append(dict(version=version, id="selection-" + str(len(selections) + 1),
                                       path=path, start=start, end=end))
    if not 1 <= len(selections) <= 20:
        counts = ", ".join(path + "=" + str(sum(s["path"] == path for s in selections)) for path in paths)
        raise ValueError("scope needs " + str(len(selections)) + " ranges; limit is 20. "
                         "Per-path ranges: " + counts + ". "
                         "Narrow --path/--context-lines or use --select for that path; no ranges were omitted.")
    # Capture now to reject absent versions or out-of-bounds explicit selections.
    for item in selections:
        diff.capture(repository, commits, item)
    return dict(schema=diff.SPEC_SCHEMA, source_label=source_label, **commits,
                sensitivity=sensitivity, purpose=purpose, paths=paths, selections=selections), changes


def preview(repository, spec, observation=None):
    """Construct the exact unsigned context, including optional observations."""
    with tempfile.TemporaryDirectory(prefix="git-diff-preview-") as name:
        folder = Path(name)
        spec_path = folder / "spec.json"
        spec_path.write_bytes(demo.canonical(spec))
        context = folder / "context"
        content_id = diff.create_context(context, repository, spec_path, observation)
        diff.verify_git_source(repository, context, content_id)
        files = {p.relative_to(context).as_posix(): p.read_bytes()
                 for p in context.rglob("*") if p.is_file()}
    size = sum(len(raw) for raw in files.values())
    if size > demo.MAX_BYTES:
        raise ValueError("unsigned context exceeds the restored-file byte budget")
    changes = review.parse_json(files["changes.json"])
    # Inventory only repository path names outside the explicit source allowlist.
    names = review.git(repository, "diff-tree", "--no-commit-id", "--name-only", "-r",
                       "--no-renames", "-z", spec["base_commit"], spec["head_commit"])
    changed_paths = sorted(name for name in names.split(b"\x00") if name)
    allowed = {path.encode("utf-8") for path in spec["paths"]}
    omitted = [name for name in changed_paths if name not in allowed]
    selected = [review.parse_json(raw) for path, raw in files.items() if path.startswith("source/")]
    summary = dict(schema=PREVIEW_SCHEMA, base_commit=spec["base_commit"], head_commit=spec["head_commit"],
                   sensitivity=spec["sensitivity"], sharing="local-only", content_id=content_id,
                   paths=spec["paths"], selections=spec["selections"],
                   changed_path_count=len(changed_paths),
                   omitted_changed_paths=[name.decode("utf-8", errors="backslashreplace") for name in omitted],
                   omitted_changed_paths_raw_hex=[name.hex() for name in omitted],
                   selected_line_count=sum(len(item["lines"]) for item in selected),
                   context_bytes=size, changes_json_bytes=len(files["changes.json"]),
                   files=[dict(path=item["path"], base=item["base"], head=item["head"],
                               diff_utf8_bytes=len(item["diff"].encode("utf-8")),
                               full_added_or_deleted_file=item["base"] is None or item["head"] is None)
                          for item in changes["files"]],
                   observation_included="observation.json" in files,
                   original_git_verified=True, signed=False, received_code_executed=False,
                   casita_store_created=False, publication_performed=False,
                   warnings=["All changed hunks in each allowlisted file are included, even outside citation ranges.",
                             "Added and deleted files appear in full in the diff. Unchanged helper suggestions include the full file.",
                             "Omitted changed paths are display metadata, not source. Raw hex preserves exact path bytes.",
                             "Range suggestions are mechanical windows, not proof of sufficient review context.",
                             "This preview performs no secret scan, signing, Casita import, publication or source execution.",
                             "The archive byte limit is checked separately when preparing the signed handoff."])
    return summary, files


def preview_markdown(summary):
    lines = ["# Unsigned Git diff scope preview", "", "Base: " + summary["base_commit"],
             "Head: " + summary["head_commit"], "", "Sensitivity: " + summary["sensitivity"] + "; local only.",
             "", str(len(summary["selections"])) + " ranges; " + str(summary["selected_line_count"]) +
             " selected lines; " + str(summary["context_bytes"]) + " exact unsigned-context bytes.", "",
             "| Path | Base ranges | Head ranges | Diff bytes | Full added/deleted file |",
             "| --- | --- | --- | --- | --- |"]
    for item in summary["files"]:
        ranges = []
        for version in diff.VERSIONS:
            spans = [str(s["start"]) + "-" + str(s["end"]) for s in summary["selections"]
                     if s["path"] == item["path"] and s["version"] == version]
            ranges.append(", ".join(spans) or "none")
        lines.append("| " + " | ".join([item["path"], *ranges, str(item["diff_utf8_bytes"]),
                                         "yes" if item["full_added_or_deleted_file"] else "no"]) + " |")
    lines += ["", str(len(summary["omitted_changed_paths"])) + " changed paths are omitted. Their names are in preview.json.",
              "", "Inspect context/changes.json and context/source/ for the exact captured evidence before signing.",
              "Optional observation included: " + ("yes" if summary["observation_included"] else "no") + ".", ""]
    lines += ["- " + warning for warning in summary["warnings"]]
    return "\n".join(lines) + "\n"


def run(args):
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError("use a new output directory")
    spec, _ = plan(args.source, args.base, args.head, args.path, args.source_label,
                   args.purpose, args.sensitivity, args.context_lines, args.select)
    summary, files = preview(args.source, spec, args.observation)
    # Exclusively reserve the destination only after validating the complete plan.
    args.output.mkdir(parents=True, mode=0o700)
    (args.output / "spec.json").write_bytes(demo.canonical(spec))
    (args.output / "preview.json").write_bytes(demo.canonical(summary))
    (args.output / "PREVIEW.md").write_text(preview_markdown(summary), encoding="utf-8")
    for name, raw in files.items():
        target = args.output / "context" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    print(f"PREVIEW: {len(spec['paths'])} paths; {len(spec['selections'])} ranges; "
          f"{summary['selected_line_count']} lines; {summary['context_bytes']} context bytes")
    print(f"Omitted changed paths: {len(summary['omitted_changed_paths'])}; inspect PREVIEW.md, preview.json and context/changes.json.")
    print("Review the exact context before passing spec.json to git_diff_review.py prepare.")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("base", "head", "source-label", "purpose"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--select", action="append", default=[], metavar="VERSION:PATH:START:END")
    parser.add_argument("--sensitivity", choices=("restricted", "synthetic"), default="restricted")
    parser.add_argument("--context-lines", type=int, default=20)
    parser.add_argument("--observation", type=Path)
    args = parser.parse_args()
    try:
        os.umask(0o077)
        run(args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("Git diff preview failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
