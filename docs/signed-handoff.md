# Signed context and result pins

Hashes establish correspondence between pins and bytes. To authenticate those
pins, a receiver also needs a key it already trusts. This example adds detached
[OpenSSH signatures](https://man.openbsd.org/ssh-keygen) to the portable worker
workflow. It verifies the exact bytes of `pins.json` before initializing a
receiver store or importing an archive. Schema, archive, root, context and
returned-result verification still run afterward.

## Run the synthetic round trip

Use Python 3.9+, the default Casita CLI and an OpenSSH `ssh-keygen` supporting
`-Y sign` and `-Y verify` on macOS or Linux. From this repository:

```sh
python3 signed_handoff.py --casita /path/to/casita --output output/signed-demo
```

The output directory must be new. The script creates separate sender and worker
Ed25519 keys with a synthetic comment, plus an outsider key for rejection checks.
Unencrypted demo private keys live temporarily in an output subdirectory with
mode `0700`; OpenSSH creates the key files with mode `0600`. The script removes
that directory in its cleanup path on both success and handled failure. An abrupt
process kill or machine shutdown can interrupt cleanup. These keys must never be
used for any real account or shared as credentials.

No existing SSH key, agent, account or global Casita store is used. All generated
files and raw receipts remain under ignored `output/`; no keys or raw receipts
are uploaded by CI. Raw receipts contain local paths and CLI output.

The script simulates receiver provisioning by writing public allowed-signers
files separately from the sender and worker archives. The same local controller
creates both the signing keys and those trust files. This is a mechanics test,
not independent proof of a real sender's identity or a secure key exchange.

The input signature uses namespace `casita-context-demo.input-pins.v1`, and the
result signature uses `casita-context-demo.result-pins.v1`. Each allowed-signers
file restricts its key to the expected namespace and synthetic identity. A result
signature therefore cannot authenticate input pins. OpenSSH verifies the full
message and authorization with `-Y verify`, not just its signature structure.

## Separate receiving commands

For the outputs from that run, the worker command can be repeated in a new store:

```sh
python3 handoff.py work --casita /path/to/casita \
  --archive output/signed-demo/sender/handoff.casitar \
  --pins output/signed-demo/sender/pins.json \
  --signature output/signed-demo/sender/pins.sig \
  --allowed-signers output/signed-demo/sender-allowed-signers \
  --signer synthetic-sender --output output/signed-worker-repeat
```

Verify the original signed result return similarly:

```sh
python3 handoff.py verify --casita /path/to/casita \
  --archive output/signed-demo/worker/handoff.casitar \
  --pins output/signed-demo/worker/pins.json \
  --signature output/signed-demo/worker/pins.sig \
  --allowed-signers output/signed-demo/worker-allowed-signers \
  --signer synthetic-worker --original output/signed-demo/sender \
  --output output/signed-return-repeat
```

All three authentication options are required together. Omitting all of them
retains the original unsigned workflow, which requires pins from a trusted
independent channel. A receiver requiring signatures must supply those options
from its own configuration. It must never accept an allowed-signers file or an
expected identity supplied by the archive sender as its source of trust.

`authentication.py` reads bounded regular files without final-component links,
snapshots the signature and trusted signer file into a private temporary directory,
and passes the pin bytes to OpenSSH on stdin. It returns that same byte buffer for
schema parsing, so replacing `pins.json` during verification cannot substitute a
different message afterward. This does not protect an already compromised host
or a receiver's mutable trust configuration.

## What the controls establish

The demo first completes signed input and result handoffs in three fresh stores,
then checks results against the original sender contexts. It also prepares a
valid alternative archive with changed synthetic tasks and matching new manifest,
directory and archive pins. The unsigned worker accepts that consistent
replacement, illustrating its documented trusted-channel requirement.

Four signed-path controls must fail specifically at signature verification,
before receiver-store initialization or archive import:

- Replacement archive and replacement pins paired with the original signature.
- Replacement pins signed by an unknown key claiming the expected identity.
- Original pins signed by the authorized key under the result namespace.
- Original signature verified against the wrong expected identity.

Signatures prove that a holder of an authorized signing key signed these pin
bytes. They do not prove freshness, prevent replay, establish truth of observations,
or attest to execution, sandboxing or environment identity. The returned verdict
is still checked against original contexts using separately trusted local code.
Received code is never executed. No image runtime or physical remote machine is
part of this example. Key distribution, rotation, revocation, replay policy and
production credential storage remain outside this small simulation.

## Recorded scope

The local check on 2026-10-01 used the default-feature Casita build at source
`aed18e32704c8f2bf821cc038720a77610a600ba` on macOS arm64, and OpenSSH 10.3p1.
The signed input/result round trip, original-context result verification,
unsigned replacement control and all four pre-import rejection controls passed.
The baseline demo passed separately. The source pin identifies the CLI build;
these signatures cover context/result pins, not the CLI or execution history.
The machine-readable [sanitized check](signed-handoff.json) records this scope
without local paths, keys, signatures, raw commands or received evidence.
