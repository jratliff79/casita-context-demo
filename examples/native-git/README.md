# Import selected Git context with the Rust API

Use this when a local tool repeatedly captures approved source subtrees and wants
to reuse unchanged Git objects without creating a checkout. The executable creates
its own synthetic bare Git repository, selects a tree, imports two revisions into
an in-memory Casita repository, and checks the retained reader through collection.
It accepts no input repository and executes no imported source.

This is an optional API example, separate from the
[complete signed handoff](../../docs/complete-demo.md). It does not produce a
capsule, share source with a reviewer, or run a model.

## Prerequisites and run

- Git with [`init --object-format=sha1`](https://git-scm.com/docs/git-init/2.29.0)
  support (Git 2.29+).
- Rust/Cargo 1.94.1 or newer; CI uses 1.94.1.
- macOS or Linux. The Git subprocess uses `/dev/null` to disable global config.
- Network access for the first build of public dependencies, plus build disk space.

From this demo's repository root:

```sh
cargo run --locked --manifest-path examples/native-git/Cargo.toml \
  --target-dir output/native-git-build -- --output output/my-native-git-demo
```

No Python, Casita CLI, signing keys, AI account, registry image or container runtime
is needed. Choose a new `output/<directory>` for every run. The executable refuses
existing directories, absolute paths, paths outside `output/`, nested destinations,
and a symlinked `output` parent. Each run uses a temporary Git repository inside
that fresh ignored directory and an in-memory store. The temporary source is
removed on normal exit; the caller-selected directory remains. An interrupted
run can leave its synthetic fixture there. Build artifacts also stay in ignored `output/`.
The manifest enables Casita's `git` and `experimental` features explicitly, with
default CLI features disabled. It pins the same immutable Casita revision as the
[tested CLI build](../../README.md#tested-casita-build):
`84ec2920791276cd4ad8c029cd60529810e15705`. Its own committed `Cargo.lock` freezes
the standalone application's dependency graph. The backend APIs under
`casita::experimental` can change; this sample is tied to that revision.

Expect these checks, followed by a completion line. A failed assertion or import
returns a nonzero exit status, including in release builds:

```text
PASS cold: imported 3 objects; surrounding tree and sibling excluded
PASS warm: imported 0 objects; reused 1 root; read 0 source bytes
PASS delta: imported 2 objects; reused the unchanged helper
PASS controls: wrong root type rejected; no named roots published
PASS retention: live reader survives collection; exact payload read back
Synthetic local example complete. No source execution or artifact transport.
```

## What gets selected

The synthetic source has this surrounding tree. Only `selected-context` is passed
to `GitClosureImport::new(objects_dir, roots)` as a type-qualified SHA-1 tree key:

```text
surrounding tree                       excluded
├── selected-context/                  selected tree root
│   ├── context.txt                    selected blob, changes in revision two
│   └── helper.py                      selected blob, unchanged
└── unselected-note.txt                excluded synthetic sibling
```

A tree selects all its descendants, not arbitrary lines. Selecting a commit can
also traverse parent history. This API does not apply a privacy policy or scan for
secrets: review and approve the complete selected closure before adapting it to
real source or sharing anything. Use the existing
[selected-line protocol](../../docs/git-review.md) when the intended selection is
specific line ranges. A native Git closure is not that protocol's manifest.

The first import stores one tree and two blobs. A second import of the same root
uses a deliberately nonexistent source directory and succeeds with zero source
bytes read. The delta changes `context.txt`, creates a new tree, and reuses the
stored helper even after its loose object has been removed from the synthetic
source. Reuse counters describe importer work: the warm root's count of one does
not mean only one object is present in its three-object closure.

The assertions also check that the surrounding tree and unselected sibling were
not stored, reject a blob falsely labelled as a tree, and read back the exact new
context bytes after garbage collection.

## Reader lifetime and limits

An import returns both a report and a retained reader. Keep that reader alive
while using the imported closure. This example publishes no named application
roots, releases the earlier readers, flushes their leases, and collects while
the second reader still protects its closure. Dropping every reader is not a
promise of durable retention. An application that needs durable retention must
publish its application roots before releasing its import reader; persistence
and root publication are outside this in-memory example.

These are functional correctness checks, not measurements of review quality,
speed or net storage savings. They establish selected object identity, reuse and
retention during this run. They do not establish Casitar transport, persistence
across processes, source execution, execution attestation or merge authority.
Git plumbing runs only over generated synthetic data. Source files remain data.
