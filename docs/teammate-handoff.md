# Share task context and proposed team knowledge

You finish investigating a task and a teammate needs to continue it with their
own assistant. Send the selected evidence, what you learned and what remains to
do. Keep their returned advice separate from accepted team knowledge until the
owner reviews it.

This example gives Alice, Bob and Carol separate Casita stores. It transports
the same synthetic task snapshot to Bob and Carol, returns two competing
source-cited proposals, and creates a new snapshot from an explicit owner
selection. It uses fixed local scripts, not real teammates or AI models.

## Run the example

Use the [shared prerequisites](../README.md#prerequisites) and
[tested default Casita CLI](../README.md#tested-casita-build). Python 3.9+ and its
standard library suffice. No AI account, signing key, container runtime or cloud
service is needed. From the demo repository root:

```sh
python3 team_handoff.py --casita "$CASITA_DEMO_BIN" \
  --output output/my-teammate-handoff --accept bob
```

The required `--accept` argument is your explicit choice in this synthetic run.
Choose `carol` in a second run with a new output directory to select the competing
proposal. There is no default acceptance.

Optional uv runner:

```sh
uv run --no-project --python 3.12 python team_handoff.py \
  --casita "$CASITA_DEMO_BIN" --output output/uv-teammate-handoff --accept bob
```

Expect six `PASS` lines, a selected knowledge file and a private `receipt.json`.
An existing output directory is refused.

## What teammates share

| Material | Fixture | Purpose |
| --- | --- | --- |
| Selected evidence | `source/retry-policy.txt` | Three synthetic lines allow at most two additional attempts and permit a lower limit |
| Task context | `task.json` | Completed policy inspection, pending guidance selection and a future real-workflow test |
| Team knowledge | `knowledge.json` | One stable entry ID, its current statement and owner label |
| Snapshot identity | `manifest.json` | Exact file hashes, team/task/revision identity and recorded parent/decision |

All inputs are generated labelled synthetic fixtures. The example does not read
your chat history, personal memory, private repository, SSH keys or customer data.
No received source or knowledge text is executed or treated as an instruction.

Bob proposes “Allow at most two additional attempts.” Carol proposes “Default to
one additional attempt, within the two-attempt limit.” Both cite the same exact
source lines. A citation verifies what the source says; it does not decide which
proposal the team should adopt.

## Exchange, review and select

1. Alice stores the initial snapshot and exports a portable Casitar. The trusted
   coordinator retains the manifest ID, directory key and archive hash separately.
2. The script deletes the original generated snapshot and moves Alice's store
   aside. Bob and Carol each restore the archive into their own fresh store and
   verify the original file hashes and task state.
3. Each returns a proposal containing its parent snapshot ID, parent directory
   key, base knowledge hash, replacement entry and exact source citation.
4. A fresh owner-return store restores the original parent and both returned
   proposals. Receipt of either proposal does not modify accepted knowledge.
5. The required CLI choice supplies a local owner decision tied to the exact
   proposal hash. The selected revision records that decision and its parent,
   changes the knowledge entry, and leaves the real-workflow test pending. The
   old snapshot and both proposals remain available.
   The selected snapshot and exact accepted proposal are exported together and
   restored back into both teammate stores. Each checks that the selected entry
   matches the accepted proposal and verifies its original source citations;
   receiving it retains their original context and proposals.
6. Both old proposals reject when checked against the new selected snapshot.
   The unselected proposal needs a new review against current context before a
   future update; it cannot silently overwrite the selected knowledge.

See the selected knowledge, both retained proposals and unfinished work in one
view:

```sh
python3 team_handoff_summary.py --run output/my-teammate-handoff | python3 -m json.tool
```

This read-only command needs Python 3.9+ and the demo scripts, with no Casita
process or AI account. It validates the original and selected snapshots, both
source-cited proposals and the recorded decision before printing. The selected
entry must match the exact proposal named by that decision. It refuses altered
evidence or links, writes no files and does not execute received text. The
unselected proposal remains advice that needs a fresh review.

Expected pins come from the trusted local run's `receipt.json`. The summary does
not authenticate that unsigned receipt or the scripted owner choice. Replacing
the entire run and its pins is outside these integrity checks; use the signed
protocol and independent signer trust when sharing real evidence.

Inspect the underlying files when needed:

```sh
python3 -m json.tool output/my-teammate-handoff/owner/context-v2/knowledge.json
python3 -m json.tool output/my-teammate-handoff/owner/context-v2/manifest.json
python3 -m json.tool output/my-teammate-handoff/owner/proposals/carol/proposal.json
python3 -m json.tool output/my-teammate-handoff/carol/accepted-proposal/proposal.json
python3 -m json.tool output/my-teammate-handoff/receipt.json
```

Eleven negative controls reject a wrong archive hash, missing owner selection,
selection for another proposal, both stale returns, a wrong parent directory key,
changed base knowledge hash, wrong task, invented excerpt, a citation outside the
selected source and an attempted owner change. Modified proposals get fresh
hashes in these controls, so a valid hash alone cannot bypass source/parent checks.

## From a demo to a team workflow

For proposals about who should do what next, see the
[bounded planning exchange](agent-planning.md). It retains a counterproposal and
requires both roles to agree to the same plan, while leaving human review pending.

Keep reviewed project knowledge in ordinary versioned files, with an owner and
source references. Use task capsules for the evidence and unfinished work behind
a particular handoff. An assistant can read both when starting a new session;
that does not restore another assistant's internal model state.

For a real handoff, preview the selected files before sharing, deliver expected
pins through a trusted channel, and establish signer trust independently. The
[signed handoff example](signed-handoff.md) demonstrates detached signatures;
this example's Alice/Bob/Carol labels do not authenticate a person. Transport
credentials and permissions belong to the chosen sharing channel, not a content
hash. Sharing a capsule does not revoke copies already received.

Use a PR or another explicitly reviewed decision to accept knowledge updates.
Check the current parent before accepting, preserve competing proposals and
record the selected proposal's identity. If Git already meets the team's need,
use Git. Casita is useful to explore when retaining and transporting repeated
snapshots with common source or artifacts; this example does not measure savings.

## Validation boundaries

The four stores and all role orchestration run sequentially on one host in one
Python process. Casita CLI commands are real subprocesses. This checks archive
transport, content and task correspondence, source citations, explicit scripted
selection and stale-return rejection. It supports one selected update from the
initial fixture; it is not a general merge engine, collaborative editor, atomic
shared-head update service or live agent adapter.

The recorded owner decision is a scripted caller choice, not proof of human
approval. Expected pins are unsigned and assumed to arrive from the trusted
coordinator. Identity authentication, access control, network isolation,
simultaneous writes, execution attestation and review quality are not tested.
The scripts preserve text as data; a real AI adapter needs its own instruction
and tool-access controls. Raw receipts include local paths and remain under
ignored `output/`; do not publish stores or archives by default.
