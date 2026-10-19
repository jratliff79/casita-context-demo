# Paced two-operator relay workload

`relay_workload.py` adds an opt-in workload beside the unchanged
[closed-loop contention harness](relay-load.md). Each controller creates a fresh
synthetic relay and ephemeral alpha/beta credentials. There is no deployed URL
or existing-state option. Received context is verified as data and never executed.

The controller requires Linux, Python 3.9+, the pinned native Casita executable,
`/usr/bin/age`, and `age-keygen`. Local workers also run on Linux. SSH workers
may run on Linux or macOS, with their own trusted native executable. The workload
uses only the standard library and the existing relay, backup and monitor code.

## Run a short code check

Use a new ignored output directory every time:

```sh
python3 ci/verify-relay-workload.py --casita "$CASITA_DEMO_BIN" \
  --output output/paced-native-smoke
```

This runs a 30-second normal phase and a 20-second burst, with an API-built
12-revision starting store. Fourteen logical publications finish at revision
26. Seven independent native consultations per operator verify the checkpoints
and memory filtering; four deliberate conflict pairs each commit both changes
using one bounded loser retry. The short profile polls every 2.5 seconds so its
last poll follows its last publication. It is explicitly labelled a smoke test.
After both operators finish all scheduled work, the controller releases a
separate bounded final catch-up to the exact acknowledged cursor. This drain
is recorded separately and does not change the offered polling rate or p95
summaries. A publication finishing after the last scheduled poll is preserved.

The check also captures, age-encrypts and restores the complete owned runtime
and relay state during active client work. A separate copied-state case exercises
three missed events in smoke mode, preserving the timed relay's original pin.
No cloud, SNS, production service or installed systemd unit is contacted.

## Bounded diagnostic evidence

Add `--stage-timing` to a controller command to retain private numeric stage
records. The native smoke check enables it automatically. The ordinary relay
CLI leaves timing disabled. This option preserves the workload, retry rules,
native verification, durability calls, acceptance budgets and denominators.

`stage-timing.jsonl` joins each scheduled client's wire attempt to exactly one
server request using a numeric synthetic tag. Nested spans separate HTTP
read/decode, relay handling, authentication, snapshot creation, native import,
root lookup, archive creation, whole-store fsck, native checkout, snapshot
persistence and SQLite commit. Preparation also records native retained-history
verification. Native spans measure wall time, including process launch/wait;
they do not attribute CPU time from overlapping child processes. Backup overlap
has a total wall span; its internal native calls and client-side native work
remain outside this server trace.

The outer `http.accepted` interval starts in the request handler, after socket
accept/setup. It does not directly measure the pre-accept queue, SSH transport
or network delay. Compare a client's wire interval with its matching server
interval using the retained clock calibration and uncertainty. Their residual
also includes queueing, client work and instrumentation; it is not a pure network
measurement. Nested intervals overlap, and their percentiles must not be added.
All instrumentation overhead remains in the observed latency. The summary's
`write_overhead_ns` counts trace encoding/write-lock/writes, not all timing work.

Each trace is capped at 60,000 records, 16 MiB total and 1,024 bytes per record,
with at most 32 nested spans per thread and 4,096 distinct request tags. Exhaustion
preserves the prefix and counts dropped records. Write errors, caps, or missing,
unexpected or duplicate scheduled tags make diagnostics incomplete and suppress
the success receipt. A complete trace describes evidence collection, not workload
acceptance: `failure.json` can coexist with a complete timing summary. Scheduled
wire observations carry the tag only when enabled; preparation, readiness,
quiescent drain and copied recovery traffic can be untagged. Tags are synthetic
correlation identifiers, not authentication or execution attestation.

After creating its new private output directory, the controller always writes
bounded `preparation-status.json` and, on failure, `failure.json`, even without
timing enabled. Status separates preflight/configuration, native initialization,
API history seeding, history verification, storage and worker startup from the
timed workload. It includes monotonic time and an acknowledged seed revision
count, updated every 50 revisions. Preparation is not the timed-phase start.
Status writes are capped at 2,048; the scalar reader refuses files over 1,024
bytes, symlinks, non-regular files and unexpected fields or values.

Failure evidence retains a fixed error category, stage and at most 12 safe stack
frames per failure, with the primary failure and up to two secondary failures
bounded to 8 KiB. Only allowlisted public module basenames, function names and
line numbers are retained; external frames become `external`. It omits raw
exception text, command arguments/output, credentials, payloads and absolute
paths. Diagnostic files use mode 0600 in a fresh owned mode-0700 directory.
Atomic replacement and close are not a power-loss durability attestation. If
the output itself cannot be created or written, even failure evidence may be
unavailable. Keep partial evidence and the nonzero exit; never treat a missing
receipt as a pass. Owned-resource cleanup still runs before publishing success.

## Run one full paced profile

After reviewing the execution environment and its resource/time authorization:

```sh
python3 relay_workload.py --casita "$CASITA_DEMO_BIN" \
  --output output/paced-fresh-one --local-clients
python3 relay_workload.py --casita "$CASITA_DEMO_BIN" \
  --output output/paced-aged-one --local-clients --initial-revisions 840
```

Each command is one 70-minute run, not a six-run qualification matrix. The full
profile schedules one publication per operator per minute for 60 minutes,
offset by 30 seconds, and a full consultation 15 seconds after each publication
arrival. Both operators poll one updates page every five seconds. During the
ten-minute burst they publish against the same revision every 30 seconds, then
each consults 15 seconds later. One loser refreshes and retries once using a new
attempt ID while preserving the logical checkpoint. Other HTTP failures and
normal-phase conflicts fail the run rather than being retried or hidden.

The normal phase produces 120 committed changes and 120 consultations. The burst
produces 40 committed changes, 40 consultations and 20 explicit initial conflicts.
Every operator alternates between two tasks with 4,096 bytes of changing,
labelled synthetic text. The two short accepted-memory fixtures retain their
original citations. Arbitrary attachments and larger working sets are outside
this profile.

Seeding uses ordinary authenticated publish/propose/accept operations. An aged
run starts at exactly 840 retained revisions, including accepted-memory changes,
and finishes at 1,000. All referenced native objects and persisted transitions
are verified before timing starts; no SQL history fabrication or pruning is used.
A fresh run starts at six revisions and finishes at 166. The full profile's
separate reconnect case then creates 100 acknowledged publications on an owned
copy and requires exact catch-up and native restoration within 30 seconds. Those
extra revisions never change the timed run's accounting.

## Use remote client processes

The controller must run on the isolated relay host. Both client machines need
a trusted checkout with identical harness module bytes, a working Python and a
previously trusted native Casita executable. SSH authentication and host-key
verification must already work. The harness does not install keys, accept new
host keys, modify SSH configuration or provision machines.

Create a private mode-0600 JSON file outside tracked source. The following values
are placeholders; replace both native SHA-256 values, paths and host aliases.
Choose unused loopback forwarding ports on the client machines and new private
output paths for this run and its `-reconnect` sibling.

```json
{
  "alpha": {
    "host": "YOUR_ALPHA_SSH_ALIAS",
    "port": 22,
    "checkout": "/absolute/trusted/demo",
    "python": "python3",
    "binary": "/absolute/trusted/casita",
    "binary_sha256": "REPLACE_WITH_64_HEX_NATIVE_BINARY_SHA256",
    "forward_port": 18765,
    "output": "/absolute/private/output/alpha-RUN_ID"
  },
  "beta": {
    "host": "YOUR_BETA_SSH_ALIAS",
    "port": 22,
    "checkout": "/absolute/trusted/demo",
    "python": "python3",
    "binary": "/absolute/trusted/casita",
    "binary_sha256": "REPLACE_WITH_64_HEX_NATIVE_BINARY_SHA256",
    "forward_port": 18766,
    "output": "/absolute/private/output/beta-RUN_ID"
  }
}
```

An optional `ssh_config` field selects an existing private SSH config file with
`-F`; otherwise normal OpenSSH host aliases apply. Agent and X11 forwarding are
disabled. Both host-key checking and forwarding failure checks are strict, and
authentication is noninteractive. The remote command executes only the trusted
checkout's worker. A reverse SSH forward exposes the controller's owned relay
only on that client's loopback address. HTTP Host validation retains the actual
relay port even when the forwarded port differs.

```sh
python3 relay_workload.py --casita "$CASITA_DEMO_BIN" \
  --output output/paced-remote-one --clients "$PRIVATE_CLIENT_CONFIG" \
  --initial-revisions 840
```

Workers report executable/module hashes before the controller sends newly
generated synthetic credentials through encrypted stdin. Credentials never
appear in command arguments. Every worker gets a private native output/store
for each consultation and removes its temporary credential before acknowledging
completion. Controller disconnect or operation-deadline failure terminates the
worker's owned process group, including in-flight native children.
Completion rechecks worker and controller source/executable bytes against their
startup hashes; changed bytes prevent a success receipt.
Executable aliases are resolved to the verified canonical target before use.

Five round trips calibrate monotonic clock offsets before scheduling. Startup
uncertainty greater than 250 ms is rejected. Each worker follows its own fixed
schedule; it does not slow the offered rate to match service response time.
Only one operation of each kind may be outstanding per operator. An arrival
that finds its operation still busy fails the run, and all latencies include
delay from the scheduled arrival. The controller independently checks timing
arithmetic, counts, native pins, exact event payloads and final checkpoint bytes.

SSH transport alone does not prove that two physical devices participated.
`remote_devices_tested` always remains false. The receipt separately records
worker system/machine/hostname observations, including whether client names are
distinct; these are not host execution attestations. Actual JP/CJ device
qualification requires independent evidence outside this harness.

## Backup and measurement evidence

At the midpoint of the normal phase, the real backup capture code briefly
quiesces only the owned HTTP server through a synthetic service adapter. It
copies and verifies every retained recovery object, resumes service, encrypts
through age, writes ciphertext to a local private sink, decrypts it with a
throwaway identity, compares the complete restored inventory, and verifies native
history and authenticated alpha/beta restoration. The private age identity is
removed even on failure. This exercises capture/encryption/restore interference;
it does not qualify installed systemd behavior, S3, alerts or off-host durability.

An encrypted stdin challenge asks a worker which logical publication or
consultation is active. A challenge sent and answered strictly within the backup
interval proves causal overlap without relying on client clock drift. At least
one such overlap is required; an idle backup outside the workload cannot pass.
The probe overhead stays in the run.

The controller retains private per-operation JSONL, calibrated schedules,
per-operator/per-phase nearest-rank summaries and maxima, first/last 15-minute
publication windows, before/after storage and history counts, one-second Linux
memory/VM/CPU/filesystem samples, and five-minute read-only monitoring samples.
Missing host measurements fail completion. Native restore timing stays separate
from full consultation timing; component percentiles are never added together.

The receipt distinguishes correctness completion (`ok`) from the recorded
candidate p95 budgets and the unqualified overall capacity claim. A hard deadline,
lost arrival, native verification, backup/restore, exact-history or cleanup failure
prevents a success receipt. Success is published atomically after cleanup. Other
partial observations remain private for diagnosis; failed attempts are not
silently replaced. The old contention mode and its dated receipts are unchanged.

`capacity_qualified` remains false, including for a full profile. This tool alone
does not verify the provider's complete CPU buckets, depleted-credit/launch state,
three fresh plus three aged repetitions, every operational budget or the actual
operator devices. It does not approve a resize, retention horizon or pruning.
Inspect and export only reviewed numeric results and hashes. Never copy the whole
private output tree into Git or a shared context.

## Reducing native check overhead without reducing verification

For local cost comparisons, use the [checked optimized build and permanent fsck
benchmark](../ci/README.md#checked-optimized-relay-build). Its source, lock and
integrity operations match the debug build; compiler optimization retains debug
assertions and overflow checks. Full `fsck --dry-run` still runs on every
publication. The corpus checks complete old history and corrupt historical data,
not just the latest snapshot. It does not qualify the new binary for a paid or
production workload; those require fresh exact-binary evidence and authorization.
