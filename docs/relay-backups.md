# Opt-in encrypted relay backups

`relay_backup.py` is a Linux operator tool for an already-qualified relay. It
quiesces writers briefly, copies complete private state with Linux ownership and
modes, restarts the relay, checks authenticated readiness and encrypts a recovery
package with age before uploading ciphertext. Snapshot context is never executed.
Derived Python `__pycache__` directories are excluded from runtime pin comparison
and runtime archives; only pinned source is restored. Private relay state is
copied completely, without that runtime-only exclusion.

This is not a general installer or automatic deployment. Supply reviewed private
root-owned mode-0600 configuration outside Git, with a mode-0700 spool directory.
The configuration identifies state, immutable runtime inventory, original native
binary SHA256, service unit, source/Casita commits, age public recipient, local
health credential, byte limit, S3 destination, region, metric namespace and SNS
topic. The age private identity is never installed on the server or in a backup.
The operator environment needs Python 3, age, tar, systemd and optional boto3.

Run `backup` through a reviewed systemd oneshot service and `monitor` separately
every few minutes. Serialize both through the spool lock. Set an `ExecStopPost`
that starts the relay even if the controller is killed, then invokes `cleanup`
to remove root-only staging/partial files. Never use an untrusted context-supplied
configuration. Public demo runs and unit tests do not access AWS.

The host's dedicated cloud credential should permit only S3 object creation under
the approved hourly/daily prefixes, metric publication in one namespace and
notification to one topic. It must not read, list or delete backups, administer
the bucket, or alter alarms. S3 acknowledgement must match the ciphertext SHA256.
Keys use UTC timestamp plus random ID; a successful first run of each UTC day also
uploads a daily copy. Failed uploads do not advance the success/daily marker.

Retention belongs to reviewed S3 lifecycle rules. Keep the original qualified
manual recovery point outside those prefixes. Hourly/daily retention are policy
choices, not defaults imposed on a user. Test boundary cases before configuring
expiration; lifecycle deletion is asynchronous and cannot be timed to the minute.

Immediate failure/recovery notices contain only the failed phase or success
status, never source, credentials, raw exceptions or local paths. Independently
evaluated CloudWatch freshness/missing-data alarms cover a stopped host, failed
credential, unreachable cloud endpoint or stopped timer. Confirm the SNS email
subscription and verify receipt of a deliberately labelled test before claiming
end-to-end email delivery. CloudWatch alarms and notification submission alone
are not inbox-delivery proof.

Qualification requires decrypting an actual downloaded cloud object, checking
its full inventory and restoring native relay state in an isolated environment.
Retain owner-only key escrow and separately rehearse a replacement host; neither
an S3 upload nor a scheduled timer establishes full disaster recovery.

The spool must be disjoint from both state and runtime trees, including resolved
symlink aliases and either direction of containment. Reject overlap before cleanup
or service quiescence. Reserve twice the payload byte limit plus 256 MiB for the
plaintext, ciphertext and metadata/compression overhead. Inventories stream paths
and stop at 50,000 entries or 8 MiB of metadata, including empty files/directories;
the final manifest is also bounded. Exceeding a limit fails the backup.

The private runtime pin file must contain the full `inventory` mapping, including
root/directories, file SHA256/size, modes and UID/GID. Hash-only pin files are
rejected; qualify the runtime metadata before migrating an existing configuration.
Recheck the copied runtime and native binary against their immutable pins. File
copies use bounded streams, including tree files and the native binary/unit, so
an overlapping oversized replacement is rejected before exceeding the budget. On the
copied state, check SQLite integrity, native store fsck, every revision's indexed
snapshot hash and archive hash, native archive validity/root, and the checked-out
snapshot hash. Never execute snapshot source. Native fsck may update the copied
process-pin journal; record the final copied inventory after verification. The
live store remains untouched by these native checks.

Status writes fsync both the temporary file and the containing directory after
rename. Under the spool lock, backup cleanup runs before constructing cloud
clients, so missing AWS tooling cannot strand plaintext from an interrupted run.

Once all required uploads and the local status file acknowledge success, a failed
freshness metric or recovery notice cannot erase that success or its daily marker.
The separate monitor retries freshness publication; recovery notices are best
effort and their submission remains distinct from inbox delivery.

The copied database must match the pinned pilot's full six-table SQL schema,
including write-path columns and uniqueness/primary-key constraints; unexpected
triggers, views or explicit indexes are rejected. Schema changes require an
operator-reviewed controller/source requalification. Future, negative or nonfinite
success timestamps make monitoring fail without publishing a fresh metric, letting
the independent missing-data alarm cover clock skew and malformed status.

The service unit also needs a full immutable `service_unit_pin` (file SHA256,
size, mode, UID/GID and kind) in private configuration; check both original and
copied unit before accepting a package. Digest reads reject links/special files
and enforce byte limits. Copied revision pins must match the receiver's exact
four-field schema with integer revisions and its snapshot/archive byte limits.
Every copied event, proposal and saved request JSON object is checked with entry,
individual-byte and aggregate metadata bounds, including events beyond the first
readiness page. Event cursor/kind/author/revision metadata and saved request cursors
must be valid. The completion metric includes elapsed monotonic time since the
recorded backup start, rather than temporarily treating a slow recovery point as
age zero. The periodic monitor continues using the recorded wall-clock timestamp.

Install `relay_validation.py` beside the controller from the same reviewed source
revision. The receiver and backup controller share data-only checkpoint/snapshot
rules: snapshot envelope/workspace/revision, selected task files/hashes, accepted
memory/citation metadata and decisions must be valid. Accepted citations remain
bound to their original source; later checkpoint changes do not rebind them. The
native binary now requires full immutable `binary_pin` metadata as well; migrate
hash-only binary configuration during qualification. Member count is capped at
11 and per-row fields are bounded in SQL before loading them into the manifest.

Pending proposal data uses the same shared schema as accepted memory. Verify the
canonical proposal ID, accepted flag, author, base snapshot and original source
citation; an accepted row must have its matching decision. Cached idempotency
responses must match their committed event/revision or proposal. Settings must
have exactly one bounded workspace/owner row whose valid owner is enabled in
membership. These checks inspect copied data and do not mutate the live relay.

Copied accepted-memory entries and decisions must name the configured workspace
owner. New write events persist request ID/hash; cached results must bind to those
fields and the exact canonical originating request hash. Legacy events remain
verifiable by reconstructing publish/propose/accept requests from retained
revision/proposal data. This avoids silently accepting swapped retry results
without requiring a migration of existing event rows. Interrupted staging and
partial ciphertext cleanup fsyncs the spool before returning or cloud setup.

Every enabled member must have a bounded, private copied credential whose token
hash and workspace match SQLite; revoked members do not need usable credentials.
Event autoincrement state must be consistent with the last cursor and leave room
for another event insert. Normal plaintext removal is fsynced before uploading,
and failure/final cleanup also flushes removals to the spool directory.

Captured history must have contiguous event cursors from 1, one originating
request per event, and every saved revision/proposal represented by its operation.
Validation compares each revision with its parent: publish changes at most its
selected checkpoint and preserves memory; acceptance preserves tasks and changes
only the proposed memory entry. Legacy publishes need one uniquely changed
checkpoint, so an ambiguous legacy no-op is rejected rather than assigned an
invented identity. New events carry the identity needed to qualify no-op writes.
Historical rows are never rewritten to manufacture missing provenance.
