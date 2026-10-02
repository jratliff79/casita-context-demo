# Review a pinned change with base/head citations

`git_diff_review.py` packages a caller's explicitly allowlisted paths at two
immutable Git commits. It includes a derived unified diff, original blob hashes
and selected surrounding source ranges. A static report can cite either version
or both. The final verifier checks the diff and each selection against the
original Git blobs, then verifies the returned report against those selections.
It uses the existing signed input/result transport, with fresh stores and
the same 1,000,000-byte restored-file and archive budgets.

## Run the public PR example

Use the [host prerequisites](../README.md#prerequisites): Python 3.9+, default
Casita CLI, Git and OpenSSH `ssh-keygen -Y`. From a full clone of this demo:

```sh
python3 git_diff_example.py --source . \
  --casita /path/to/casita --output output/git-diff-example
```

For a shallow clone, first fetch both public source commits:

```sh
git fetch origin 5cc171423d293ad5ca6b2e5323718d4f699e1136
git fetch origin 002fe4d138a4b17f972dec7639ddda5a4eb62d70
```

Optional uv runner, with the same Casita and OpenSSH prerequisites:

```sh
uv run --no-project --python 3.12 python git_diff_example.py --source . \
  --casita /path/to/casita --output output/git-diff-example-uv
```

This uses public MIT source from
[PR #15](https://github.com/jratliff79/casita-context-demo/pull/15): base
`5cc171423d293ad5ca6b2e5323718d4f699e1136` and PR head
`002fe4d138a4b17f972dec7639ddda5a4eb62d70`. The
[source fixture](../fixtures/git-diff-source.json) independently allowlists both
versions' full blob hashes for `git_review.py`, `review_handoff.py` and `LICENSE`.
Only those three paths are packaged. The five source ranges include the two
changed functions at both versions and the license. Other PR files are omitted.

The return is a **scripted synthetic citation control**, describing the already
merged size guard. It is not a new defect finding or an AI review. The example
verifies two exact citations, one from each version, and rejects six signed
reports with wrong base/head commits, swapped source version, invented excerpt,
wrong blob hash and uncaptured lines. A seventh control signs a rewritten diff:
signature and context checks succeed, but original-Git verification rejects it.

Expected final output:

```text
PASS: public base/head diff, signed scripted return, versioned citations and rejection controls
```

The receipt marks `scripted_report: true`, `fresh_ai_review: false`,
`received_code_executed: false`, `patch_applied: false` and
`execution_attested: false`. The source snapshot is public, not synthetic.
Each run removes its throwaway private keys, including on role failure.
Generated archives, reports, trust files and raw receipts stay in ignored
`output/`; receipts may include local paths. CI uploads no raw artifacts.

## Specify a change and its surrounding source

The generic CLI accepts a spec using the shape below. Replace placeholders with
full 40-character Git commit IDs and real source line ranges. Keep the spec
outside tracked public examples when using private source.

```json
{
  "schema": "casita-context-demo.git-diff-spec.v1",
  "source_label": "explicit source label",
  "base_commit": "<full base commit>",
  "head_commit": "<full head commit>",
  "sensitivity": "restricted",
  "purpose": "Review the supported behavior change and state missing context.",
  "paths": ["src/example.py"],
  "selections": [
    {"version": "base", "id": "example", "path": "src/example.py", "start": 1, "end": 20},
    {"version": "head", "id": "example", "path": "src/example.py", "start": 1, "end": 25}
  ]
}
```

There are 1 to 20 explicit paths and 1 to 20 selected ranges, with at most 400
lines per range. IDs must be unique within a version; the same ID can occur at
base and head. Every selection must belong to an allowlisted path. For an added
or deleted file, select ranges only from the version where the file exists.
At least one allowlisted file must change. Unchanged helpers can be included
explicitly. Renames require listing both old and new paths; they appear as a
deletion and an addition. Regular UTF-8 Git files are supported; symlinks,
submodules, directories, invalid UTF-8 and NUL-containing binary files are rejected.

**The diff can reveal more than the selected ranges.** It includes every changed
hunk in each allowlisted file, with three lines of surrounding context. Added
and deleted files appear in full. Approve the entire selected files' diff and
metadata before sharing. This CLI does not redact secrets or scan confidentiality.
Dirty and untracked files are excluded; paths and source labels are explicit.

The diff is generated with Python's standard-library unified diff from the
immutable blobs, without Git diff drivers, textconv, external tools or hooks.
LF defines source lines. The diff preserves CRLF and records missing final
newlines; citation excerpts use the existing source-range normalization of
CRLF to LF. Mode-only changes appear in `changes.json` blob metadata with an
empty textual diff. It does not claim to reproduce every Git diff rendering.

## Signed roles and report shape

Follow the [Git review guide](git-review.md#receive-review-and-return) to
provision signer trust independently. The input and result signature namespaces
are unchanged. The four roles accept the same arguments:

```sh
python3 git_diff_review.py prepare --casita /path/to/casita \
  --source /path/to/repository --spec input/diff-spec.json \
  --signing-key /path/to/sender-key --output output/diff-sender

python3 git_diff_review.py receive --casita /path/to/casita \
  --archive input/handoff.casitar --pins input/pins.json --signature input/pins.sig \
  --allowed-signers /path/to/trusted-input-signers --signer expected-sender \
  --output output/diff-receiver

python3 git_diff_review.py return --casita /path/to/casita \
  --context output/diff-receiver --report input/report.json \
  --signing-key /path/to/return-key --output output/diff-return

python3 git_diff_review.py verify --casita /path/to/casita \
  --source /path/to/repository --original output/diff-sender \
  --archive returned/handoff.casitar --pins returned/pins.json --signature returned/pins.sig \
  --allowed-signers /path/to/trusted-return-signers --signer expected-return-signer \
  --output output/diff-verified
```

Use a new output directory per command. Optional `prepare --observation` captures
an explicit JSON object as sender-supplied data. The receiver checks the signature
before import and verifies context hashes, schema and task bytes. It leaves
`original_git_verified` false because it has no Git objects. The final verifier
needs both original commits and the sender's trusted original output directory;
do not replace that directory with recipient-supplied state.

The report schema is `casita-context-demo.git-diff-report.v1`. Use the existing
[report fields and finding rules](git-review.md#receive-review-and-return),
replace `source_commit` with `base_commit` and `head_commit`, and add `version`
to each citation:

```json
{
  "version": "head",
  "selection_id": "example",
  "path": "src/example.py",
  "blob_sha256": "<head selection full blob SHA256>",
  "line_start": 10,
  "line_end": 11,
  "excerpt": "<exact head lines joined with LF, no final newline>"
}
```

An empty findings list is valid. Findings can cite both versions, but each
citation covers at most ten captured lines in one version. A base hash or
excerpt cannot stand in for different head content. A signature establishes
the signing key and namespace, not whether a derived diff or finding is true.
The workflow executes no received code, applies no patches, invokes no model
and supplies no OS sandbox, execution attestation or freshness proof. Independent
reproduction and maintainer review remain separate steps.
