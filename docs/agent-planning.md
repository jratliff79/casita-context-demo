# Let two assistants agree on a work split

Two assistants receive the same approved task capsule, exchange a proposal and a
counterproposal, then explicitly agree to the same plan. The result is ready for
human review. It does not authorize either assistant to start work.

See the [recorded Codex/Halo exchange](agent-planning-case.md) for a live,
operator-relayed trial, separate from the scripted offline example.

## Run the offline demonstration

Use the [shared prerequisites](../README.md#prerequisites) and the
[tested Casita CLI](../README.md#tested-casita-build):

```sh
python3 agent_planning.py demo --casita "$CASITA_DEMO_BIN" \
  --output output/my-planning-demo
python3 -m json.tool output/my-planning-demo/summary.json
```

Optional uv: prefix commands with `uv run --no-project --python 3.12`.
No AI account or inference service is needed. The participants are scripted and
the retry task is synthetic. Real Casita archive exports, imports, checkouts and
integrity audits run in separate local stores.

The builder initially offers implementation, tests and documentation. The tester
counterproposes taking tests. Both explicitly agree to the revised plan. All five
snapshots remain available under `turn-0` through `turn-4`; each has an independent
restored copy, summary and transport receipt. `summary.json` records both role
agreements, exact plan identity and `human_approval_performed: false`.

## Use real assistants, with an explicit operator relay

The CLI has no network client or automatic inference loop. Start a fresh run:

```sh
python3 agent_planning.py start --casita "$CASITA_DEMO_BIN" \
  --output output/planning-start
```

Read `bundle/task.json`, `bundle/policy.txt` and `summary.json`. Independently
retain the exact state hash from `state-sha256.txt`, then prepare the next request:

```sh
python3 agent_planning.py request --bundle output/planning-start/received \
  --state-sha256 "$REVIEWED_STATE_SHA256" --model "$LOADED_MODEL" \
  --output output/planning-request-1
python3 -m json.tool output/planning-request-1/request.json
cat output/planning-request-1/request-sha256.txt
```

Preview the entire request before sharing it. It contains only the fixed
synthetic task, policy and bounded retained transcript. It does not read arbitrary
repositories, chat exports, personal memory, SSH keys or environment variables.
Use an approved assistant channel. For Halo, check the currently loaded model,
pass the unchanged request to `lemonade_chat` with `allow_download: false` and no
tools, and retain the complete MCP result locally. Another assistant can produce
the direct reply JSON from the same request. These are separate opt-in actions;
preparing a request does not invoke a model.

Retain the independently reviewed request hash and the unchanged response:

```sh
python3 agent_planning.py reply --casita "$CASITA_DEMO_BIN" \
  --request output/planning-request-1 --request-sha256 "$REVIEWED_REQUEST_SHA256" \
  --response output/operator-relay/response-1.json --output output/planning-turn-1
python3 -m json.tool output/planning-turn-1/summary.json
```

For each next turn, use the preceding turn's `received` bundle and independently
retained state hash. Roles alternate between builder and tester. A proposal
resets earlier agreements; both roles must subsequently return `agree` with the
exact same plan. Proposing alone never counts as agreeing. Stop at
`ready_for_human_review` or `turn_budget_exhausted` (six turns maximum).
There is no automatic retry, silent repair or work-execution command. If an
assistant needs more context, stop and have the operator review that request.

Rejected raw responses are retained with a rejection receipt; they do not become
a new accepted bundle. Stale parents, wrong roles, changed requests, added tools,
invented citations, unknown deliverables, duplicate ownership and dependency
cycles reject. Response text is never executed.

## What this does and does not establish

This is a sequential, operator-relayed protocol, not a connection between
teammates' Codex accounts, a chat service, shared memory or a general collaboration
engine. Stores are separate, but transport runs on one host. No execution,
network isolation, reasoning quality or model identity is attested. Citation
checks do not establish that a plan is useful.

Role labels and unsigned locally retained pins do not authenticate people or
prove two independent agents participated. Replacing an entire run and its
trusted pins coherently is outside these checks. For real people and sensitive
tasks, establish signer trust and transport access controls independently using
the [signed handoff protocol](signed-handoff.md), and review selected content for
secrets. Hashes are not credentials or automatic redaction.

This first version accepts only its fixed synthetic capsule. Keep requests,
responses, stores and receipts under ignored `output/`; do not publish them by
default. Review the resulting plan through an ordinary human decision or PR
before granting any execution authority.
