# Development and validation

Install the [host prerequisites](../README.md#prerequisites) and
[tested Casita build](../README.md#tested-casita-build) first.

```sh
python3 -m unittest discover -s tests -v
python3 demo.py --casita "$CASITA_DEMO_BIN" --output output/another-run
```

Or select Python 3.12 with uv:

```sh
uv run --no-project --python 3.12 python -m unittest discover -s tests -v
uv run --no-project --python 3.12 python demo.py \
  --casita "$CASITA_DEMO_BIN" --output output/uv-development
```

The unit suite invokes OpenSSH for signature checks, but does not require Casita
or an image runtime. The real demo command additionally requires Casita.

Unit checks cover fixture selection, linked inputs, wrong pins, forged manifests,
extra files, symlinks, archive pin mismatch, fixture consistency and preservation
of an existing output. Image checks also cover blob corruption, platform/config
pins, runtime identity readback and cleanup after post-load validation failures.
Signature tests invoke OpenSSH with temporary keys and check signer/namespace
binding, file limits, schema checks, exact verified bytes and rejection before import.
Review report checks bind findings to the original context and verify each cited
file hash and exact line excerpt. They do not judge whether a finding is correct.
The real CLI run exercises root identity, sharing, multi-root portable transport,
restore, returned-result verification and integrity audits. Both are needed to validate
changes to the demonstration.

[CI](../.github/workflows/ci.yml) runs the unit tests on plain Python 3.9 and 3.12, then
builds the tested Casita revision with Rust 1.94.1 and locked dependencies on a
fresh GitHub-hosted Linux runner using this repository's
[dependency snapshot](../ci/README.md), since upstream ignores `Cargo.lock`.
Its handoff check runs the uv quick start with uv 0.12.22 and Python 3.12,
using a disposable fixture copy under `output/`, and verifies
restored file sets, exclusion of a generated Python cache, and result-return controls. This is a
signed and unsigned transport check; the signed control rejects replacement pins
and archives before initializing a receiver store. It remains a
correctness check using a debug CLI build, not a performance benchmark. The workflow uses read-only
permissions and does not upload raw receipts, stores or context archives.
An additional OCI job builds with `--features oci` and checks image/context
transport plus the trusted host checker. It does not run Apple Container.
The handoff job also replays a recorded capsule-only AI review of an allowlisted
public source snapshot, signs its return and rejects correctly signed reports
with a wrong context or invented excerpt. It does not invoke an AI reviewer.
It also runs the selected-Git-lines protocol with a synthetic repository and
scripted report, checks dirty-checkout exclusion, and rejects signed reports
with incorrect source bindings or citations. This is not a fresh AI review.

## Synthetic build-artifact handoff

```sh
python3 artifacts.py --casita "$CASITA_DEMO_BIN" --output output/artifact-development
```

The [artifact guide](artifact-handoff.md) demonstrates a different use case: restore
two exact static-site output bundles after deleting their generated source and
build directories, then select the older version without rebuilding. The handoff
CI job runs this against the same locked default CLI. Unit tests cover manifest
and build-input binding, altered or extra output, source changes during capture,
symlinks, deterministic synthetic rendering and existing-output preservation.
Source, recipe and toolchain metadata are declared and pinned; build provenance
and execution are not attested. No received code or HTML is executed.

## Synthetic offline event kit

```sh
python3 event_kit.py --casita "$CASITA_DEMO_BIN" --output output/event-kit-development
```

The [event-kit guide](offline-event-kit.md) exercises local repository sync and an
incremental cue update. Transfer leaves the prior kit selected until the new
snapshot's pinned root and restored contents verify. The final checkout uses only
the venue store after the sender path is moved aside. Unit tests cover stale
selection, corrupt restored assets, mixed versions, event binding and cue schema.
The handoff CI job runs the example and verifies exact venue-only files and
command ordering. Network isolation and Eventools integration are not exercised.

## Synthetic visible workflow checkpoints

```sh
python3 checkpoint.py --casita "$CASITA_DEMO_BIN" --output output/checkpoint-development
```

The [checkpoint guide](ai-workflow-checkpoint.md) freezes visible inputs, prompts,
recorded responses, notes and pending tasks, then restores them into a fresh store.
Two fixed local policies continue the same parent; returned results and their
parent are restored and verified in another fresh store. The handoff CI job runs
the example through uv and checks exact checkpoint bytes, branch choices and nine
negative controls. Unit tests cover stale parents, swapped branches, repinned
results, malformed inputs, inconsistent progress/responses, links and preserving
prompts as data. No model runs, received instructions are not executed, and model
internal state, tool truth and execution are not attested.

## Synthetic teammate context and knowledge

```sh
python3 team_handoff.py --casita "$CASITA_DEMO_BIN" \
  --output output/teammate-development --accept bob
```

The [teammate guide](teammate-handoff.md) separates task context, proposed advice
and selected knowledge. CI runs both explicit selections with fresh stores,
checks exact restored snapshots, retained competing proposals and recorded
parent/decision identities, and checks eleven rejection controls. Unit tests
cover source citations, stale returns, an absent or mismatched owner decision,
repinned task/team/owner changes, linked input, changed bytes during reads,
knowledge text as data and preservation of an existing output. Roles are
scripted and sequential; signatures, access control and simultaneous writes
are outside this example.

## Recorded public docs checkpoint

The [public docs checkpoint pilot](docs-checkpoint-case.md) has a separate
model-free replay:

```sh
python3 git_review_replay.py --case docs-checkpoint --source . \
  --casita "$CASITA_DEMO_BIN" --output output/docs-checkpoint-development
```

It checks the unchanged recorded AI report against five allowlisted public files
and saved task state at the original commit. CI uses an immutable source checkout
and uv, checks signatures and citations, rejects six newly signed invalid returns
and rejects locally rehashed progress against the original pin. It does not invoke
a fresh reviewer or verify the quality of its recommendations. Unit tests reject
an unknown case, wrong source bytes and substituted recorded reports before
creating artifacts or signing keys.

## Optional native Git API example

The [standalone Rust example](../examples/native-git/README.md) has its own source
pin and lockfile. Run it from the repository root:

```sh
cargo run --locked --manifest-path examples/native-git/Cargo.toml \
  --target-dir output/native-git-build -- --output output/native-git-development
```

A separate CI job uses Rust 1.94.1 and the same locked command on Linux. The
executable asserts subtree and sibling selection, warm import without its source,
delta reuse with the original helper removed, wrong-type rejection and retained
reader survival through collection. It then imports the selected tree into a
fresh disk store and commits a named root before releasing the reader. Its
publisher process removes the original Git source and exits successfully before
a separate verifier process reopens the store and collects without a new retained reader. The
unrooted control must be removed while the named closure remains complete, and
exact payload readback must succeed after deleting the original Git source.
That CI job also runs Rust tests for
existing-output preservation and rejection of paths or symlinks that could
redirect artifacts. Real-process integration tests check that publisher failure
prevents verifier startup and that a mismatched expected root rejects before
garbage collection.
Run those tests locally:

```sh
cargo test --locked --manifest-path examples/native-git/Cargo.toml \
  --target-dir output/native-git-build
```

The executable uses only a synthetic bare Git repository under a fresh ignored
`output/<directory>`, an in-memory store and a local persistent store. It does not
run the Python handoff protocols, transport an archive or execute source. Disk
reopening happens in a separate process on the same host after a normal publisher
exit; crash and power-loss recovery are outside these checks. The coordinator
propagates worker failures, and the generated local identity receipt is not a
signed handoff or execution attestation.
Its `experimental` backend APIs remain tied to the pinned upstream revision.


## Historical validation

The [initial validation](validation.md) and
[cross-platform worker trial](portable-worker.md#executed-cross-platform-trial)
record the earlier `b8366af1` build. They do not validate the current source pin.
The [pinned image trial](pinned-environment.json) records the newer `aed18e32`
build with OCI support and its executed Apple Container check. The demo receipt
records the executable hash and reported version; a version string alone does
not identify a source commit.
