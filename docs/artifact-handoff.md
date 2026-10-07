# Restore yesterday's build without rebuilding

Use this when you need an exact previous output after its build directory has
disappeared, or want to hand that output to someone who does not need the source
or build tools. This synthetic example builds two tiny static sites, stores their
output graphs, and restores either version into a fresh Casita repository.

## Run

From the demo root, install Python 3.9+ and the
[tested Casita CLI](../README.md#tested-casita-build), then run:

```sh
python3 artifacts.py --casita "$CASITA_DEMO_BIN" --output output/my-artifacts
```

Or use optional uv to select Python 3.12:

```sh
uv run --no-project --python 3.12 python artifacts.py \
  --casita "$CASITA_DEMO_BIN" --output output/uv-artifacts
```

Python uses only the standard library. No AI account, OpenSSH signing keys,
registry image or container runtime is required. Use a fresh relative
`output/<directory>` each time. Existing destinations and symlinked output parents
are rejected. All stores, receipts and generated fixtures stay in ignored
`output/`. The script accepts no external source directory.

Expect seven `PASS` lines:

```text
PASS: two site builds record source and recipe hashes, toolchain metadata and output hashes
PASS: changed HTML has a new identity; unchanged CSS shares one identity
PASS: original source and build directories removed before receiver import
PASS: both versions restored exactly in a fresh store without rebuilding
PASS: current root rolled back from v2 to v1 with verified output
PASS: wrong archive pin, altered output, extra file and rebound metadata rejected
PASS: wrong version rejected; sender and receiver pass integrity audits
```

## What happens

The generated page changes a synthetic event's door time from 18:00 to 18:30.
Both versions use the same stylesheet. A separately trusted
[local recipe](../fixtures/artifacts/build.py) renders the generated JSON and CSS
once per version. The script executes the exact local recipe bytes it hashes;
received code is never executed.

Each stored bundle contains only:

```text
manifest.json
outputs/index.html
outputs/style.css
```

The manifest records source-file SHA-256 hashes, the local recipe's SHA-256,
Python implementation and version, host OS and architecture, and output-file
SHA-256 hashes. A build-input ID hashes the source hashes, recipe hash and
toolchain metadata together. A separate manifest ID pins the complete manifest,
including the output hashes. Toolchain metadata describes the sender's runtime;
it does not package Python or establish a hermetic environment.

The changed input and HTML have different identities. The example checks the
actual Casita tree entries to show the unchanged CSS shares one stored object
identity. It retains both artifact versions under named roots and exports only
those bundles into a Casitar. Source files and the builder are not included.

Before receiver import, it removes its own generated source and build directories.
It verifies the archive against the sender's expected hash, imports it into a
fresh store and matches roots by pinned directory key rather than import index.
Both checkouts must have the exact original file set and hashes. The receiver
does not invoke the builder, install a sender toolchain or execute the HTML.

Finally, it moves `artifacts/current` from v2 back to v1 and checks the restored
v1 bundle. It rejects altered HTML, an unexpected file, mismatched version and
rebound recipe metadata even when the altered manifest has a new manifest pin.
The expected build-input ID remains independently fixed for that last control.
Both stores receive integrity audits.

Inspect `received/v1`, `received/v2` and `received/rollback` under your output
directory. `receipt.json` records the CLI commands, pins, original output hashes,
sharing observation and rejection controls. Raw receipts contain local paths;
keep them and the stores private. Review artifacts before any real sharing.

## What this proves

The executed checks demonstrate exact artifact retention, graph transport and
rollback after the original generated build directories are deleted. They also
demonstrate correspondence with independently expected build metadata. The sender
builds a synthetic site; this is not a production build-cache integration.

Casita verifies stored content, not the claim that an arbitrary artifact was
produced by a particular compiler or recipe. These manifests are declarations
bound to sender pins, not signed build provenance or execution attestations.
A real recipient must obtain expected pins through a trusted independent channel;
replacing both the archive and all trusted pins is outside this unsigned example.
The [signed handoff](signed-handoff.md) demonstrates signer verification separately.

This example does not measure build-time or net-storage savings, execute restored
applications, test cross-host behavior or reproduce an arbitrary build. It shows
shared CSS identity without treating that as a storage benchmark. The static site
uses synthetic data throughout.
