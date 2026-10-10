# Review selected source with Halo

The Mac retains Git, Casita stores, signing keys, tests and approval. Halo supplies
inference only. This adapter supplies no tools and executes no received evidence.
Use Python 3.9+, Git and OpenSSH with `ssh-keygen -Y`.

See the [recorded public trial](halo-handoff-case.md) for one recommendation,
its first rejected quote, an explicit correction and an independently checked fix.

## Prepare and preview offline

Follow the [signed selected-Git guide](git-review.md#prepare-an-explicitly-selected-context)
to prepare `output/halo-sender` and restore it into `output/halo-receiver`.
Provision signer trust independently. Check Halo's current inventory and use an
exact already-loaded model name. Replace the example signer/model below:

```sh
python3 halo_review.py request \
  --context output/halo-receiver --pins output/halo-sender/pins.json \
  --signature output/halo-sender/pins.sig \
  --allowed-signers output/halo-trust/input-signers --signer pilot-sender \
  --source . --model Halo-Qwen38-MTP --output output/halo-request
python3 -m json.tool output/halo-request/request.json
cat output/halo-request/request-sha256.txt
```

The command authenticates pins, compares restored pins, verifies immutable Git
blobs and freezes a context copy. Each line gets its original number and exact
text, including whitespace. Unselected files and dirty checkout changes are
excluded. Observations remain sender-supplied data. No inference is invoked.
Preview the entire request and retain its SHA256 independently before sending.
Signature and hash checks verify integrity, not whether content is safe to share.
Review selected source, the task, source label and optional observations for
secrets or private information; this adapter does not redact them automatically.

## Opt in to inference

Establish your own authenticated tunnel to the already-running Halo runtime.
Set `HALO_SSH_HOST` and `HALO_RUNTIME_PORT` to your verified SSH alias and port,
then run this in a separate terminal:

```sh
ssh -N -o ExitOnForwardFailure=yes \
  -L "127.0.0.1:18080:127.0.0.1:$HALO_RUNTIME_PORT" "$HALO_SSH_HOST"
```

The explicit local bind keeps this forward on loopback even when SSH's
`GatewayPorts` setting permits other interfaces. It does not create a public
listener; other processes on the Mac can still reach the local port.

Check the runtime's actual chat path: the example uses Lemonade's
`/api/v1/chat/completions`; a directly forwarded backend may use
`/v1/chat/completions`. Set `REVIEWED_REQUEST_SHA256` to the hash you reviewed:

```sh
python3 halo_review.py send --request output/halo-request \
  --request-sha256 "$REVIEWED_REQUEST_SHA256" \
  --endpoint http://127.0.0.1:18080/api/v1/chat/completions \
  --allow-inference --output output/halo-send
```

The sender requires an explicit HTTP loopback IP and port and the reviewed hash.
It rebuilds the request from frozen evidence, refuses redirects and environment
proxies, and makes one bounded POST without automatic retries. It supports a
credential-free runtime behind your authenticated tunnel, not credential-bearing
URLs or public endpoints. The request supplies no tools and sets
`allow_download: false`; your runtime must honor that option. No model is
installed or configured. Oversized responses retain only a rejected prefix.

Alternatively, in Codex check `lemonade_list_models`, then pass the unchanged
verified request to Halo's `lemonade_chat` tool. Save its complete MCP result as
`output/halo-send/response.json` without evaluating the text. This is inference,
not a Halo/Pi agent or sandbox review. The HTTP sender has a real loopback
synthetic-server test; that is not live Halo HTTP qualification.

## Validate, return and judge

```sh
python3 halo_review.py validate --request output/halo-request \
  --request-sha256 "$REVIEWED_REQUEST_SHA256" \
  --response output/halo-send/response.json --output output/halo-validated
```

This accepts a direct report, one completed HTTP chat response or one text MCP
result. It rejects truncation, tool requests, incorrect context/commit bindings,
invented quotes, changed hashes and invalid source ranges. Leading whitespace
matters. The unmodified response is retained first; rejection writes an error
receipt and no `report.json`. Never silently repair citations or execute a
returned patch. For an explicit manual correction, retain both requests and
responses and the first rejection; a retry does not erase that failure.

For an accepted report, run `git_review.py return` with
`--context output/halo-request` and `--report output/halo-validated/report.json`.
Sign on the Mac with a dedicated controller key, establish return trust
independently and run `verify` against retained sender state. Remove disposable
private keys afterward. This authenticates controller-approved bytes, not model
identity or remote execution.

Inspect the advice against original code, reproduce or test its claimed behavior,
then implement useful changes independently in a PR. Citation checks are not
reasoning validation, human approval or merge authority. A real two-person
handoff remains a separate trial. Use synthetic or selected public licensed
source for a shareable demonstration. Keep raw contexts, responses, stores,
receipts and keys under ignored `output/`; do not publish them by default.

The CLI applies umask `0077` before creating artifacts, so new directories are
owner-only (`0700`) and new files are owner-only (`0600`). This also covers
retained failure responses. It does not change permissions on older artifacts.
