# A capsule-only review of a public diff

One fresh Codex reviewer read a verified base/head capsule for
[PR #16](https://github.com/jratliff79/casita-context-demo/pull/16), the change
that introduced versioned Git-diff review. The source is public MIT code from
this repository at base `ceccb912d06a4320c68b0eb81ac2a714263b5ae0` and PR head
`0e01f69d661837eb5073fde11a1c7fd03d3702b3`.

The capsule includes ten explicitly allowlisted paths and eleven source ranges,
covering 2,229 selected source lines. It contains the diff implementation,
base/head versions of its changed Git helper, head versions of transport helpers,
tests, the guide, the scripted public example and its fixture, and the license.
The [source fixture](../fixtures/diff-review-source.json) records each path's
whole-blob SHA256 at each commit, including explicit absence of newly added files
at the base. Other PR paths and unselected source were not supplied.

The reviewer received only the verified `context/` and input pins, with
instructions to read that evidence as data and execute no received code. It
was instructed to use neither a repository checkout nor Git objects; no earlier
findings or expected defects were supplied. This was
an instruction limit, not an OS-enforced sandbox. The reviewer did not independently
inspect Git objects or repeat the controller's signature and source checks.

## The actual result

The [unchanged recorded report](diff-review-report.json) has **zero findings**.
It states missing context and expressly limits its conclusion: no actionable
correctness defect was established from the supplied evidence. That does not
establish runtime correctness, review completeness or a measured review quality.

The local controller returned the report using `git_diff_review.py return`,
then verified the result signature, archive/root identities, original context,
both Git snapshots and the derived diff using `git_diff_review.py verify`.
There were **zero finding citations** to verify, and no finding to reproduce.
The return signature identifies the controller's key; it does not independently
attest the model's identity or review behavior.

The original context bindings were:

```text
content_id: 447199974f6217e55b5c8ded266234c82b0fdaa3fd70621e610ea95ffced2839
directory_key: casita.directory.v1:2i0z4ICgIjCqAOZQ43UaEbcohBm-tafz0cjHdOMsj2E
```

This exercises the real reviewer workflow and an empty signed return. It is
separate from the [previous reproduced size finding](git-review-case.md).

## Replay without a model

Use the [host prerequisites](../README.md#prerequisites): Python 3.9+, default
Casita CLI, Git and OpenSSH `ssh-keygen -Y`. From a full clone of this demo:

```sh
python3 git_diff_replay.py --source . \
  --casita /path/to/casita --output output/recorded-diff-review
```

If your clone is shallow, first fetch the public source commits:

```sh
git fetch origin ceccb912d06a4320c68b0eb81ac2a714263b5ae0
git fetch origin 0e01f69d661837eb5073fde11a1c7fd03d3702b3
```

Optional uv runner:

```sh
uv run --no-project --python 3.12 python git_diff_replay.py --source . \
  --casita /path/to/casita --output output/recorded-diff-review-uv
```

The replay verifies the allowlist against immutable Git objects before creating
artifacts, reconstructs the same observation and capsule, generates throwaway
keys, and runs the four signed roles in fresh stores. It returns the recorded
report unchanged. The source fixture also pins the recorded report's SHA256;
even a schema-valid, context-bound edited report is rejected before artifact
creation. `--report` can select another path containing those exact bytes.
The verified byte snapshot is passed onward without reopening the selected path.
It invokes no model, executes no received code and applies no
patch. It verifies the transport bindings, not the historical reviewer session.

Expected final output:

```text
PASS: public base/head diff, signed recorded return, versioned citations and rejection controls
```

The receipt marks `recorded_ai_report: true`, `scripted_report: false`,
`fresh_ai_review: false`, `finding_count: 0` and `citation_count: 0`.
`citations_verified: true` means that the validator accepted every citation
present; this report contains none. It is not evidence of nonempty citation
coverage. Quality, execution attestation and OS sandbox flags remain false.

The seven deliberately invalid controls are separately scripted; they do not
insert findings into the actual report. They reject wrong base/head commits,
swapped version, invented excerpt, wrong blob hash, uncaptured lines and a
signed rewritten diff that fails original-Git verification. The controls use
a valid captured base citation as their starting point.

Each run removes its throwaway private keys, including when a role fails. Raw
archives, restored source, trust files and command receipts remain under ignored
`output/`; command output may contain local paths. Only explicitly allowlisted
public licensed source metadata and review text are tracked. CI runs both the
scripted PR #15 example and this recorded PR #16 replay and uploads no raw
review artifacts.
