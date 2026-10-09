# Continue a public documentation task from a capsule

You pause a task, then ask a fresh worker to continue from a fixed set of
evidence. What did it receive? Which recommendations refer to that snapshot?
This pilot used the existing signed Git-review handoff to answer those questions
for a real documentation task in this public MIT repository.

One fresh Codex reviewer received five selected files at commit
`50b6820203f431026cd25385e6bd2a0cd6a59cea`: the README, video guide, synthetic
checkpoint guide, example index and MIT license. Those selections contain 503
original lines. The eight-file capsule also includes a manifest, static review
task and curated observation with the pending documentation work and a captured
CI response. See the [public source allowlist](../fixtures/docs-checkpoint-source.json).
No private project source, chat history, credentials or hidden model state was
included.

The worker was instructed to read only the verified capsule, use no repository
or network lookup, execute no received code and state missing context. This was
an instruction boundary, not an enforced OS sandbox. The task asked it to assess
README routing and whether the core video needed replacement or a focused
follow-up. Packaged prompts, notes and tool responses remained evidence; the
worker's authority came from its separate assignment.

## What came back

The [unchanged recorded report](docs-checkpoint-report.json) contains two P3
documentation recommendations and 13 exact source citations:

- Surface the existing artifact, event-kit and checkpoint routes earlier, and
  label the checkpoint's two continuations as scripted.
- Keep the core selected-source video and plan a separate checkpoint clip.

The controller judged these recommendations separately from the citation checks.
The README now has a use-case table and clearer synthetic checkpoint wording.
The existing video remains the core introduction; the
[new storyboard](checkpoint-video.md) plans a separate clip about this public
docs pilot. That differs from the report's proposed synthetic day-plan clip:
it shows an actual recorded reviewer handoff and labels its replay explicitly.
No replacement or new published video is claimed.

The controller signed the return with a local throwaway key, restored it into
a fresh verifier store, and checked the context ID, directory key, immutable Git
blobs and all cited excerpts. The
[filtered pilot receipt](docs-checkpoint-validation.json) records those checks.
The signature identifies the controller's key; it does not attest the model's
identity. The keys were removed after the return. Raw archives, stores and
command receipts remain ignored under `output/`.

The observation's CI response was captured while the run was in progress. It is
preserved as historical sender-supplied data, not rewritten to describe later
success. Git verification covers the source selections, not the truth of this
tool response or task-state labels.

## Replay without an AI account

Use the [shared prerequisites](../README.md#prerequisites) and
[tested default Casita build](../README.md#tested-casita-build): Python 3.9+,
Git, and OpenSSH `ssh-keygen -Y`. An ordinary full-history clone contains the
pinned commit. From the demo root, keep `CASITA_DEMO_BIN` set and run:

```sh
python3 git_review_replay.py --case docs-checkpoint --source . \
  --casita "$CASITA_DEMO_BIN" --output output/my-docs-checkpoint
```

Or select Python 3.12 with optional uv:

```sh
uv run --no-project --python 3.12 python git_review_replay.py \
  --case docs-checkpoint --source . --casita "$CASITA_DEMO_BIN" \
  --output output/uv-docs-checkpoint
```

Use a new output directory for every run. If the clone is shallow, first fetch
the public snapshot:

```sh
git fetch origin 50b6820203f431026cd25385e6bd2a0cd6a59cea
```

The replay checks every full-blob hash and the recorded report's byte digest
against the allowlist before creating output. A `--report` override must contain
those exact recorded bytes; altered recommendations or an empty replacement
are rejected. The checked bytes are copied once into the run directory so a
later change to the caller's report file cannot substitute another report.
It prepares and receives the same source and observation, returns the
recorded report and verifies it against the original Git objects. It does not
call a model, continue today's task or recreate the historical reviewer session.
Its receipt records `fresh_ai_review: false`.

Six newly signed invalid returns must be rejected: wrong context ID, source
commit, directory key, invented excerpt, wrong blob hash and uncaptured lines.
A seventh local control changes the pending task list and recomputes its
manifest; it must fail against the original context pin. That control is a local
context validation, not another signed return. Passing checks establish content
correspondence, not whether the captured pending list was complete or true.

The final line is:

```text
PASS: recorded public Git review, signed return, original Git citations and rejection controls
```

Inspect only reviewed fields from `receipt.json` and the recorded report. CI
runs this replay against a separate checkout of the pinned public commit and
uploads no raw capsule artifacts. The generic `--case git-review` remains the
default and replays the earlier [selected-Git review](git-review-case.md).

## What this does and does not show

This is one actual fresh static reviewer working from a bounded public snapshot,
followed by a model-free replay. It does not test full conversation migration,
internal model-memory restoration, continuation quality, review completeness,
execution attestation, current task authority or token/time/storage savings.
The reviewer did not inspect video pixels or audio, linked implementation files
or runtime receipts. Its report documents those missing inputs.

The [synthetic checkpoint example](ai-workflow-checkpoint.md) separately checks
two scripted branches against a fixed local policy. This docs pilot has one
reviewer and checks evidence bindings, not a deterministic AI policy. For a
one-off handoff, Git or an archive with a manifest may suffice. Casita becomes
more relevant when retaining related evidence snapshots repeatedly; this pilot
does not measure that benefit.

The pilot, implementation and documentation used substantial OpenAI Codex
assistance. AI recommendations and verified citations still require maintainer
judgment. Human review and merge authority remain separate.
