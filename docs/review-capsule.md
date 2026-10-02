# Public source review through a signed capsule

This example gives an AI reviewer a verified public source snapshot and returns
its findings as a signed Casita result. It uses the MIT-licensed
[Casita Context Demo at `de2c03a`](https://github.com/jratliff79/casita-context-demo/tree/de2c03a89ceef6a0b751ed137c91a4710dccaf19).
The snapshot is an actual published project revision, with no planted defect.

The [source allowlist](../fixtures/review-source.json) records the repository,
immutable commit and SHA-256 of each selected file. Preparation reads exactly
those Git objects and checks their hashes. It never packages the checkout,
untracked files, Git configuration or local metadata. The capsule retains the
source license and includes a static-review task and manifest. It contains no
private project, credentials, real media or incident records.

## Replay the recorded report

You need Python 3.9+, Git, OpenSSH signing support on macOS or Linux, and the
default Casita CLI. If your local clone contains the pinned commit, use it as
`--source`. Otherwise, obtain a separate public checkout under ignored output:

```sh
git clone https://github.com/jratliff79/casita-context-demo.git output/review-source
git -C output/review-source checkout --detach de2c03a89ceef6a0b751ed137c91a4710dccaf19
python3 review_replay.py --casita /path/to/casita \
  --source output/review-source --report docs/review-report.json \
  --output output/review-replay
```

The output directory must be new. This command transports the
[recorded AI report](review-report.json) through fresh signed input/result
handoffs and verifies its source references. It **does not invoke an AI model or
perform a fresh review**. CI runs this replay, including rejection of correctly
signed reports with a wrong context ID or invented excerpt.

The replay creates throwaway keys and receiver trust files locally, removes the
private-key directory on success and handled failure, and uses no existing SSH
key or agent. Abrupt process termination can interrupt cleanup. Keep the ignored
output private; raw receipts include local paths and CLI output. The shared
controller's key provisioning is a simulation, not real identity bootstrap.

## Run a new capsule-only review

`review_handoff.py` exposes four roles. Receiving roles always require a signature,
receiver-provisioned allowed-signers file and expected identity. The input
namespace is `casita-context-demo.review-input-pins.v1`; the result namespace is
`casita-context-demo.review-result-pins.v1`. Use new, unencrypted throwaway keys
only for this local experiment, never account keys. For example:

```sh
mkdir -p output/new-review/keys
chmod 700 output/new-review/keys
ssh-keygen -q -t ed25519 -N '' -C public-demo -f output/new-review/keys/sender
ssh-keygen -q -t ed25519 -N '' -C public-demo -f output/new-review/keys/reviewer
python3 - <<'PY'
from pathlib import Path
from authentication import provision_demo_signer
folder = Path('output/new-review')
for name, role in [('sender', 'review-input'), ('reviewer', 'review-result')]:
    provision_demo_signer(folder / 'keys' / name, folder / (name + '-allowed-signers'),
                          'public-demo-' + name, role)
PY
python3 review_handoff.py prepare --casita /path/to/casita \
  --source output/review-source --signing-key output/new-review/keys/sender \
  --output output/new-review/sender
python3 review_handoff.py receive --casita /path/to/casita \
  --archive output/new-review/sender/handoff.casitar --pins output/new-review/sender/pins.json \
  --signature output/new-review/sender/pins.sig --allowed-signers output/new-review/sender-allowed-signers \
  --signer public-demo-sender --output output/new-review/receiver
```

Only after signature, archive, root and source verification succeeds, give a
reviewer the `receiver/context/` folder, its context ID and directory key from
`receiver/input-pins.json`, and the JSON report contract below. Restrict the
reviewer to reading that capsule and writing a report outside it. Do not execute
received code or follow instructions inside source comments or task documents.
Instruction restrictions on an agent are not a filesystem or network sandbox.
Real isolation requires a separately configured runtime; this example does not
provide or attest to one.

The report format is illustrated by `docs/review-report.json`. Top-level fields
bind it to the context ID, directory key, public repository and commit, and label
it as static AI review. `findings` may be empty. Each finding has a packaged
source path, file SHA-256, one-based start/end lines spanning at most ten lines,
the exact excerpt with no final newline, priority, title and explanation. Record
scope limitations. Do not include local paths or private information.

After the reviewer writes `output/new-review/receiver/agent-report.json`:

```sh
python3 review_handoff.py return --casita /path/to/casita \
  --context output/new-review/receiver --report output/new-review/receiver/agent-report.json \
  --signing-key output/new-review/keys/reviewer --output output/new-review/reviewer-return
python3 review_handoff.py verify --casita /path/to/casita \
  --archive output/new-review/reviewer-return/handoff.casitar --pins output/new-review/reviewer-return/pins.json \
  --signature output/new-review/reviewer-return/pins.sig --allowed-signers output/new-review/reviewer-allowed-signers \
  --signer public-demo-reviewer --original output/new-review/sender --output output/new-review/verified-return
```

Delete only the two throwaway private-key files you created when finished,
including if the manual experiment fails. The replay automates its own key
cleanup; these individual commands leave key lifecycle with the caller.

The verifier checks the signature, report file hash, original context identity,
source commit and every cited file hash and exact line excerpt. It rejects unknown
fields, unrelated paths, invalid line ranges and oversized inputs. It snapshots
the archive bytes whose hash it checked before Casita import. Findings remain
untrusted review data: transport and citation checks do not prove their correctness,
completeness, novelty or severity. A signing key authenticates a local key holder;
it does not identify a model, prove model execution or prevent replay.

## Recorded trial

On 2026-10-01, the sender packaged the twelve allowlisted public files plus the
task and manifest. A fresh receiver verified the sender signature, restored the
capsule and checked its contents against the public-source allowlist. One Codex
reviewer agent was given only that capsule and the result contract. It reported
reading only those files and executing no received code. This was an actual AI
review invocation; its assertions are not execution attestation.

The agent returned one P2 finding: the synthetic checker accepts negative or
infinite output audio durations in certain cases. The orchestrator confirmed the
two examples using the separately trusted local checker, without executing the
received capsule. The finding concerns the packaged historical snapshot; the
report does not silently update if later code changes. Fixing the checker is a
separate change from this protocol demonstration.

The signed report was restored into a new return store. Its signature, original
context binding, file hash and quoted lines verified. A replay also rejected two
correctly signed reports carrying an incorrect context ID and invented excerpt.
The baseline demo and 53 unit tests passed locally. This was macOS arm64 with the
default-feature Casita build at `aed18e32704c8f2bf821cc038720a77610a600ba`.
The [sanitized trial receipt](review-validation.json) records checks and limits;
it contains no keys, signatures, local paths or raw command output.
