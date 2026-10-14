# Two-client synthetic load qualification

`relay_load.py` measures two independent Python client processes against one
fresh Linux loopback relay and the real Casita executable. Each run creates its
own synthetic workspace, credentials, SQLite index and native stores. It has no
URL or existing-state option. It does not contact or modify a deployed relay.

Build the [pinned CLI](../README.md#tested-casita-build), then run from this repo:

```sh
python3 relay_load.py --casita "$CASITA_DEMO_BIN" \
  --output output/load-small --rounds 20 --tasks 4 --payload-bytes 4096
```

Linux `/proc`, Python 3.9+, and the native CLI are required. Use a new private
output directory for every run. All generated state, credentials, native stores,
client configuration, logs and receipts belong under ignored `output/`. Share
only an inspected numeric receipt, never the whole output directory.

## What the workload does

A coordinator seeds the selected number of tasks and two cited memory entries:
one workspace entry and one entry scoped to the first client's task. Seeding is
outside the load measurement. Task payloads are deterministic synthetic ASCII
hash blocks, labelled synthetic, with distinct content per task and version.
The payload size is selected text bytes per task, not total snapshot bytes.

Two persistent processes have separate member credentials and fresh native
stores for every restore. A ready/go rendezvous releases their publish attempts
at the same workspace revision in each round. The serial HTTP server must
commit exactly one and reject the other with HTTP 409. Winner selection depends
on scheduling; this is a contention workload, not a fairness test. The harness
does not retry a conflicting publication or treat other HTTP failures as normal.
Each client then fetches and independently restores the current snapshot,
checks task/workspace memory filtering, and catches up its own event cursor.

Both clients must report the same snapshot pin each round. The coordinator
checks exact final checkpoint bytes, total revisions/events/acknowledged
requests, and every expected commit/conflict. Failed runs exit nonzero and
write `failure.json`, rather than a successful performance receipt. Cleanup
attempts every owned server/client process group and stream even if one fails.
The success receipt is published atomically only after cleanup succeeds.
Received source and notes are never executed.

## Measurements and their limits

Source pins cover the reachable local Python modules, including authentication
and lazily imported helpers, and are rechecked at the end. They record file
identity, not execution attestation.

`receipt.json` contains the workload, executable/source hashes, host CPU/memory
counts, per-round observations, verification results and these measurements:

- HTTP latency split by operation and status, including successful publishes
  and expected 409 conflicts. This includes loopback transport and server queue
  time; the clients' release barrier is outside the latency timer.
- Native restore latency includes fresh-store init, archive verification/import,
  checkout, native fsck and snapshot validation. It is separate from HTTP time.
- Client cycle latency covers publish, fetch, native restore and event catch-up.
- Server CPU is a delta from its post-seeding baseline; client CPU is each
  worker's total from process birth, including all interpreter startup/imports.
  Both include waited native children. The wall-time load window starts after
  client spawn and CPU baseline reads, so it includes only remaining startup,
  rendezvous pacing and the coordinator's per-round storage scans.
- Sampled peak process-tree RSS includes live native children. The default
  50 ms sampling can miss brief peaks. RSS can count shared pages more than once
  and is not unique physical memory usage.
- Before/after/per-round retained relay bytes, archive/store bytes, file counts,
  revision/event/request/proposal counts and separate client artifact bytes.
  Allocated bytes include directories and count each inode once. Logical file
  bytes describe visible file entries. These are storage observations, not a
  claim about net deduplication savings.

Percentiles use nearest rank. They are descriptive for this bounded sample,
not an availability or latency SLA. Rounds have no client think time, and the
coordinator waits for both clients before starting the next round. The resulting
rounds/second is this closed-loop harness's rate, not maximum relay throughput.
No memory/latency threshold makes a run pass: pass means correctness checks held.

## Repeatable trial matrix

Start with the small run above. Compare a larger selected snapshot in a new
directory, using the same native executable:

```sh
python3 relay_load.py --casita "$CASITA_DEMO_BIN" \
  --output output/load-large --rounds 20 --tasks 20 --payload-bytes 16000
```

The harness bounds rounds to 1–200, tasks to 2–20 and per-task text to
256–16000 bytes. Larger snapshots and growing native history change fsck and
restore costs; retain the per-round values rather than interpreting one average
as a fixed limit. Repeated runs need separate directories. Debug and release
builds, compiler versions, laptop/cloud resources, filesystem behavior and
background load can all change results; record them when comparing trials.

Local runs qualify the harness and these synthetic correctness paths. They do
not establish capacity of the 2 vCPU / 2 GB pilot or SSH latency between devices.
A comparable isolated run on representative hardware and an agreed workload are
still needed before operational thresholds or resize decisions. The harness
does not provision hosts, publish private source, modify alarm configuration,
prune state or establish retention policy. Keep every server revision until a
separately reviewed recovery/retention decision authorizes otherwise.

## Recorded local trial

The [October 13 Pacific validation](relay-load-validation.json) records both
20-round run summaries and exact executable/source hashes. Full per-round
receipts remain under ignored `output/`; their hashes are in the summary.
The host was Linux x86_64 with 16 logical CPUs and about 31.1 GiB RAM, Python
3.14.7 and an unoptimized native debug build from the pinned source/lockfile with
Rust 1.99.0. This differs from CI's compiler and the deployed environment.

| Measurement | 4 tasks × 4096 bytes | 20 tasks × 16000 bytes |
| --- | ---: | ---: |
| Successful publish p95 | 425 ms | 1240 ms |
| Native restore p95 (40 restores) | 464 ms | 598 ms |
| Sampled server tree peak RSS | 145.7 MiB | 173.6 MiB |
| Retained allocated growth (20 commits) | 1.21 MiB | 15.39 MiB |
| Final retained revisions (including seeds) | 26 | 42 |

Both runs passed 20 commits, 20 expected conflicts, identical pins on both
clients, independent native restorations, filtering, contiguous event catch-up
and exact final checkpoint checks. The larger workload retained more bytes and
had slower publications on this host. These samples do not establish a growth
forecast, safe request rate or deployed-host workload threshold. Host-wide
background load was not measured.
