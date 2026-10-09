# Selected Git source review

`git_review.py` prepares a signed capsule from explicitly selected lines at a
full Git commit. A recipient can read those lines without repository access,
return a signed static report, and let the sender verify each citation against
the original Git objects. It uses the existing Casita transport and OpenSSH pin
signatures. It does not invoke a model or execute received source.

To review an explicitly selected change across two commits, use the
[Git diff review guide](git-diff-review.md). It adds a derived unified diff and
base/head citation labels to the same signed handoff roles.

For a real public-source use of this command, see the
[selected-Git review case](git-review-case.md). It records one fresh capsule-only
review, verified citations, an independent bug reproduction and a model-free
replay. Outgoing restored files and Casitar archives must each fit the receiver's
1,000,000-byte budget. Canonical JSON escaping and archive framing count toward
those limits; oversized output fails before pins are signed.

The [public docs checkpoint pilot](docs-checkpoint-case.md) uses the same roles
to carry selected documentation plus visible task state to a fresh reviewer.
Its replay is available with `git_review_replay.py --case docs-checkpoint`.

The existing [public review example](review-capsule.md) remains pinned to its
allowlisted snapshot. This command accepts a caller-selected repository and
specification; it does not automatically discover files, repository URLs, logs,
credentials or working-tree changes.

## Run the synthetic example

Use the [prerequisites](../README.md#prerequisites): Python 3.9 or newer, Git,
OpenSSH with `ssh-keygen -Y`, and the tested Casita CLI. Python uses only its
standard library. Optionally use uv to select Python 3.12:

```sh
uv run --no-project --python 3.12 python git_review_example.py \
  --casita /path/to/casita --output output/git-review-example
```

Or run `python3 git_review_example.py` with the same arguments. Use a new output
directory each time. The example creates a tiny local Git repository, packages
two selected ranges and a fabricated CI observation, signs its pins, restores
the capsule into a fresh store, and returns a scripted synthetic finding through
another store. It verifies the return against the original Git objects.

The fixture deliberately changes a tracked file after committing it and adds an
untracked note. Neither enters the capsule. Five correctly signed invalid reports
are rejected: a wrong context, wrong commit, invented excerpt, wrong blob hash,
and a line outside the captured selection. Modified input pins are rejected
before a receiver store is initialized. Throwaway private signing keys are
removed even if the example fails.

Inspect `output/git-review-example/receipt.json` and the role receipts. The
returned report is in `verified-return/result/report.json`; input selections
are JSON files in `receiver/context/source/`. The generated task contains the
report fields, exact citation rules and string priority enum.

This is a protocol check with a known synthetic omission, not a fresh AI review,
execution proof or performance measurement. No source from another project is
used by the example.

## Prepare an explicitly selected context

Create a specification with this shape. Replace the commit placeholder with a
full lowercase 40-character Git commit present in the local repository; supply
paths and original line ranges from that commit:

```json
{
  "schema": "casita-context-demo.git-review-spec.v1",
  "source_label": "local-project",
  "commit": "<full Git commit>",
  "sensitivity": "restricted",
  "purpose": "Review the selected classifier and inventory for a mismatch.",
  "selections": [
    {"id": "classifier", "path": "classifier.py", "start": 3, "end": 6},
    {"id": "inventory", "path": "pages.json", "start": 1, "end": 5}
  ]
}
```

The source label and purpose are explicit metadata. Avoid names or details you
do not intend to share. Sensitivity is `restricted` or `synthetic`; every context
has `sharing: local-only`. These are labels, not encryption or access control.
Choose 1 to 20 unique selection IDs with at most 400 lines each. Paths must be
relative, normalized ASCII paths. Source objects must be regular UTF-8 Git
files; links, submodules and blobs larger than 1,000,000 bytes are rejected.
The complete restored context is also limited to 1,000,000 bytes.

Use your separately provisioned signing key:

```sh
python3 git_review.py prepare --casita /path/to/casita \
  --source /path/to/repository --spec output/spec.json \
  --signing-key /path/to/sender-key --output output/git-sender
```

Optionally add `--observation output/observation.json` for a JSON object containing
curated observations. Its exact content is hashed with the context, but Git
verification only covers the source selections. A signed observation is still
sender-supplied data, not authenticated runtime execution. Packaging does not
redact secrets. Review the selected bytes and metadata before sharing anything.

Source capture reads immutable Git blobs, with Git replacement objects disabled
and Git's repository-local environment variables cleared so `--source` selects
the intended repository. Line numbers count LF boundaries; CRLF endings are
normalized by removing the CR before LF. Other separator characters inside a
line remain data and do not create extra source lines.
It never includes dirty or untracked checkout files. The sender receipt records
the Casita binary hash, commands, signed pins and original Git verification.

## Receive, review and return

Transfer `handoff.casitar`, `pins.json` and `pins.sig` through your chosen private
channel. Provision the recipient's `allowed_signers` independently, as described
in the [signed handoff guide](signed-handoff.md). Use the namespace
`casita-context-demo.review-input-pins.v1` for the sender and
`casita-context-demo.review-result-pins.v1` for the return signer. The synthetic
example's throwaway keys simulate this setup; they are not production identity.

```sh
python3 git_review.py receive --casita /path/to/casita \
  --archive input/handoff.casitar --pins input/pins.json --signature input/pins.sig \
  --allowed-signers /path/to/trusted-input-signers --signer expected-sender \
  --output output/git-receiver
```

Only give the reviewer the restored `context/` directory and the verified pins
from `input-pins.json`. The signature is checked before archive import; the
receiver then checks archive identity, root identity, file hashes, context
schema and task bytes. The receiver does **not** have the original Git objects,
so its receipt leaves `original_git_verified` false. Configure any reviewer
access restrictions separately: this CLI is not an OS sandbox.

Write the report outside the context directory. A report has this shape; copy
the actual binding values from the verified pins and manifest:

```json
{
  "schema": "casita-context-demo.git-review-report.v1",
  "context_id": "<verified content_id>",
  "context_directory_key": "<verified directory_key>",
  "source_commit": "<manifest commit>",
  "reviewer": "descriptive reviewer label",
  "scope": "static review; no received code executed",
  "findings": [{
    "title": "Inventory page omitted from classifier",
    "body": "Explain the supported mismatch and proposed correction.",
    "priority": "P2",
    "citations": [{
      "selection_id": "classifier",
      "path": "classifier.py",
      "blob_sha256": "<selection blob_sha256>",
      "line_start": 3,
      "line_end": 3,
      "excerpt": "PERFORMANCE_PAGES = {\"HomePage\", \"PricingPage\"}"
    }]
  }],
  "limitations": ["Static review of selected lines; no tests executed."]
}
```

Priority must be a string: `P0`, `P1`, `P2` or `P3`. Empty findings are valid.
Each finding needs 1 to 8 citations, each covering at most 10 original lines
within one captured selection. Excerpts join the exact selected lines with
newlines and omit the final newline. The full-blob hash identifies the original
file, while only the selected lines are shared. Include at least one limitation.
The report supports at most 20 findings and 10 limitations.

```sh
python3 git_review.py return --casita /path/to/casita \
  --context output/git-receiver --report output/report.json \
  --signing-key /path/to/return-key --output output/git-return
```

The return signer signs the report pins. A local controller can perform this
step on behalf of a model; such a signature identifies the controller's key,
not an independently attested model identity. Return validation checks citations
against the packaged selections and does not judge whether the reasoning is
correct.

## Verify against the sender's source

Keep the sender's original `git-sender/` directory as trusted local state.
Do not accept a replacement of this directory from the recipient. The source
repository must retain the original commit and blobs; its current checkout can
be dirty or on a different branch.

```sh
python3 git_review.py verify --casita /path/to/casita \
  --source /path/to/repository --original output/git-sender \
  --archive returned/handoff.casitar --pins returned/pins.json \
  --signature returned/pins.sig --allowed-signers /path/to/trusted-return-signers \
  --signer expected-return-signer --output output/git-verified
```

Verification checks the original selected lines and whole-blob hashes against
the original Git objects, verifies the return signature and archive, then binds
the report and exact citations to the original context. A valid signature cannot
authorize an invented excerpt or a report for another context.

Output directories are created with mode 0700 and existing outputs are preserved.
All artifacts remain local; the CLI never pushes a repository, posts a report
or uploads an archive. Archives themselves are not encrypted. Keep real source
and observations out of public examples, CI logs and uploaded build artifacts.

`original_git_verified` and `citations_verified` establish source correspondence.
They do not establish execution, current production behavior, finding quality,
sender authorization to share the data or a sandbox. Independent reproduction
and maintainer review remain separate steps.
