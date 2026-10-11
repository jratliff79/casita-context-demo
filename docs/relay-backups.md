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
