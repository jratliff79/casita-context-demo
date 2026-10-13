# Read-only relay health and storage monitoring

`relay_monitor.py` samples an explicitly configured local relay. It checks an
authenticated loopback `/updates` request beyond the largest event cursor, so the
response contains no event history. It rejects redirects and oversized or malformed
responses. Filesystem available bytes and free inodes, bounded allocated state
bytes, and read-only SQLite revision/event counts are sampled independently.
A readiness failure does not hide disk observations; a failed storage collector
sets `MonitorComplete=0` and omits that collector's metrics. Readiness qualifies
authentication and the updates path, not native archive restoration or every
publication/acceptance operation.

Create a private, mode-0600 configuration outside Git, with these fields:

```json
{
  "state": "/var/lib/context-relay",
  "credential": "/var/lib/context-relay/credentials/monitor.json",
  "port": 8765,
  "backup_max_bytes": 536870912,
  "free_percent": 15,
  "inodes_free_percent": 10,
  "region": "us-west-2",
  "namespace": "Example/ContextRelay",
  "relay_name": "example"
}
```

`credential` must refer to a provisioned member credential owned by the state
owner with mode 0600. This example does not create that member. The credential
stays in the private file and only travels in the authenticated loopback request.
The namespace and relay label must be operator-selected generic labels, never
context, task IDs, member names or secrets.

```sh
python3 relay_monitor.py --config "$RELAY_MONITOR_CONFIG"
# Explicitly opted-in cloud submission requires the operator's boto3 environment:
python3 relay_monitor.py --config "$RELAY_MONITOR_CONFIG" --publish
```

Without `--publish`, the command prints only numeric observations and does not
access AWS. Cloud submission requires a separately authorized deployment and
billing decision. Dedicated credentials should allow only `PutMetricData` in the
approved namespace; the monitor cannot create alarms or alter access. The nine
custom metrics have a single static `Relay` dimension. No context, file names,
credentials, workspace identity or raw diagnostics enter metrics or stdout.
Do not put private paths or credentials in command-line arguments other than the
configuration file path. The CLI emits a generic failure on invalid configuration
or cloud failure and exits nonzero; missing metric data must be independently
alarmed, so a dead host, stopped timer or expired cloud credential cannot look healthy.

A five-minute deployment should separately alarm on `Readiness < 1`,
`MonitorComplete < 1` and `StoragePressure > 0`, using two consecutive breaching
periods and missing data as breaching. Brief backup quiescence can fail one
readiness sample. Alarm lookback, timer accuracy, request timeouts and delivery
mean this is not a precise detection deadline. Test failure, recovery and missing
telemetry in isolation before production installation; AWS acknowledgements do
not establish inbox delivery.

`StoragePressure` is true when free bytes reach the backup controller's two-copy
reserve (`2 * backup_max_bytes + 256 MiB`), configured free-percent threshold, or
free-inode-percent threshold. Keep `backup_max_bytes` synchronized with the actual
backup configuration. The monitor does not predict archive compression or
reserve additional space for an unbounded publication. Record a measured baseline
before choosing thresholds. Display `StateAllocatedBytes` and retained counts over
time for growth review; this is sampled allocated space, not a quiescent inventory,
deduplication result, capacity forecast or concurrent-load qualification.

The inventory rejects links/special files, scans at most 50,000 entries for ten
seconds, opens directories without following links, and never reads state payload
files or invokes Casita. SQLite counts are read-only and bounded by a progress
handler. Deploy under a read-only filesystem sandbox and a total process deadline;
a growing or changing tree can invalidate a sample. A future WAL deployment may
need separate read-only SQLite qualification. There is no pruning, restart,
publication, deployment, background installation or credential rotation here.
