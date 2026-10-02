# From signed capsule to landed fix

An AI reviewer found a real bug in this small public demo using only a verified
source capsule. Its findings came back through Casita, their source references
checked against the original snapshot, and a regression-tested fix merged.
This is the recorded path from portable AI context to a concrete code change.

```mermaid
flowchart LR
    S[Pinned public source] --> C[Signed Casita capsule]
    C --> A[Capsule-only AI review]
    A --> R[Signed findings return]
    R --> V[Verify source citations]
    V --> F[Merged fix and tests]
```

1. **Pin the input.** The sender selected twelve MIT-licensed files from this
   repository at [commit `de2c03a`](https://github.com/jratliff79/casita-context-demo/tree/de2c03a89ceef6a0b751ed137c91a4710dccaf19).
   The [allowlist](../fixtures/review-source.json) records each file's SHA-256.
   A task and manifest completed the capsule. Casita exported its graph as a
   Casitar; OpenSSH signed the separate pins. A fresh receiver checked the
   signature against a locally provisioned demo key, then verified the archive,
   directory key and files before the agent read them.

2. **Review that snapshot.** One Codex agent was instructed to read only the
   restored capsule and execute no received code. It returned
   [one P2 finding](review-report.json): the synthetic timing checker could
   accept a negative output duration for a short source span, or infinity
   decoded from JSON `1e309`. The published snapshot was unmodified. The report
   also states its review limits.

3. **Verify the return.** The controller signed the report's pins with a
   separate throwaway key and moved the result into a fresh store. Verification
   bound it to the original context and source commit, checking the cited file
   hash and exact lines. Signed reports with a wrong context or invented excerpt
   were rejected.
   [PR #9](https://github.com/jratliff79/casita-context-demo/pull/9) landed this
   workflow; the [sanitized trial receipt](review-validation.json) records the checks.

4. **Confirm and fix the bug.** The orchestrator reproduced the two examples
   using the separately trusted local checker. The fix now requires a positive
   finite numeric output duration and rejects booleans. Regression tests cover
   the reported cases, negative timestamps, shortening tolerance and JSON
   overflow through context/result verification.
   [PR #10](https://github.com/jratliff79/casita-context-demo/pull/10)
   merged as [commit `a2a809f`](https://github.com/jratliff79/casita-context-demo/commit/a2a809f97ebb2821eac058d5caef3836f13fbc5c).
   All 57 tests and all four
   [PR CI checks](https://github.com/jratliff79/casita-context-demo/actions/runs/36970523190)
   passed before merge.

## Replay the handoff

Use Python 3.9+, Git, OpenSSH signing support on macOS or Linux, and the
[tested default Casita CLI](../README.md#tested-casita-build). From a full clone
of this repository containing the pinned source commit:

```sh
python3 review_replay.py --casita /path/to/casita --source . \
  --report docs/review-report.json --output output/review-story
```

Use a new output directory. For shallow clones, see the
[source preparation guide](review-capsule.md#replay-the-recorded-report).
The command creates fresh stores, checks both signatures and the report's
citations, rejects invalid reports, and removes its throwaway private keys on
success or handled failure. Keep raw artifacts private under ignored `output/`.

This transports the **recorded report** without invoking AI or executing packaged
source. The report still names the pre-fix snapshot, so its citations remain
checkable. For a fresh review, use the
[four-role workflow](review-capsule.md#run-a-new-capsule-only-review).

Casita supplied graph identity and transport; the example added manifests,
signatures and source-bound reports. Citations alone do not prove a finding:
reproduction and tests supported this fix. Demo keys and agent instructions do
not attest to identity, sandboxing or execution. This independent example uses
public source and synthetic timing data, with substantial Codex assistance.
