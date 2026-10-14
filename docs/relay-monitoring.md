# Read-only relay health and storage monitoring

`relay_monitor.py` samples an explicitly configured local relay. It checks the
server's authenticated `/readiness` endpoint with a separate readiness-only token.
That endpoint returns a readiness boolean with fresh challenge proofs; it cannot
read context, archives or events,
or publish, propose or accept updates. Readiness qualifies authentication and
SQLite access, not native restoration or every write operation. The monitor
rejects ordinary member credentials, redirects and oversized/malformed responses.
State and backup-spool filesystem observations, bounded allocated state bytes and
read-only SQLite counts are independent collectors. A failed collector sets
`MonitorComplete=0` and omits its metrics; readiness failure does not hide storage.

Install the pinned `relay_readiness.py` protocol helper beside both server and
monitor scripts. The server must include the readiness-scope implementation
before installation.
For an explicitly authorized state, provision as its owner:

```sh
python3 context_relay.py provision-readiness --state "$RELAY_STATE"
# Revoke separately; subsequent readiness requests fail, ordinary members stay active:
python3 context_relay.py revoke-readiness --state "$RELAY_STATE"
```

Provisioning creates `monitoring/readiness.json` with mode 0600 in a private
0700 directory. It never inserts a member token, alters the SQLite schema or
overwrites an existing credential. Provisioning writes/fsyncs a private temporary
file before publishing it without replacement, flushes both directory levels,
and removes its new files on failure so a retry can succeed. Readiness opens
SQLite without creating a missing database. The complete-state backup includes this file;
isolated native capture/restore qualification checks its scope after recovery.
These commands do not authorize a production server upgrade or provisioning.

Create a private, mode-0600 configuration outside Git, with these fields:

```json
{
  "state": "/var/lib/context-relay",
  "spool": "/var/backups/context-relay/scheduled",
  "credential": "/var/lib/context-relay/monitoring/readiness.json",
  "port": 8765,
  "backup_max_bytes": 536870912,
  "free_percent": 15,
  "inodes_free_percent": 10,
  "region": "us-west-2",
  "namespace": "Example/ContextRelay",
  "relay_name": "example"
}
```

`credential` must refer to that dedicated scoped credential, owned by the state
owner with mode 0600. Never reuse an owner/member credential. It stays private and
is never sent to the listener. Each request carries a fresh 256-bit nonce and a
request HMAC; the response must match a separate response HMAC bound to that nonce
and the configured workspace. Request and response domains differ, preventing
reflection; captured old responses cannot satisfy a fresh challenge. Neither the
secret nor the workspace name is transmitted. Authentication precedes request
scope validation, so bogus proofs cannot enumerate workspace names. Hide ordinary member credentials
from the monitoring process with the deployment's filesystem sandbox. Choose
generic operator labels, never context, task IDs, member names or secrets.

```sh
python3 relay_monitor.py --config "$RELAY_MONITOR_CONFIG"
# Explicitly opted-in cloud submission requires the operator's boto3 environment:
python3 relay_monitor.py --config "$RELAY_MONITOR_CONFIG" --publish
```

Without `--publish`, the command prints only numeric observations and does not
access AWS. Cloud submission requires a separately authorized deployment and
billing decision. Dedicated credentials should allow only `PutMetricData` in the
approved namespace; the monitor cannot create alarms or alter access. The twelve
custom metrics have a single static `Relay` dimension. No context, file names,
credentials, workspace identity or raw diagnostics enter metrics or stdout.
Do not put private paths or credentials in command-line arguments other than the
configuration file path. The CLI emits a generic failure on invalid configuration
or cloud failure and exits nonzero; missing metric data must be independently
alarmed, so a dead host, stopped timer or expired cloud credential cannot look healthy.

A five-minute deployment should separately alarm on `Readiness < 1`,
`MonitorComplete < 1` and `StoragePressure > 0`, using two consecutive breaching
periods and missing data as breaching. Authorize notification actions and labelled
email tests explicitly. Publish periodic healthy samples before creating alarms;
create with actions disabled and verify `OK` before enabling authorized actions.
Brief backup quiescence can fail one readiness sample. Alarm lookback, timer accuracy, request timeouts and delivery
mean this is not a precise detection deadline. Test failure, recovery and missing
telemetry in isolation before production installation; AWS acknowledgements do
not establish inbox delivery.

`StoragePressure` is true when available spool bytes reach the backup controller's
two-copy reserve (`2 * backup_max_bytes + 256 MiB`) on the configured `spool` filesystem,
or either mount reaches its configured free-percent/inode-percent threshold.
State and spool capacity are measured separately; a small healthy state mount
does not inherit the spool's byte reserve. Failed spool sampling cannot report
`StoragePressure=0`. Keep `backup_max_bytes` synchronized with the actual
backup configuration. The monitor does not predict archive compression or
reserve additional space for an unbounded publication. Record a measured baseline
before choosing thresholds. Display `StateAllocatedBytes` and retained counts over
time for growth review. State allocation excludes the ordinary `credentials/`
subtree that the deployment hides from this process; it includes monitoring state.
This is sampled visible allocation, not a quiescent full-state inventory,
deduplication result, capacity forecast or concurrent-load qualification.

The visible inventory rejects links/special files, scans at most 50,000 entries for ten
seconds, opens directories without following links, and never reads state payload
files or invokes Casita. SQLite counts are read-only and bounded by a progress
handler. Deploy under a read-only filesystem sandbox and a total process deadline;
a growing or changing tree can invalidate a sample. Filesystems with undefined
or inconsistent inode totals produce incomplete telemetry, not a fabricated healthy
inode percentage; qualify the target filesystem. A future WAL deployment may
need separate read-only SQLite qualification. There is no pruning, restart,
publication, deployment, background installation or credential rotation here.
