# Choose a demonstration

Start with the [complete walkthrough](complete-demo.md). The guides below
cover individual protocols, historical reviewer trials and optional runtimes.
All host Python commands use the [shared prerequisites](../README.md#prerequisites).

| Demonstration | Entry point | Requirements beyond Python 3.9+ |
| --- | --- | --- |
| Two context versions and verified result return | [Basic quick start](../README.md#run-it) | Default Casita CLI |
| Separate sender, worker and return-verifier processes | [Portable worker guide](portable-worker.md) | Default Casita CLI on each side |
| Signed input and result pins with pre-import rejection checks | [Signed handoff guide](signed-handoff.md) | Default Casita CLI and OpenSSH `ssh-keygen -Y` on macOS or Linux |
| Public source capsule and signed AI review findings | [Review capsule guide](review-capsule.md) | Default Casita CLI, OpenSSH, Git and the pinned public source commit |
| Selected Git lines and a signed static-review return | [Git review guide](git-review.md) | Default Casita CLI, OpenSSH and Git; synthetic walkthrough included |
| Recorded public Git review, six verified citations and reproduced size bug | [Selected-Git review case](git-review-case.md) | Default Casita CLI, OpenSSH, Git and the pinned public source commit; no AI account |
| Base/head diff capsule with versioned citations | [Git diff review guide](git-diff-review.md) | Default Casita CLI, OpenSSH and both pinned Git commits; public PR walkthrough included |
| Preview an explicit diff scope and suggest source ranges before signing | [Diff scope preview guide](git-diff-preview.md) | Git and both pinned commits; no Casita or signing key needed |
| Recorded capsule-only review of a real public diff, with an empty return | [Recorded diff review case](diff-review-case.md) | Default Casita CLI, OpenSSH and both pinned public commits; no AI account |
| Missing-context request with an unchanged-helper-only signed supplement | [Context request guide](context-requests.md) | Default Casita CLI, OpenSSH and Git; no AI account |
| Image and context transport with a trusted host check | [Environment transport guide](pinned-environment.md#transport-check) | Casita built with `oci`; public registry access |
| Trusted checker in the restored image | [Apple Container guide](pinned-environment.md#apple-container-trial) | OCI-enabled Casita, public registry access and Apple Container running on a Mac |

The image examples select a reviewed public image by its full manifest digest.
They verify its manifest, config, layers and platform before runtime loading.
`--runtime none` runs the checker on the host; `--runtime apple` runs it in the
restored image. Both use synthetic evidence and the separately trusted checker.
The [physical worker trial](physical-worker.md) records a Mac-to-Linux
round trip over SSH. The image guide records local Mac/Linux VM trials.
Execution attestation remains outside these checks.

## When a reviewer needs more source

The [synthetic context-request example](context-requests.md) starts with an
incomplete capsule, records a request tied to its identity and commits, then
previews an explicitly approved signed supplement. The receiver checks the parent
and request binding before a source-bound report is returned. It uses scripted
reports, no AI model or received-source execution.

The [recorded public reviewer trial](context-request-case.md) shows an actual
request for three dependencies and a changed assessment after supplementation.
Its replay needs no AI account. The reported concern reproduced a documented
protocol limitation; it is not presented as a newly confirmed defect.

For a verified review that led to a merged fix, read
[from signed capsule to landed fix](review-to-fix.md). The
[selected-Git review case](git-review-case.md) records a separate public review
with six source citations and a locally reproduced report-size bug. Historical
reports retain their original source pins; they describe the versions reviewed.
