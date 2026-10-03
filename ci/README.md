# Casita dependency snapshot

The pinned upstream Casita revision, `aed18e32704c8f2bf821cc038720a77610a600ba`,
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
The default-feature handoff job also runs [the signed pin example](../docs/signed-handoff.md)
with OpenSSH and temporary keys. This adds authentication checks without changing
the Casita dependency snapshot or introducing a signing service.

When changing the tested Casita revision, regenerate the lockfile in a fresh
public-source checkout, review its dependency sources, and validate the locked
build and handoff on the hosted Linux runner before merging. Update the source
pin, toolchain and README together. The
[README build](../README.md#tested-casita-build) uses the same snapshot and source pin. It accepts Rust/Cargo 1.94.1 or newer; CI pins the compiler to 1.94.1.
The command is a local debug build and does not install or replace a global CLI.
