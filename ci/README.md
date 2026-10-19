# Casita dependency snapshot

The pinned upstream Casita revision, `9d7a2f42b14d86189662e21241f72a55ccb42cb6`,
ignores `Cargo.lock`. A fresh source checkout therefore cannot build with
`--locked` on its own.

`Cargo.lock` here was regenerated in a fresh public-source checkout of that
revision using Cargo 1.99.0, which resolved Rust 1.94.1-compatible packages from
Casita's declared minimum Rust version. CI copies it into the fresh upstream
checkout before building with Rust 1.94.1 and `--locked`. It contains public
registry checksums and pinned Git dependency revisions, including dependencies
of inactive workspace members and optional backends. It does not add those
backends to the default CLI build.
The optional OCI importer is exercised by the separate environment-transport job,
which enables `--features oci`. The original handoff job keeps default features.
The OCI job imports a digest-pinned public Python image anonymously, checks the
restored layout and runs the trusted host checker; it does not execute the image.
Both jobs use the same source pin, toolchain and dependency snapshot. The separate
Apple Container execution is recorded in [the local image trial](../docs/pinned-environment.json).
The [current-pin local validation](../docs/relay-validation.json) records the
locked default build, core handoff, native Git checks and authenticated relay
pilot. CI retains the default and OCI example jobs and adds the two-client relay
trial. The [previous receipt](../docs/casita-update-validation.json) retains its
original pin and build metadata. These checks preserve the distinction between
transport and container execution; they do not benchmark the upstream change.

The same native handoff job also installs `age` and runs the short
[paced workload check](../docs/relay-workload.md). It reuses the existing native
CLI build and verifies an aged synthetic store, scheduled arrivals, bounded
conflict retries, genuine age capture/restore overlap and separate copied-state
catch-up. The short local-worker profile does not qualify a 70-minute remote
run. `ci/verify-relay-workload.py --ssh-fixture` exercises real strict SSH in a
disposable Docker container with private throwaway keys and loopback-only sshd;
that option is guarded against running on the operator host and is not enabled
on the GitHub host runner. No cloud credentials or outbound notifications are used.
The default-feature handoff job also runs [the signed pin example](../docs/signed-handoff.md)
with OpenSSH and temporary keys. This adds authentication checks without changing
the Casita dependency snapshot or introducing a signing service.

When changing the tested Casita revision, regenerate the lockfile in a fresh
public-source checkout, review its dependency sources, and validate the locked
build and handoff on the hosted Linux runner before merging. Update the source
pin, toolchain and README together. The
[README build](../README.md#tested-casita-build) uses the same snapshot and source pin. It accepts Rust/Cargo 1.94.1 or newer; CI pins the compiler to 1.94.1.
The command is a local debug build and does not install or replace a global CLI.

The [native Git Rust example](../examples/native-git/README.md) is a separate
application with its own `Cargo.lock`. It enables `git` and `experimental`, disables
default CLI features and pins the same Casita revision in its `Cargo.toml`. The
standalone lockfile was originally seeded from the CLI snapshot, then resolved
for that application with Cargo 1.94.1. This update changes its Casita revision
while preserving its other dependency pins. It is not a replacement for the workspace snapshot
used by CLI builds. When updating Casita, update the standalone manifest and CLI
workflow source pins, review both lockfiles, and validate `cargo run --locked` as well
as the CLI jobs. CI does not enable native Git features in the default CLI build.
The Rust job also checks named-root publication, successful publisher process
exit, reopening the disk store in a separate verifier process and collection of
an unrooted control while the selected tree remains retained. This covers a normal
restart on one host, not crash recovery or a signed transport protocol.

After completing its default-CLI demonstrations, the handoff job enables
`experimental` to run upstream's four `metadata_buffers` integration tests.
They cover length hints, EOF, bounded allocation and short reads. This feature
is enabled for the test build; the demonstrations run first with the default
CLI build. No performance timings are collected.

## Checked optimized relay build

The ordinary debug build is intentionally unoptimized. Its cost is visible when
`Relay.save_snapshot` runs full `fsck --dry-run` for every retained revision.
`relay-checked-profile.toml` changes code optimization while explicitly retaining
**both debug assertions and overflow checks**. It inherits `dev`, uses opt-level
3, disables debug symbols/incremental compilation, and keeps the same native
source, default features and dependency lock. It changes no integrity algorithm,
root retention, fsck scope/frequency, archive verification or durability call.
The traversal still scales with retained history; this is a constant-factor build
optimization, not an incremental-integrity design.

After the fresh pinned source checkout and lockfile copy described above:

```sh
python3 ci/build-relay-native.py --source output/casita-source \
  --target output/checked-native-build --output output/checked-native-build-evidence
export CASITA_CHECKED_BIN="$PWD/output/checked-native-build/relay-checked/casita"
```

Use Python 3.12+ and a new evidence directory outside the input checkout. The
helper requires an unchanged Git checkout at the native pin, the exact lockfile,
rustup's Rust 1.94.1 toolchain and no compiler/profile environment overrides.
It rejects untracked and ignored source inputs except the exact root lockfile,
then builds a private Git archive snapshot with replacement objects disabled
and a sanitized Git environment for every validation/archive command. Cargo runs
from that snapshot, with automatic ancestor/home configs rejected and the explicit profile restricted to
the reviewed settings. This accounts for [Cargo's hierarchical configuration](https://doc.rust-lang.org/cargo/reference/config.html#hierarchical-structure)
without consuming configuration from the invoking directory. Keep the build
target outside the source input too. It checks Cargo's emitted binary artifact profile and records source,
lock, config and binary hashes in `build-receipt.json`. A build receipt describes
observed inputs/profile; it is not execution attestation or approval to replace
a deployed binary. The old debug executable is retained for comparison.

The permanent corpus registers `relay-fsck` in `benchmarks/manifest.json` and
includes it in `benchmark all`:

```sh
python3 benchmark all --baseline "$CASITA_DEMO_BIN" \
  --candidate "$CASITA_CHECKED_BIN" --output output/checked-fsck-comparison
```

The default curve covers 6, 166 and 840 API-built revisions. Every seed publication
still runs whole-store physical/logical fsck, checkout and snapshot fsyncs. Both
binaries verify all retained archives/snapshots. Four alternating-order trials
per horizon compare full fsck reports (only explicit timing lines are excluded)
and direct API publication on separate fresh copies for each operation. The
standalone scan cannot mutate the publication copy; filesystem/OS caches are
shared and uncontrolled, so these are not cold-cache samples. Each binary has
two first and two second positions at every horizon. The final publication
copy per binary receives another full history audit. Setup/copy/audit time stays
outside each timed call. Raw wall/physical/logical samples and all attempted
cases remain in the private output; completed scan/publication timings are
recorded before post-call validation, with a separate validation status. A failed or missing case cannot become an
`ok` receipt. Timings do not impose hardware-independent speed thresholds.

Four synthetic corruption controls run with each binary. A distinct old native
canary root is retained before the six API revisions. Its damaged/missing pack
must fail full fsck and block publication even while the latest native
snapshot still restores; latest-pack corruption must also block publication.
Committed SQLite history cannot change on rejection. A separate damaged retained
archive must fail the complete history audit while its native store remains
healthy. None of these controls is used to skip a check for healthy samples.

The `Checked relay fsck` CI job builds both profiles and runs `--smoke` (6/12
revisions, two alternating trials and the same corruption controls). A local
comparison is not a paced remote-client workload, capacity qualification, or
permission for a paid run or production change. Any future qualification must
pin the newly built executable bytes and preserve the historical failed results.
