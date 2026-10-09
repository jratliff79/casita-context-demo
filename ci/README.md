# Casita dependency snapshot

The pinned upstream Casita revision, `1407672f4b235609ae7fff0f87a30e06183f941f`,
ignores `Cargo.lock`. A fresh source checkout therefore cannot build with
`--locked` on its own.

`Cargo.lock` here was regenerated in a fresh public-source checkout of that
revision using Cargo 1.98.1, which resolved Rust 1.94.1-compatible packages from
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
The [current-pin local validation](../docs/casita-update-validation.json) records
locked default and OCI builds, core and artifact/event-kit/checkpoint demos,
native Git checks, bounded metadata tests and image transport with the host
checker. The [previous receipt](../docs/casita-20261005-validation.json) retains its
original pin and build metadata. These checks preserve the distinction between
transport and container execution; they do not benchmark the upstream change.
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
