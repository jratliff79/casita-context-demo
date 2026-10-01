# Local validation

This records the earlier `b8366af1` macOS build. The current source pin in
[CI](../.github/workflows/ci.yml) is validated separately on hosted Linux.
Changing that pin does not change the historical local receipt below.

On 2026-10-01, fourteen unit checks passed and the real Casita end-to-end demo
completed all 33 commands successfully on macOS arm64. The context archive
was 5,880 bytes, with two distinct context roots and one shared unchanged
source directory identity. Both restored contexts matched their expected
keys and file hashes. Altered evidence was rejected, and both isolated stores
passed `fsck --dry-run`.

The demo's separately trusted local checker produced the expected rejection for
v1 and acceptance for v2. Received source was not executed. Both results were
exported in a 2,076-byte Casitar, fully verified and imported into a third fresh
store. Result file hashes, context bindings, checker identities and verdicts
matched the original sender contexts. Altered results, altered results with new
hash pins, and results bound to the other context were rejected. The receiver
was audited again after adding results, and the return store passed its audit.

The import assigned v2 to `received/0` and v1 to `received/1`. The wrapper
matched both roots by their expected directory identities, without assuming
archive order preserved the requested version order.

The [sanitized receipt summary](validation.json) records the tested Casita
source revision, executable hash, context pins, archive hash and shared object
key, plus result-return pins and controls. Raw commands and results remain under
ignored `output/result-return-checker-validation/receipt.json`; they can contain local paths.

Before publication, packaging was changed from copying a whole source directory
to selecting only three intended fixture files. Regression checks confirm that
Python caches and unselected local files are excluded and linked fixture inputs
are rejected. The real run then restored exactly those three evidence files and
their manifest in each context. This review covers the demo's selected synthetic
inputs, not arbitrary future user-supplied data or Casita's implementation.

A regression test places an overriding regular `fixtures` package on Python's
search path. The checker is loaded directly from the same fixed repository bytes
that are hashed, so that foreign package cannot supply a different callable.

Reproduce from the repository root with the tested Casita build:

```sh
python3 -m unittest discover -s tests -v
python3 demo.py --casita /path/to/casita --output output/validation
```

These are local fixture, deterministic-check and graph-transport checks. No independent AI reviewer,
remote machine, real media tool, storage benchmark or execution attestation was
part of this demonstration. Root identities may vary if source bytes or file
executable modes change; the demo derives and verifies fresh pins on each run.
