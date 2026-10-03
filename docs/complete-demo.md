# Run the complete Casita context demo

This walkthrough covers the core demonstration: shared source identity, verified
transport, an explicit request for missing context, an approved signed supplement,
and signed reports checked against original Git. It uses synthetic fixtures and
allowlisted public MIT source. It requires no AI account or Python packages.

## Start from a fresh clone

Have Python 3.9+, Git, OpenSSH signing support and the
[tested Casita CLI](../README.md#tested-casita-build). The
[prerequisites](../README.md#prerequisites) explain installation and optional uv.
Casita is a separate executable; uv supplies Python and does not install Casita.

```sh
git clone https://github.com/jratliff79/casita-context-demo.git
cd casita-context-demo
export CASITA_DEMO_BIN=/path/to/casita
```

Use a full clone so the recorded public source commits are available. Replace the
executable path with your tested Casita build. All commands below use fresh stores
and new output directories; choose different names when rerunning them.

## Run the three stages

```sh
python3 demo.py --casita "$CASITA_DEMO_BIN" --output output/complete-basic

python3 context_request_example.py --helpers-only \
  --casita "$CASITA_DEMO_BIN" --output output/complete-request

python3 context_request_replay.py --source . \
  --casita "$CASITA_DEMO_BIN" --output output/complete-recorded
```

Optional uv: replace each `python3` with
`uv run --no-project --python 3.12 python`. No project installation is required.

| Stage | What its passing result checks |
| --- | --- |
| Basic transport | Unchanged source shares an identity; restores match pinned roots and file hashes; trusted local checker results return and match the original contexts |
| Missing-context exchange | A scripted reviewer requests an omitted helper; an explicitly approved supplement contains only that unchanged helper; signed inputs and reports, parent/request binding and original-Git citation checks pass |
| Recorded public review | The actual public reviewer request and two recorded reports replay unchanged as JSON values; source/artifact pins, signed handoffs and four citations pass |

The missing-context stage has seven rejection controls, including a correctly
signed wrong-parent supplement and correctly signed invented helper source.
The recorded stage has six independent rejection controls. Neither stage
executes received source or invokes a model.

Each stage prints `PASS` messages and writes a `receipt.json` in its output
folder. The request receipt records `helpers_only_supplement: true`,
`only_approved_helper_source: true`, `supplement_selected_line_count: 12`,
`final_citation_count: 1`, and seven successful negative controls. Throwaway
private keys are removed on success and handled failure.

## Understand the result

These stages complete the core workflow. For the actual reviewer trial and its
controller adjudication, read [the public case](context-request-case.md).
The recorded P2 concern was a documented limitation of the earlier demo protocol;
its replay does not measure review quality or repeat the historical reproduction.
The current workflow supports unchanged-only supplements explicitly.

Casita identity verifies content correspondence. It does not attest model identity,
execution, sandbox enforcement, finding quality or merge authority. The basic
stage executes the repository's trusted local checker; it does not execute a
checker received in a capsule. The other stages are static evidence handoffs.

Keep raw `output/` artifacts local. Receipts and logs may contain local paths;
archives and source restorations are not automatic publication artifacts.
Additional OCI, physical-worker and source-selection examples in the README are
optional extensions to this completed core demonstration.

The [sanitized validation receipt](unchanged-supplement-validation.json) records
all three stages passing from a fresh public clone with baseline Python and
optional uv, using the documented existing host prerequisites. CI separately
checks the signed workflows on Linux.
