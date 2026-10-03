# Preview the scope before signing

`git_diff_plan.py` creates a reviewable spec and unsigned context from explicitly
named files at two full Git commit IDs. It suggests citation ranges around
changed lines so you do not have to count every range by hand. It requires only
Python 3.9+ and Git. It creates no Casita store, signing key or archive, and
executes no source code.

From a full clone of this demo, preview the public MIT source used in the
[PR #15 diff example](git-diff-review.md#run-the-public-pr-example):

```sh
python3 git_diff_plan.py --source . \
  --base 5cc171423d293ad5ca6b2e5323718d4f699e1136 \
  --head 002fe4d138a4b17f972dec7639ddda5a4eb62d70 \
  --path git_review.py --path review_handoff.py --path LICENSE \
  --source-label 'Public MIT source from PR 15' \
  --purpose 'Review the report and archive size guards; identify missing context.' \
  --output output/scope-preview
```

For a shallow clone, fetch both named public commits first, as described in the
diff guide. Optional uv works with the same arguments:
`uv run --no-project --python 3.12 python git_diff_plan.py ...`.

The helper compares exactly the two named commits. For a PR, choose its merge
base when the latest base-branch tip includes unrelated changes. It does not
fetch source or infer a PR's base for you.

Open `output/scope-preview/PREVIEW.md`. Its table shows each allowlisted file,
both versions' selected ranges, diff bytes, and whether an added or deleted file
appears in full. Inspect `context/changes.json` for **every changed hunk** and
`context/source/` for the exact selected source. `preview.json` records hashes,
the unsigned context identity, sizes, selections and omitted changed path names.
Undecodable name bytes are escaped for display. The parallel
`omitted_changed_paths_raw_hex` list preserves the exact Git path bytes in the
same order, including when two names share the same escaped display string.
`spec.json` is the input for the existing signed preparation command.

The example suggests six ranges, 230 selected lines and 21,439 unsigned-context
bytes. It omits ten other changed paths. These counts describe this pinned
public example, not a complete review or a performance comparison.

## Adjust the selections

The default adds 20 lines of context around changed lines in both versions.
Use `--context-lines 10` for smaller windows. Overlapping windows merge, and
long windows split into ranges of at most 400 lines. An explicitly named
unchanged helper or a permission-only changed file is suggested in full at
each existing version. Empty files have no source lines to cite.
For a change involving only empty files, explicitly include a nonempty helper
to supply the protocol's required source selection.

Use repeated `--select VERSION:PATH:START:END` options when the mechanical
windows miss a function boundary or include too much. A selection replaces
**all suggestions for that path**; other allowlisted paths still get suggestions.
For example, rerun into a new output directory and add:

```sh
--select base:git_review.py:310:325 \
--select head:git_review.py:310:328
```

Every selection must name an explicitly allowlisted path and an existing version.
Paths and ranges use the [diff protocol's limits](git-diff-review.md#specify-a-change-and-its-surrounding-source):
at most twenty paths, twenty ranges and 400 lines per range. If suggestions
exceed the range limit, the helper rejects the plan with a count and leaves the
output uncreated. The error includes counts per path. It never silently drops
a range to fit. Narrow the paths or
context, or explicitly select the needed surrounding source.

`--observation input/observation.json` includes the same optional sender-supplied
JSON object accepted by the preparation command. Its exact canonical bytes
appear in `context/observation.json` and count toward the context budget.

## Review, then prepare

Review the entire exposed diff and selected source before signing. Use the
[independently provisioned signer](git-review.md#receive-review-and-return) from
the signed Git workflow:

```sh
python3 git_diff_review.py prepare --source . \
  --spec output/scope-preview/spec.json \
  --casita /path/to/casita --signing-key /path/to/sender-key \
  --output output/scope-sender
```

If an observation was previewed, also pass
`--observation output/scope-preview/context/observation.json`. With unchanged
spec and observation bytes, the sender's content ID matches `preview.json`.
Changing either after preview changes the exposure; rerun the preview with the
new choices before signing. The sender independently checks original Git bytes
and the archive byte limit. The preview measures unsigned context bytes, not
archive size.

## Scope and handling

Suggestions are mechanical windows, not an assessment of what a reviewer needs.
Add helpers, callers and tests explicitly. The helper inventories changed path
names outside the allowlist to show omissions, but reads their source bytes only
if you explicitly allowlist them. An omitted path name can itself be sensitive.

Every changed hunk of an allowlisted file is exposed even when outside the
selected citation ranges. Added and deleted files appear in full in the diff.
Dirty and untracked files are excluded. The output defaults to `restricted`,
uses private permissions, and refuses an existing destination, including a
dangling symlink. Keep private specs, previews and contexts outside tracked public
examples. `--sensitivity synthetic` is for synthetic fixtures; a label is a
curator assertion, not an authorization or secret scan.

The helper provides no redaction, secret detection, model call, publication,
sandbox or execution attestation. The final signed return still needs original
Git and citation verification, independent reasoning checks, and any relevant
application tests.
