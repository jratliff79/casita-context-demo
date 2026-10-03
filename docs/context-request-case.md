# A public reviewer asks for missing context

A fresh Codex reviewer read an initially incomplete capsule for the public MIT
[PR #19](https://github.com/jratliff79/casita-context-demo/pull/19). The pinned base
is `23bae1883cfa6cbaa696e4408f15ef268fff503c`; the reviewed PR head is
`133459e56fce11e93358a6f1b9b1b9cff9985a5b`.

This trial records a real request and reassessment. It measures neither review
quality nor token savings, and its reported concern is a documented limitation,
not a newly confirmed correctness defect.

## The exchange

The initial signed capsule contained only `context_request.py` head lines 1–158
and the MIT license: 179 selected lines. The sender observation listed public
candidate paths, line counts and blob hashes, without supplying their source.
The reviewer received only the verified context and pins. It had no earlier
findings or expected defect, and was instructed to use no checkout, Git objects,
network or received-code execution. This was an instruction boundary, not an
OS-enforced sandbox.

The [initial report](context-request-initial-report.json) established no finding
and named omitted dependency contracts. The reviewer independently wrote a
[request](context-request-recorded-request.json) for three complete head ranges:

| Requested public source | Lines | Question |
| --- | ---: | --- |
| `git_diff_review.py` | 1–260 | Selection shape and verification guarantees |
| `git_diff_plan.py` | 1–208 | Preview contract and source exposure |
| `review_handoff.py` | 1–223 | Input-pin validation and authentication boundary |

The controller separately approved those paths/ranges and retained the original
helper/license selections. It inspected a 870-line unsigned preview, then signed
and restored the supplement in a fresh Casita store. The receiver checked the
parent identity, frozen commits, canonical request hash and range coverage before
the same reviewer reassessed it. The [source fixture](../fixtures/context-request-source.json)
pins both snapshots of every exposed path, explicit base absence of the new helper,
the inventory observation, approved specs and recorded artifact hashes.

The [supplement report](context-request-supplement-report.json) raised one P2
concern with four exact citations. The [unsigned assessment](context-request-assessment.json)
explains which interface questions were resolved and which omitted primitives,
tests and documentation remained unknown. The controller returned both reports
through signed result capsules and verified their bindings and original Git
citations. The return signature identifies the controller's key, not an
independently authenticated model identity.

## What changed, and what the report does not prove

The supplement resolved uncertainty about selection dictionaries, preview
exposure and pin parsing. It also led the reviewer to report that a supplement
approved only for unchanged dependencies fails the inherited requirement that an
allowlisted diff contain a change.

A separate controller reproduction using trusted repository code confirmed the
error `allowlisted paths contain no changes`. The explicit approval validator
accepts the requested unchanged paths, but the preview builder rejects them
before creating output. The rejection comes from this demo's Python diff
builder. This reproduction executed trusted local tool code,
not restored evidence; the reviewer performed static analysis only.

The controller then checked the pinned guide. It already states that at least
one approved path must contain a change, as required by the existing diff
protocol. Our actual supplement retained the changed request helper and succeeded.
The reviewer had not received that documentation and explicitly listed it as
missing. We preserve its report unchanged and classify the concern as a
**reproduced, documented protocol limitation**, not a newly confirmed defect.
The current [context-request walkthrough](context-requests.md) now supports
unchanged-only supplements through an explicit supplement schema. This recorded
trial and its replay remain pinned to the earlier combined-supplement behavior.

This case shows a request narrowing uncertainty and changing a review conclusion.
It also shows why exact citations, signatures and a reproduced error do not by
themselves establish that a reported behavior violates the intended contract.
One reviewer and one supplement do not establish general review effectiveness.

## Replay without a model

Use Python 3.9+, Git, OpenSSH signing support and the
[tested Casita CLI](../README.md#tested-casita-build). From a full clone:

```sh
python3 context_request_replay.py --source . \
  --casita /path/to/casita --output output/recorded-context-request
```

For a shallow clone, fetch both public commits first:

```sh
git fetch origin 23bae1883cfa6cbaa696e4408f15ef268fff503c
git fetch origin 133459e56fce11e93358a6f1b9b1b9cff9985a5b
```

Optional uv:

```sh
uv run --no-project --python 3.12 python context_request_replay.py --source . \
  --casita /path/to/casita --output output/recorded-context-request-uv
```

The replay checks all four recorded artifact hashes and both public source
allowlists before creating output or keys. It recreates the initial observation,
returns the recorded first report, applies the explicit approved spec to the
recorded request, verifies the signed supplement association, and returns the
recorded second report. JSON values are preserved; the result transport uses
canonical JSON. It pins the unsigned assessment but does not sign it as a report
or treat its reasoning as verified truth.

The six independent rejection controls cover unapproved source, wrong parent
context ID, directory key, base/head commits, and a correctly signed supplement
report presented for the initial capsule. They do not insert findings into either
recorded report.

Expected final output:

```text
PASS: recorded public context request, approved supplement and original-Git report returns
```

The replay invokes no model and applies no patch. It verifies transport, request
association and four citations, not the historical reviewer session, controller
adjudication or finding quality. It does not repeat the historical unchanged-only
reproduction. Its receipt marks `fresh_ai_review: false`,
`review_quality_verified: false`, `received_code_executed: false`,
`execution_attested: false` and `os_sandbox_enforced: false`.

Throwaway private keys are removed, including on handled failure. Raw artifacts,
source restorations, trust files and command logs stay under ignored `output/`.
Only explicitly allowlisted public licensed source metadata, recorded review text
and a sanitized validation receipt are tracked. CI replays the recorded exchange
without a model and uploads no raw review artifacts.

The [sanitized local validation receipt](public-context-request-validation.json)
records the actual trial, direct/uv replays, local CI assertions, required basic
demo and 134 tests on Python 3.9 and 3.12. It contains public identities and
check results; raw command outputs remain ignored.
