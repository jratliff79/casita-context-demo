# Casita dependency snapshot

The tested upstream Casita revision, `b8366af1d3859b47db9cb45f910522b7c24ee35d`,
ignores `Cargo.lock`. A fresh source checkout therefore cannot build with
`--locked` on its own.

`Cargo.lock` here is a dependency snapshot from the local checkout of that
tested revision. CI copies it into the fresh upstream checkout before building
with Rust 1.94.1 and `--locked`. It contains public registry checksums and pinned
Git dependency revisions, including dependencies of inactive workspace members
and optional backends. It does not add those backends to the default CLI build.

When changing the tested Casita revision, regenerate the lockfile in a fresh
public-source checkout, review its dependency sources, and validate the locked
build and handoff on the hosted Linux runner before merging. Update the source
pin, toolchain and README together. The CI build uses the snapshot, while the
README's simple source-install instructions resolve their own dependencies.
