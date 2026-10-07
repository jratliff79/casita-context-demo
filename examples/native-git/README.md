# Import selected Git context with the Rust API

Use this when a local tool repeatedly captures approved source subtrees and wants
to reuse unchanged Git objects without creating a checkout. The executable creates
its own synthetic bare Git repository, selects a tree, imports two revisions into
an in-memory Casita repository, and checks the retained reader through collection.
Its publisher process then imports the selected tree into a disk store, publishes
a named root, deletes the original Git source and exits. A separate verifier
process opens that store and checks collection and exact payload readback.
It accepts no input Git repository and executes no imported source.

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
that fresh ignored directory, an in-memory store, and a persistent Casita store
under `<directory>/durable-store`. The temporary source is explicitly removed
before the publisher exits; the caller-selected directory and disk store remain.
The coordinator waits for successful publisher exit before starting the verifier.
It runs two copies of this locally built executable, not imported source. A failed
worker makes the command fail and prevents the final completion line.
An interrupted run can leave its synthetic fixture there. Build artifacts also
stay in ignored `output/`.
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
PASS durable publish: named root committed before releasing import reader
PASS process exit: publisher succeeded and exited before verifier starts
PASS durable reopen: named root and unrooted control survive publisher exit
PASS durable collection: unrooted control removed; named tree remains complete
PASS durable readback: exact context restored with original Git source removed
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

## Reader lifetime and durable roots

An import returns both a report and a retained reader. Keep that reader alive
while using the imported closure. The first, in-memory phase publishes no named
application roots. It releases the earlier readers, flushes their leases, and
collects while the second reader still protects its closure. Dropping every
reader alone is not a promise of durable retention.

The disk phase restores only the synthetic helper removed earlier, then imports
the same second tree into a fresh `Repository::local` store. This is a separate
import, not transport from the memory store. Its mutation session publishes the
named root `synthetic/context-v2` while the import reader is still alive. Only
after that commit does the example release the reader. It also publishes a
separate unrooted synthetic blob as a collection control, releases its session,
flushes the store, closes all its handles, and removes the original Git source.
It writes a small local `restart-state.txt` receipt containing the selected tree,
context blob and unrooted control identities, then exits normally. This generated
synthetic receipt is not a signed handoff or execution attestation.

Only after that process exits successfully does the coordinator start a separate
verifier process. No repository handles, retained readers, leases or runtime state
from the publisher can keep objects alive in the verifier. Using the local receipt,
it checks that the durable name still points to the exact selected tree and that
the unrooted control exists. Collection runs before
opening any new retained reader. It must remove that control while the named
tree's three-object closure remains complete. A warm import using a nonexistent
source directory reads zero source bytes, then reads back the exact context
payload. The named root remains in the disk store after the executable exits.

In an application, publish the durable name before releasing the import reader,
and remove or update names according to the application's retention policy.
The generated disk store uses `casita.sqlite` for metadata and `blobs/` for
payloads. It contains synthetic evidence and stays private in ignored `output/`;
it is not a publication artifact.

These are functional correctness checks, not measurements of review quality,
speed or net storage savings. They establish selected object identity, reuse and
retention through a normal process exit, reopening and collecting the disk store
in a separate process on the same host. They do not test crash or power-loss
recovery, cross-host handoff, Casitar transport,
source execution, execution attestation or merge authority.
Git plumbing runs only over generated synthetic data. Source files remain data.

The `--internal-publish` and `--internal-verify` worker modes are implementation
details of the coordinator. They receive its fresh output path through stdin;
use the documented `--output` command rather than targeting an existing store.
