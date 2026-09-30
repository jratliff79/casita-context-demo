# Local validation

On 2026-09-30, nine unit checks passed and the real Casita end-to-end demo
completed all 20 commands successfully on macOS arm64. The portable archive
was 5,880 bytes, with two distinct context roots and one shared unchanged
source directory identity. Both restored contexts matched their expected
keys and file hashes. Altered evidence was rejected, and both isolated stores
passed `fsck --dry-run`.

The import assigned v2 to `received/0` and v1 to `received/1`. The wrapper
matched both roots by their expected directory identities, without assuming
archive order preserved the requested version order.

The [sanitized receipt summary](validation.json) records the tested Casita
source revision, executable hash, context pins, archive hash and shared object
key. Raw commands and results remain under ignored
`output/ai-disclosure-validation-2/receipt.json`; they can contain local paths.

Before publication, packaging was changed from copying a whole source directory
to selecting only three intended fixture files. Regression checks confirm that
Python caches and unselected local files are excluded and linked fixture inputs
are rejected. The real run then restored exactly those three evidence files and
their manifest in each context. This review covers the demo's selected synthetic
inputs, not arbitrary future user-supplied data or Casita's implementation.

Reproduce from the repository root with the tested Casita build:

```sh
python3 -m unittest discover -s tests -v
python3 demo.py --casita /path/to/casita --output output/validation
```

These are local fixture and graph-transport checks. No independent AI reviewer,
remote machine, real media tool, storage benchmark or execution attestation was
part of this demonstration. Root identities may vary if source bytes or file
executable modes change; the demo derives and verifies fresh pins on each run.
