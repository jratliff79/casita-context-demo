# Casita dependency snapshot

The pinned upstream Casita revision, `aed18e32704c8f2bf821cc038720a77610a600ba`,
ignores `Cargo.lock`. A fresh source checkout therefore cannot build with
`--locked` on its own.

`Cargo.lock` here was regenerated in a fresh public-source checkout of that
revision using Cargo 1.98.1, which resolved Rust 1.94.1-compatible packages from
Casita's declared minimum Rust version. CI copies it into the fresh upstream
checkout before building with Rust 1.94.1 and `--locked`. It contains public
registry checksums and pinned
Git dependency revisions, including dependencies of inactive workspace members
and optional backends. It does not add those backends to the default CLI build.
The OCI importer is an optional feature and is not exercised by this workflow.

When changing the tested Casita revision, regenerate the lockfile in a fresh
public-source checkout, review its dependency sources, and validate the locked
build and handoff on the hosted Linux runner before merging. Update the source
pin, toolchain and README together. The CI build uses the snapshot, while the
README's simple source-install instructions resolve their own dependencies.
