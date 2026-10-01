# Pinned image and context handoff

`environment.py` exports three Casita roots together: two synthetic investigation
contexts and one reviewed public Python OCI image layout. A fresh receiver restores
all three by their expected directory keys. Returned results travel through a second
Casitar and a third fresh store, then are checked against the original sender evidence.

The reviewed image pins are in [the environment fixture](../fixtures/environment.json).
They were resolved from the official public `python:3.12-alpine` image on 2026-10-01.
Imports use the selected platform's full manifest digest, never the discovery tag.
The fixture also pins the config digest. Tags may advance; changing these pins is a
reviewed repository change. Public registry access is anonymous and requires a network
connection during preparation. The image download limit is 25 MB, and the combined
Casitar is bounded to 30 MB. A registry outage or rate limit fails the run.

## Transport check

Build the [pinned Casita source](../README.md#tested-casita-build) with OCI support:

```sh
cargo build --release --locked --package casita --bin casita --features oci
```

Upstream ignores `Cargo.lock`. Copy this demo's `ci/Cargo.lock` into that checkout
first, as described in [the dependency snapshot guide](../ci/README.md).

From this demo's repository root, select that executable and a new output directory:

```sh
python3 environment.py --casita /path/to/oci-enabled-casita \
  --output output/environment --platform linux/amd64 --runtime none
```

This verifies the restored image's selected manifest, config, platform, every blob's
digest and size, and exact file set. It rejects links, extra files, oversized metadata
and unsupported descriptors. The wrong-image-pin control calls the runtime loader
with a mismatched expected manifest and confirms rejection before any runtime command
or image tar creation. The checker runs on the host in this mode; the image is not
loaded or executed. Hosted CI uses this mode on Linux with the amd64 image pin.

## Apple Container trial

On a Mac with Apple Container running, select its native architecture:

```sh
python3 environment.py --casita /path/to/oci-enabled-casita \
  --output output/environment-apple --platform linux/arm64 --runtime apple
```

After verification, a narrow adapter wraps the restored layout in an OCI tar for
`container image load`. It adds a temporary local name to the transport index;
the image manifest, config and original layer bytes stay unchanged. The runtime's
readback must report that name, the exact selected manifest, the expected platform
and the same config content before execution. The top-level transport-index digest
changes when adding the name and is not treated as the image-manifest digest.

The worker uses one CPU and 512 MB of memory, networking disabled, a read-only image
filesystem, read-only synthetic contexts and separately provisioned trusted checker
code. Only a dedicated result directory is writable. It executes the reviewed local
checker; the checker source inside the received context is only compared and hashed.
No host home, SSH agent, credentials or ports are mounted. Python runs with `-I -B`.
The disposable VM is removed after exit, and the temporary image name is deleted.
The existing builder is not modified. This is a small fixture execution, not a
general service for accepting arbitrary images or code from a context recipient.

The host checks both worker result files before packaging the return, and checks
them again after restoring into a third store. The result schema is the existing
deterministic context/checker binding; it does not attest which machine or image
executed it. The receipt records the selected image and local runtime readback as
diagnostics. These records are not authenticated worker identities or execution
attestations. All three Casita stores receive an integrity audit.

## Recorded scope

The [sanitized trial summary](pinned-environment.json) records the executed macOS
arm64 trial. It uses public image blobs and synthetic context/result fixtures.
Hosted CI independently tests amd64 image transport and a trusted host check;
it does not exercise the Apple Container adapter. Raw receipts, image archives,
stores and copied trusted code stay under ignored `output/` and can contain local
paths. This local simulation generates its own expected pins; a real handoff must
deliver expected pins through a trusted independent channel.
