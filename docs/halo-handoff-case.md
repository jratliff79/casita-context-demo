# A numbered public-source request to Halo

On 2026-10-09, the operator asked Halo whether the teammate summary exposed the
original citations beside proposed knowledge. The signed capsule selected the
summary script, its tests, a documentation range and the MIT license from
[public commit 619a230](https://github.com/jratliff79/casita-context-demo/tree/619a230a8cedeca36fc1a5d95a61eabfb07a68b4).
`halo_review.py request` verified the input signature and Git source, froze the
context, and supplied explicit original line numbers. The operator previewed the
public payload and used the existing Halo MCP inference tool with no model tools.

Halo recommended displaying each proposal's already-verified citation beside its
statement. Its first quote omitted leading indentation, so the unchanged strict
validator rejected that report. The operator made one explicit correction request
about whitespace. The second report passed the same citation and context checks;
neither response was silently repaired. Both requests, responses and validation
receipts remain private under ignored `output/`.

The operator reproduced the omission using the trusted local summary code,
implemented the field independently, and extended regression checks for both
selected and unselected advice. Existing invented-citation checks continue to
reject altered source even when the proposal is rehashed. The returned report
was exported through Casita, signed with a dedicated Mac controller key and
verified in a fresh store against the retained sender's original Git context.
Disposable private keys were removed afterward.

The [public validation metadata](halo-handoff-validation.json) records content
hashes, the first failure, manual correction and verification boundaries. The
[operator guide](halo-handoff.md) explains repeating the stages. This records a
useful source-cited recommendation after one correction, not first-pass success,
a model-quality benchmark, a live HTTP transport test or authenticated human
approval. Casita and final tests stayed local; no remote Halo Casita import or
two-person handoff occurred. At recording time, implementation acceptance was
pending the owner's ordinary PR review and merge.
