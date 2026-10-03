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


## Historical validation

The [initial validation](validation.md) and
[cross-platform worker trial](portable-worker.md#executed-cross-platform-trial)
record the earlier `b8366af1` build. They do not validate the current source pin.
The [pinned image trial](pinned-environment.json) records the newer `aed18e32`
build with OCI support and its executed Apple Container check. The demo receipt
records the executable hash and reported version; a version string alone does
not identify a source commit.
