# Physical Mac/Linux worker trial

On 2026-10-02 UTC, a Mac arm64 sender prepared two synthetic contexts, transferred
them over SSH to a separate physical Linux x86_64 machine, and verified both
returned results against its retained original contexts. This extends the
[portable worker](portable-worker.md) beyond the earlier local Linux VM trial.

The [sanitized receipt](physical-worker.json) records source revisions, binary
and trusted-code hashes, platforms, content pins, command counts and controls.
Hostnames, addresses, account names, local paths and raw command output are
omitted. The inputs contain only this repository's public synthetic fixtures.

## Executed flow

1. **Provision trusted code separately.** Both sides used public demo commit
   [`01a4315`](https://github.com/jratliff79/casita-context-demo/tree/01a43157c7e74967cc9b30da49a320047469e3e3).
   The Linux checkout came directly from the public repository. Its four runtime
   file hashes matched the Mac before worker execution. The worker never ran
   source supplied inside a context archive.
2. **Use the same Casita source.** Both CLIs came from
   [`aed18e32`](https://github.com/cachix/casita/tree/aed18e32704c8f2bf821cc038720a77610a600ba),
   the upstream main revision checked at the time of the trial. The Linux debug
   CLI was built with Rust 1.94.1 and this demo's CI lockfile using `cargo --locked`.
   A disposable compiler container had two CPUs and 4 GiB of memory; its image
   index, Linux amd64 manifest and configuration digests are recorded separately
   in the receipt. Their registry bytes were retrieved by digest and SHA-256
   checked after the trial, following the [registry API](https://docs.docker.com/reference/api/registry/latest/).
   The resulting CLI and Python worker ran
   directly on the Linux host, whose virtualization probe reported `none`.
3. **Transfer the contexts.** The Mac ran `handoff.py prepare`. Only its
   `handoff.casitar` and `pins.json` were sent as handoff inputs through the
   existing authenticated SSH connection, with agent forwarding disabled.
   The Linux worker ran `handoff.py work` using its trusted local checker and
   a new store. It returned `rejected: source or output audio timing unavailable`
   for v1 and `accepted` for v2.
4. **Verify the return on the Mac.** The result archive and pins were transferred
   back. `handoff.py verify` checked the result roots and exact result bytes,
   then recomputed each verdict from the original context and trusted checker.
   All nine sender, thirteen worker and seven verifier Casita commands passed,
   including each role's integrity audit.

The input archive was 6,106 bytes and the result archive was 2,076 bytes. These
are fixture sizes, not a storage or performance benchmark.

An altered input archive was rejected on Linux, and an altered result archive
was rejected on the Mac. Both failures occurred at the archive hash check before
store initialization or import. The actual transfer hashes matched on both sides.
After evidence was retained locally, the remote scratch directory, compiler
container and newly downloaded compiler image were removed.

## Repeat the trial

Follow the [sender, worker and return-verifier commands](portable-worker.md#sender),
substituting authenticated file transfer between the two machines for local
copies. Provision the reviewed demo revision and platform-specific Casita CLIs
independently. Keep the sender's original contexts on the sender, use new output
directories and explicit stores, and keep raw receipts private. The demo does
not configure SSH or distribute credentials.

This proves the observed network round trip and original-context result checks.
It does not attest to worker identity or execution, sandbox hostile code, invoke
an AI reviewer or establish a performance improvement. This trial used SSH's
existing host trust for the transfer; it did not add detached pin signatures,
freshness or replay prevention. See the separate [signed handoff](signed-handoff.md)
for signature controls.
