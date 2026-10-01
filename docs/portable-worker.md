# Portable synthetic worker

`handoff.py` separates preparation, work and return verification. Each command
uses a new output directory and its own Casita store. It refuses existing output
and keeps a raw receipt under that output. This supplements `demo.py`; the original
single-command demonstration remains available.

Pre-provision the same reviewed revision of this demo and the tested Casita build
on both sides. The worker needs `handoff.py`, `demo.py` and the separately trusted
`fixtures/source/verify.py`. Do not install or execute source received inside the
context archive. The worker compares received checker bytes with its local
checker, then executes only the trusted local bytes whose hash it records.

## Sender

```sh
python3 handoff.py prepare --casita /path/to/casita --output output/sender
```

Retain `output/sender/contexts/` and `output/sender/pins.json` locally. Transfer
only `output/sender/handoff.casitar` and the input pins to the worker. Pins are
outside the archive and must arrive through a trusted channel, such as your
authenticated file-transfer session. Replacing both archive and pins is not
prevented by content hashes. The demo does not set up SSH, accounts or credentials.

## Worker

From the pre-provisioned trusted demo checkout:

```sh
python3 handoff.py work --casita /path/to/casita \
  --archive incoming/handoff.casitar --pins incoming/pins.json \
  --output output/worker
```

Input pin schemas and identifiers are checked. Archive size is bounded to 1 MB,
and its expected SHA-256 is checked before store initialization or import.
Imported roots must match the expected keys, independent of archive ordering.
Manifest/file hashes are checked before the local checker reads observations.

Transfer `output/worker/handoff.casitar` and `output/worker/pins.json` back to the
sender. No whole store, input checkout or raw receipt is needed for the return.

## Return verifier

On the sender, with its retained original evidence:

```sh
python3 handoff.py verify --casita /path/to/casita \
  --archive returned/handoff.casitar --pins returned/pins.json \
  --original output/sender --output output/verified-return
```

This verifies the return archive, expected result roots and exact result files.
The sender recomputes the deterministic verdict against its original context.
It checks context, observation and checker bindings rather than trusting a
worker's verdict. Every phase audits its fresh store.

The commands are transport-neutral. Raw receipts can contain local paths; keep
them private. The files selected for archives are the existing public synthetic
fixtures and deterministic result JSON, with no credentials or private projects.

## Executed cross-platform trial

On 2026-10-01, the Mac prepared both contexts, a fresh Apple Container Linux arm64
VM ran the worker, and the Mac verified both returned results. The worker's only
incoming handoff files were the context archive and separate pins; its trusted
code and Casita source/build were provisioned independently. Incoming files and
trusted code were mounted read-only. Writable output was a dedicated scratch
directory. No host home, global Casita store, SSH agent, credentials or ports
were exposed to the worker. The disposable VM exited and was removed normally;
the existing builder was left running.

The 9 sender, 13 worker and 7 return-verifier Casita commands succeeded. V1
returned `rejected: source or output audio timing unavailable`; v2 returned
`accepted`. The return verifier matched both results to the original contexts.
The input archive was 5,880 bytes and the result archive 2,076 bytes. These are
observed fixture sizes, not a storage or performance benchmark.

The pinned Casita revision was
`b8366af1d3859b47db9cb45f910522b7c24ee35d`. The Mac used the existing release build;
the Linux worker compiled a default-feature debug CLI from the same source with
the CI lockfile in the official `rust:1.94.1-bookworm` image. Binary hashes and
platform details are in the [sanitized trial summary](portable-worker.json).

This tests a local Linux VM and cross-platform Casita transport. It does not test
physical remote machines, network authentication, hostile-code isolation,
execution attestation or AI review. Worker platform fields are diagnostics,
not authenticated identities. CI additionally runs all three commands in separate
processes on a single hosted Linux runner; that is a process-separation check.
