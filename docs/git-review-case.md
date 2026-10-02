# A selected-Git capsule review and a reproduced bug

A fresh Codex reviewer read seven verified selections from this public MIT
repository at commit `5cc171423d293ad5ca6b2e5323718d4f699e1136`. The capsule
contained the merged Git handoff tool, its directly used helpers, its tests and
guide, and the MIT license: 1,429 source lines and 83,452 original source bytes.
The reviewer received the verified capsule and input pins, with instructions to
read only those files, treat source as data, and execute no received code.
This was an instruction boundary, not an enforced OS sandbox.

The [recorded report](git-review-report.json) contains one P2 finding and six
exact citations. The local controller returned it through `git_review.py return`
and verified its detached signature, source context, original Git blobs and
all six excerpts through `git_review.py verify`. The return signature identifies
the controller; it does not independently attest the model's identity.

## What the reviewer found

The input report could fit the byte budget while canonical JSON escaping made
the outgoing report too large to receive. The reviewer proposed a concrete
counterexample: one source line containing 10,000 U+0080 characters, cited in
each of 20 findings. The reviewer performed static analysis only.

A separate local reproduction used trusted demo code and a disposable synthetic
Git repository. It confirmed these sizes with the tested Casita binary:

| Artifact | Bytes |
| --- | ---: |
| Literal UTF-8 input report | 406,190 |
| Canonical report | 1,205,797 |
| Casitar archive | 1,206,353 |
| Receiver limit | 1,000,000 |

The old sender reported success and signed pins for that oversized archive.
The receiver's bounded archive read rejected it. The regression test constructs
the same valid citation pattern and checks that the fixed return command rejects
canonical overflow before creating result files or running Casita. The shared
export helper also checks restored-file totals before initialization and archive
size before verification or signing. A separate test covers archive framing
that exceeds the transport limit even when the file payload fits.

This is one reproduced correctness finding. It does not measure review accuracy,
token savings, review completeness or sandbox effectiveness. Identity and exact
citations alone do not prove a finding is correct.

## Replay the recorded handoff

Use the [host prerequisites](../README.md#prerequisites): Python 3.9+, default
Casita CLI, Git and OpenSSH `ssh-keygen -Y`. Run from the demo repository root.
An ordinary clone contains the pinned source commit; no separate source checkout
is necessary. If yours is shallow, fetch that public commit first:

```sh
git fetch origin 5cc171423d293ad5ca6b2e5323718d4f699e1136
python3 git_review_replay.py --source . \
  --casita /path/to/casita --output output/public-git-review
```

Optional uv runner, with the same Casita and OpenSSH prerequisites:

```sh
uv run --no-project --python 3.12 python git_review_replay.py --source . \
  --casita /path/to/casita --output output/public-git-review-uv
```

The replay first compares every selected Git blob against the public source
allowlist in [the source fixture](../fixtures/git-review-source.json). It reads
immutable Git objects, not working-tree edits or untracked files. The label
`restricted` in the specification keeps the generic command's publication
behavior local even though this explicitly allowlisted snapshot is public.

It generates fresh throwaway signing keys and four fresh Casita stores, prepares
and receives the source capsule, returns the unchanged recorded report, and
verifies it against the original Git source. It also signs five deliberately
invalid synthetic reports and requires rejection for wrong context, wrong
commit, invented excerpt, wrong blob hash and uncaptured lines. Those controls
are separate from the actual reviewer finding.

Expected final output:

```text
PASS: recorded public Git review, signed return, original Git citations and rejection controls
```

The replay does not invoke an AI model or execute received code. Its receipt
records `fresh_ai_review: false`, `review_quality_verified: false`,
`execution_attested: false` and `os_sandbox_enforced: false`. It verifies artifact
bindings and citations, not the historical reviewer session or the finding's
runtime behavior. Run the regression suite to exercise the size rejection:

```sh
python3 -m unittest discover -s tests -v
```

Each run removes its throwaway private keys, including when a role fails. Raw
receipts, archives, restored source and generated public trust files remain
under ignored `output/`; raw command output may contain local paths. The tracked
allowlist and report contain only public licensed source and review text.
CI runs the replay against the immutable public snapshot and uploads no raw
review artifacts.
