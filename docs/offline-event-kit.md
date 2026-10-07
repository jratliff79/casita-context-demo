# Take a frozen event kit to the venue

Before an event, freeze the approved rundown and supporting assets locally. When
a last-minute cue changes, transfer the update, verify the selected version, and
keep working from that local snapshot when the sender is unavailable.

This runnable example uses one synthetic event, two cues, a generated SVG title
and a text operator card. It is inspired by an Eventools-style workflow but is
independent of Eventools: it reads no production exports, private events or media,
and does not integrate with the application or drive a show.

## Run

From the demo root with Python 3.9+ and the
[tested default Casita CLI](../README.md#tested-casita-build):

```sh
python3 event_kit.py --casita "$CASITA_DEMO_BIN" --output output/my-event-kit
```

Or use optional uv:

```sh
uv run --no-project --python 3.12 python event_kit.py \
  --casita "$CASITA_DEMO_BIN" --output output/uv-event-kit
```

Only the Python standard library and Casita are required at runtime. No account,
signing keys, real event data, image runtime or media tools are needed. Choose a
fresh relative `output/<directory>`. Existing destinations and linked output
parents are rejected; generated data remains ignored under `output/`.

Expect seven `PASS` lines:

```text
PASS: synthetic v1 kit synced and selected with exact rundown and assets
PASS: v2 incremental sync sends fewer payloads; unchanged assets retain identity
PASS: transfer and stale candidate leave v1 active until verified v2 selection
PASS: venue store restores exact v2 with original files and sender path unavailable
PASS: welcome cue remains 18:15; no received code or media executed
PASS: mixed rundown, altered or missing asset and rebound event metadata rejected
PASS: venue store passes integrity audit
```

## Transfer is separate from selection

Each version contains exactly these files:

```text
manifest.json
rundown.json
assets/title.svg
assets/operator-notes.txt
```

The manifest records the synthetic event ID, version and exact file hashes. Sender
pins identify that manifest and its Casita directory key. The rundown has explicit
cue IDs, clock times and asset references; verification rejects missing or linked
files, unexpected paths, invalid clocks and duplicate cues.

The script imports v1 under `kit/synthetic-demo/v1` and syncs it to a fresh venue
store using Casita's local repository sync. It checks the restored kit before
selecting it as `kit/current`. V2 changes only the welcome cue from 18:10 to 18:15
and the manifest version. Both assets remain byte-identical.

V2 sync uses `--incremental` against the already populated venue repository. The
example checks shared asset-directory identity and records Casita's transfer
progress. Its cold and update payload counts describe this fixture's transfer
work; they do not measure network bandwidth, latency or net disk savings. With
incremental discovery, Casita can skip a complete verified destination subtree,
so `payloads-reused` need not count every object beneath the shared asset root.
See the official [sync guide](https://casita.rs/guides/sync/) for that policy.

Syncing the version root does not select it as current. A deliberately stale
candidate must fail before checkout or selection, leaving v1 active. Then the
script restores the separately pinned v2, checks every file and its rundown
schema, and only then selects v2. This is scripted selection, not proof of a
human's approval or authorization.

## Continue from the venue store

After selection, the script removes only its own generated kit directories and
renames its own sender store so the original source path is unavailable. It then
uses only venue-repository commands to restore the current snapshot under
`venue-only-v2`. That checkout must exactly match the original v2 file hashes and
retain the 18:15 welcome cue.

The example rejects a v1 rundown mixed into v2, replaced or missing artwork, and
an event-ID change even if the changed manifest receives a new hash. The original
expected event identity remains fixed. It finishes with a venue-store integrity
audit. `receipt.json` records pins, progress reports, command ordering, original
hashes and rejection controls. Inspect `venue-only-v2/rundown.json` as data.

This models loss of access to a sender with two repositories on one host. It does
not turn off the host network, test a venue outage or cross-host transfer, or
measure live Eventools behavior. No received code, SVG or media is executed or
played. A working checkout can later be edited; reverify it before relying on it.

Pins must come through an independently trusted channel in a real handoff. These
unsigned hashes verify correspondence, not freshness, signer identity, show
safety or execution. The [signed handoff](signed-handoff.md) demonstrates signer
verification separately. Keep raw receipts and stores private; receipts contain
local paths, and synthetic fixtures are not a sanitization method for real event
exports. Review the complete selected content before real sharing.
