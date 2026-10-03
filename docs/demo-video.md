# A short public demo video

Aim for about two minutes. Show one missing-context exchange, using the synthetic
helper-only example from the [complete walkthrough](complete-demo.md).
Use output from an executed run. Label terminal excerpts as edited and the
reviewer as scripted; this example does not invoke an AI model.

## Story and narration

| Scene | Screen | Narration |
| --- | --- | --- |
| 1. The handoff | Capsule, request, supplement and report flow | A reviewer needs exact source context. This independent Casita demo packages selected evidence, handles a request for missing lines, and verifies the returned report. |
| 2. Store and restore | Basic demo command and passing output | Casita moves the saved graphs into fresh stores. The basic example checks shared source identity, restored file hashes and returned results from a separately trusted local checker. |
| 3. Missing helper | Request reason and explicit ranges | In this scripted synthetic review, the first capsule omits a helper. The request names the missing file and ranges, bound to the original context and Git commits. |
| 4. Explicit approval | Approved helper path, base/head ranges and preview | The example supplies a separate approval spec. Only the unchanged helper is added: six lines from each version. A request alone does not authorize access to more source. |
| 5. Verified return | Signature, parent/request and citation checks | The signed supplement stays bound to the parent and request. The returned report is checked against the original Git source, including its exact citation. These checks do not judge finding quality. |
| 6. Reject mismatches | Executed rejection-control results | The controls reject a correctly signed supplement for the wrong parent and correctly signed invented source. A valid signature alone is not enough to accept the evidence. |
| 7. Try it | Public repo and complete-walkthrough link | Run the three-stage walkthrough with Python or optional uv. It includes a recorded public AI review replay. This video uses synthetic evidence, scripted reports and edited output from real commands. |

## Run before recording

Follow the [tested build](../README.md#tested-casita-build) and keep its
`CASITA_DEMO_BIN` in this shell. From the demo root, run:

```sh
python3 demo.py --casita "$CASITA_DEMO_BIN" --output output/video-basic
python3 context_request_example.py --helpers-only \
  --casita "$CASITA_DEMO_BIN" --output output/video-request
```

Use new output names when rerunning. Inspect only selected fields from
`output/video-request/preview/request.json`, `preview/spec.json`,
`preview/preview.json`, `supplement-report.json` and `receipt.json`.
The approved paths should contain only `numbers_helper.py`, with base/head ranges
1–6, twelve selected lines and one final citation. All seven negative controls
should pass. Verify throwaway private keys were removed before completing the run.

Show the request as an unsigned structured request. Describe the separate
approval as the example's explicit spec, not a new human decision made during the
video. The initial report's zero findings does not establish correctness. The
helper report cites its supplement; the initial gate remains in the parent.

## Reproduce the displayed summaries

The video uses a local read-only helper to display selected fields, rather than
raw receipts. After the synthetic run, save this script as
`output/video-summary.py`:

```python
import json
from pathlib import Path
import sys
root=Path('output/video-request')
r=json.loads((root/'receipt.json').read_text())
assert r['ok'] and r['throwaway_private_keys_removed']
assert not (root/'throwaway-keys').exists()
mode=sys.argv[1]
if mode=='request':
    req=json.loads((root/'preview/request.json').read_text())
    print('reason:',req['reason'])
    for s in req['selections']:
        print(f"requested: {s['version']} {s['path']}:{s['start']}-{s['end']}")
elif mode=='approval':
    spec=json.loads((root/'preview/spec.json').read_text())
    print('approved paths:',', '.join(spec['paths']))
    for s in spec['selections']:
        print(f"approved: {s['version']} {s['path']}:{s['start']}-{s['end']}")
    print('selected_line_count:',r['supplement_selected_line_count'])
    print('only_approved_helper_source:',str(r['only_approved_helper_source']).lower())
elif mode=='verified':
    for key in ('input_signatures_verified','returned_reports_verified','citations_verified','final_citation_count'):
        print(key+':',str(r[key]).lower())
    print('request_binding_verified:',str(r['binding']['request_binding_verified']).lower())
elif mode=='rejections':
    for key,value in r['negative_controls'].items():
        print(key+':',str(value).lower())
else:raise ValueError(mode)
```

Run the four views:

```sh
python3 output/video-summary.py request
python3 output/video-summary.py approval
python3 output/video-summary.py verified
python3 output/video-summary.py rejections
```

In the rejection view, `true` means that invalid input was rejected by the
corresponding control. These summaries describe an executed synthetic run; they
do not replace the signed-context and original-Git verification in the example.

## Keep the recording public-safe

Record a single clean terminal window, or render allowlisted excerpts from its
executed output. Use the synthetic fixture only. Keep prompts generic and display
relative paths or the literal `$CASITA_DEMO_BIN` variable. Do not show the desktop,
notifications, personal home paths, environment dumps, signing files, raw receipts
or archives. Review all visible text and the audio transcript before sharing.
Keep generated video and raw material under ignored `output/` until reviewed.

Use a persistent label: **Synthetic fixture · scripted reviewer · edited terminal
output**. Caption any separate historical review clip as **recorded AI review**.
If using a synthetic voice, disclose that in the video credits. Credit substantial
AI assistance in the recording guide and video credits.

Casita supplies object storage and verified graph transport. This independent
demo supplies manifests, OpenSSH signatures, request binding and citation checks.
Do not claim execution attestation, model identity, sandbox enforcement, finding
quality, performance, token savings or net storage savings. Build waits and output
can be edited for length, but do not present scripted or replayed reports as live
model discoveries.

The script and documentation were developed with substantial OpenAI Codex
assistance. A generated draft is a review artifact; no video is published by this
guide or its demo commands.

The [sanitized onboarding receipt](onboarding-validation.json) records the tested
locked build, three-stage walkthrough, optional uv quick start and local video
draft. It does not indicate public video publication.
