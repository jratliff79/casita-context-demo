# Visible AI workflow checkpoints

An agent task outlives its chat, moves to a different machine, or needs two
possible continuations. You want the next worker to see exactly which inputs,
prompt, recorded responses, notes and unfinished tasks were handed over.
This example packages that visible state in Casita and binds returned work to
its parent checkpoint. It uses a synthetic task and fixed local Python policy;
it does not call an AI model or restore a model's internal memory.

For a bounded use with a fresh AI worker, see the
[public documentation checkpoint pilot](docs-checkpoint-case.md). That case uses
the existing signed Git-review protocol to carry public docs and visible task
state, and checks the returned citations. Its replay uses the recorded report;
it does not turn this synthetic day-plan script into a live agent adapter.

## Prerequisites and run

Use the [shared prerequisites](../README.md#prerequisites) and
[tested default Casita CLI](../README.md#tested-casita-build). Python 3.9+ and the
standard library suffice. No AI account, API key, signing key, cloud service or
container runtime is needed. Run from the demo repository root with a fresh
output directory:

```sh
python3 checkpoint.py --casita "$CASITA_DEMO_BIN" --output output/my-checkpoint
```

Or use optional uv to select or download Python 3.12:

```sh
uv run --no-project --python 3.12 python checkpoint.py \
  --casita "$CASITA_DEMO_BIN" --output output/uv-checkpoint
```

Expect seven `PASS` lines and an ignored `receipt.json`. Every run requires a new
`output/<directory>`; an existing directory or linked output is refused.

## What is frozen

The script creates two versions of a synthetic day-plan task. The budget changes
from 90 to 75; three choices cost 40, 70 and 85. These values are invented and
are not real travel advice or prices.

| File | Visible state |
| --- | --- |
| `inputs.json` | Synthetic budget and candidate costs and durations |
| `prompt.txt` | Recorded request to draft two options |
| `tool-responses.json` | Labelled synthetic response from a fixed local budget filter |
| `notes.txt` | Recorded progress note |
| `progress.json` | Task/revision identity, completed inspection and pending drafting/comparison |
| `manifest.json` | Exact file hashes and task/revision binding |

The manifest SHA-256 and Casita directory key identify each checkpoint. Expected
pins are captured separately by the trusted local script. Their authenticity is
assumed in this unsigned demonstration; a receiver must not accept replacement
pins from the same untrusted source as the evidence. See the
[signed handoff](signed-handoff.md) for signer provisioning and pin authentication.
The manifest's file set is allowlisted for this fixture, not an automatic privacy
filter for arbitrary chat exports.

## Resume and branch

1. Import both snapshots and export them in a portable Casitar.
2. Check its independent archive hash and verify its graph. Remove only this
   run's generated checkpoint directories and move its sender store aside.
3. Import into a fresh worker store. Restore and verify both snapshots against
   the original pins and file hashes.
4. Apply the trusted local policy to the restored v2 inputs. The `economy` branch
   chooses the least expensive eligible option, park. The `full-day` branch
   chooses the longest eligible option, museum. On v1 it would choose studio,
   which exceeds v2's budget.
5. Record both results with parent manifest ID, parent directory key, input hash
   and branch identity. Mark drafting complete and leave comparison pending.
6. Export the parent and both branches together. Restore them into a third fresh
   store and check each result against the independently expected parent and
   trusted local policy. Both stores pass a dry-run integrity audit.

The worker and verifier are roles within one Python process, using separate
Casita stores. This is a scripted transport/checkpoint demonstration, not a
live agent integration or a separate-process restart test. Prompts and notes
are preserved as data; they do not direct the script's execution. No received
tool name, instruction or source code is executed.

Nine negative controls reject a wrong archive hash, mixed progress, altered
prompt, missing response, a valid result from the stale v1 parent, a swapped
branch, and results with newly hashed but incorrect parent ID, parent directory
key or selected choice. Rehashing a changed result cannot satisfy the separate
expected parent and fixed continuation policy.

## Inspect the result

```sh
python3 -m json.tool output/my-checkpoint/receipt.json
python3 -m json.tool output/my-checkpoint/verified/economy/result.json
python3 -m json.tool output/my-checkpoint/verified/full-day/result.json
```

The receipt records original file hashes, checkpoint/result pins, branch choices,
the nine rejection controls and each actual CLI command and exit code. It also
records the executable hash and reported Casita version. Raw receipts contain
local paths and stay private under ignored `output/`.

This verifies correspondence to a visible checkpoint and a deterministic local
fixture policy. It does not establish prompt completeness, model identity,
external tool truth, execution attestation, deterministic AI replay, continuation
quality, current task authority, or token/time/storage savings. The sender path
is unavailable after export; host network isolation is not enforced. Both stores
and archives are local to one host.

A real agent adapter would need an explicitly reviewed export of visible state,
trusted pin delivery, a worker that treats received content as evidence, and
separate judgment of its returned work. Do not export credentials, private chat
history or customer data by default. Git or an ordinary archive with a manifest
may suffice for a one-off handoff; Casita is more relevant when retaining many
related snapshots and branching work repeatedly.
