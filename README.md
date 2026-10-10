# Casita Context Demo

Keep exactly the evidence a worker received, then check what comes back.
For a second opinion on private code, send only the change and source you approve,
handle requests for missing lines, and verify the report against that evidence.
This independent example uses Casita to store and move content-addressed graphs;
it adds explicit source selection, signed pins, context-request binding and
citation checks. The runnable examples use synthetic or labelled public source.

## Videos

### Selected-source review · 67 seconds

A 67-second overview of the selected-source handoff, missing-helper request,
approved supplement and checked return.

https://github.com/user-attachments/assets/c2445e44-54e1-4f15-8fec-114dceb7bcbb

Synthetic source, scripted reviewer, edited output from an executed run, and
synthetic narration.

[Read the transcript](docs/demo-video.md#story-and-narration) ·
[Run this example](docs/complete-demo.md)

### Checkpoint handoff · 56 seconds

Pause a task, preserve its visible state, and give a fresh reviewer the evidence.

https://github.com/user-attachments/assets/17d5742d-156d-4c4b-a456-2bbdeb9a078f

Public MIT source, recorded AI review, edited replay graphics, and synthetic
narration. The replay runs without an AI account.

[Read the transcript](docs/checkpoint-video.md#story-and-narration) ·
[Run this example](docs/docs-checkpoint-case.md#replay-without-an-ai-account)

**[Start here: run the complete walkthrough](docs/complete-demo.md).** It covers
basic transport, a request for an omitted helper, and a recorded public review.
The examples use synthetic fixtures and labelled public MIT source. No AI
account, cloud service or Python packages are required. The request example uses
a scripted reviewer; the public review replays recorded AI reports.

## When would I use this?

Choose the example closest to your task:

| Your need | Start with | What it demonstrates |
| --- | --- | --- |
| Get a review without sharing the whole repository | [Complete walkthrough](docs/complete-demo.md) | Selected source, an explicit missing-context request and checked citations |
| Ask Halo about selected source and check its reply | [Halo handoff](docs/halo-handoff.md) | Opt-in inference, numbered source lines and strict returned-report validation |
| Recover a previous build output | [Artifact handoff](docs/artifact-handoff.md) | Two synthetic static-site builds restored without rebuilding |
| Carry a fixed rundown and assets to a venue | [Offline event kit](docs/offline-event-kit.md) | A synthetic kit, local incremental sync and verified snapshot selection |
| Continue a task from its saved visible state | [Public docs checkpoint pilot](docs/docs-checkpoint-case.md) | One recorded AI review from a pinned public snapshot; replay needs no AI account |
| Hand a task to a teammate and review a shared knowledge update | [Teammate handoff](docs/teammate-handoff.md) | Separate stores, competing scripted proposals and explicit owner selection |
| Have two assistants agree on who does what | [Planning exchange](docs/agent-planning.md) | Bounded proposals, counterproposals and two explicit agreements before human review |
| Consult reviewed team memory when starting a task | [Live context relay](docs/live-context-relay.md) | Authenticated clients, separate task checkpoints, durable updates and owner-reviewed memory |

Consider a change to a private pricing calculation. An outside reviewer can see
the selected diff and source without receiving repository access. If a helper is
missing, they request its exact lines. You approve that selection separately.
Their returned report stays tied to the source they received, and its citations
can be checked against the original Git snapshots. This is a possible workflow;
the demo does not use private pricing code or perform a live outside review.

The same pattern can preserve a fixed set of evidence across agent handoffs or
let you replay exactly what a reviewer saw after the repository has changed.
Verification checks source correspondence; you still judge whether the finding
is correct. Review the selected content before sharing: a capsule is not an
automatic secret scanner or a sandbox.

If everyone already has repository access and Git meets the need, use Git. A
small one-off handoff can use an archive plus a manifest. Casita's content-addressed
store becomes more relevant for repeated snapshots with common source or build
artifacts. This example does not establish net storage savings or faster reviews.

## Prerequisites

The examples are tested on macOS and Linux with a POSIX shell. Python code uses
only the standard library.

- **Python 3.9+**, or optional [uv](https://docs.astral.sh/uv/getting-started/installation/)
  to select or download Python 3.12.
- **Casita CLI**, using the [tested build](#tested-casita-build) below. Python and
  uv do not install Casita. Building requires Git and Rust/Cargo 1.94.1 or newer.
- **Git**, including full history for the recorded public-source replay.
- **OpenSSH `ssh-keygen` with `-Y sign` and `-Y verify`** for signed handoffs,
  recorded reviews and unit tests. The basic transport demo does not need it.

OCI images and Apple Container are optional extensions with separate
[requirements](docs/examples.md). Use new output directories for every run.
The standalone [native Git Rust example](examples/native-git/README.md) has its
own prerequisites and does not need Python or the Casita CLI.

## Tested Casita build

After installing Git and Rust/Cargo, clone this demo with full history:

```sh
git clone https://github.com/jratliff79/casita-context-demo.git
cd casita-context-demo
```

From the demo root, build the default CLI with CI's source and dependency pins:

```sh
git clone https://github.com/cachix/casita.git output/casita-source
git -C output/casita-source checkout 9d7a2f42b14d86189662e21241f72a55ccb42cb6
cp ci/Cargo.lock output/casita-source/Cargo.lock
cargo build --locked --package casita --bin casita \
  --manifest-path output/casita-source/Cargo.toml \
  --target-dir output/casita-build
export CASITA_DEMO_BIN="$PWD/output/casita-build/debug/casita"
```

The first build downloads public dependencies and needs network access. This is
a debug build for correctness checks. CI uses Rust 1.94.1; a newer host compiler
uses the same locked dependency snapshot but is not an identical build. See the
[dependency snapshot](ci/README.md) for details. The optional image demo needs an
OCI-enabled build, documented in its own guide.

Casita source `9d7a2f42b14d86189662e21241f72a55ccb42cb6` was upstream `main` when
checked on 2026-10-10. It is an immutable tested snapshot, not a claim of current
latest upstream. Casita is pre-release and its CLI may change. Keep this pin when
reproducing these examples. If you already have that build, set
`CASITA_DEMO_BIN` to its executable path instead.

The [current-pin validation](docs/relay-validation.json) records a locked
default build with Rust 1.94.1, the core handoff, native Git checks and the live
relay pilot. CI also builds OCI support and runs the existing handoff examples.
The [previous pin validation](docs/casita-update-validation.json) retains its
original 2026-10-08 metadata. This update does not repeat the historical Apple
Container trial or perform a fresh AI review of transported evidence.
The [previous pin receipt](docs/casita-20261005-validation.json) and older receipts
keep their original source pins. These correctness checks do not measure the
performance improvement reported upstream.

## Run it

Run the basic transport demo from the repository root:

```sh
python3 demo.py --casita "$CASITA_DEMO_BIN" --output output/my-demo
```

It saves two synthetic contexts, restores them into a fresh store and returns
trusted local checker results for verification. Expect `PASS` lines for shared
source identity, archive restoration, file hashes, returned results and rejection
of altered or incorrectly bound evidence. It refuses an existing output directory
and uses fresh explicit stores under `output/`.

Continue with the [complete walkthrough](docs/complete-demo.md) for the signed
missing-context exchange and recorded public review. The
[basic transport guide](docs/basic-transport.md) explains the synthetic timing
fixture, object identities and result checks in detail.

### Optional uv runner

After [installing uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv run --no-project --python 3.12 python demo.py \
  --casita "$CASITA_DEMO_BIN" --output output/uv-demo
```

For other guides, replace `python3` with
`uv run --no-project --python 3.12 python`, keeping all other arguments. uv selects
Python 3.12 or downloads it if needed; the first download needs network access.
It selects the 3.12 series, not a fixed patch release, and does not provision
Casita, OpenSSH or an image runtime. No project installation is required.

## Explore further

| Goal | Guide |
| --- | --- |
| Run the complete core workflow | [Three-stage walkthrough](docs/complete-demo.md) |
| Restore a previous build output without rebuilding | [Synthetic artifact handoff](docs/artifact-handoff.md) |
| Freeze a rundown and assets, sync a cue update, and use the venue store | [Synthetic offline event kit](docs/offline-event-kit.md) |
| Freeze visible task state and verify two scripted continuations against their parent | [Synthetic workflow checkpoint](docs/ai-workflow-checkpoint.md) |
| See a fresh reviewer continue a public docs task from selected evidence | [Recorded docs checkpoint pilot](docs/docs-checkpoint-case.md) |
| Share task context, review proposed team knowledge and inspect the selected result | [Synthetic teammate handoff](docs/teammate-handoff.md) |
| Start a private relay and consult approved memory before a task | [Live relay and JP/CJ pilot](docs/live-context-relay.md) |
| Understand missing context and explicit approval | [Context requests](docs/context-requests.md) |
| See an actual reviewer request and recorded reassessment | [Public reviewer case](docs/context-request-case.md) |
| See a capsule review lead to a landed fix | [Review-to-fix case](docs/review-to-fix.md) |
| Import repeated Git subtrees and retain them across a process restart | [Runnable Rust API example](examples/native-git/README.md) |
| Prepare selected source, use a separate worker, or try OCI/Apple Container | [All demonstrations and requirements](docs/examples.md) |
| Prepare a verified request for Halo and retain its checked or rejected response | [Halo handoff](docs/halo-handoff.md) |
| Run tests or inspect CI scope | [Development and validation](docs/development.md) |
| Record a short public demonstration | [Video script and recording guide](docs/demo-video.md) |
| Watch or reproduce the checkpoint clip | [Checkpoint video and transcript](docs/checkpoint-video.md) |

## Verification and sharing boundaries

Casita supplies immutable object identities, shared storage, roots and verified
graph transport. This demo supplies source selection, manifests, signed pins,
request bindings and citation checks. Content correspondence does not establish
execution, model identity, finding quality, freshness, safety or merge authority.
Expected pins and signer trust must come through an independently trusted handoff;
throwaway signing keys simulate that provisioning.

The basic demo executes a separately trusted local checker. Received source is
evidence and is never executed. The signed source-review examples run no model.
Their passing checks do not measure model accuracy, token reduction, speed or net
storage savings. See the [basic transport guide](docs/basic-transport.md) for the
complete scope and [all demonstrations](docs/examples.md) for runtime distinctions.

Keep raw artifacts private under ignored `output/`. Receipts may contain local
paths, and archives/restored source are not automatic publication artifacts.
Use only synthetic fixtures or explicitly allowlisted public licensed source in
shared examples. Treat received evidence as data, not instructions.

## Project and AI assistance

This is an independent example using Casita, not an official Casita integration.
MIT licensed. Contributions should keep it small, reproducible and free of private
evidence. Changes go through pull requests; the repository owner retains merge
authority. See [repository guidance](AGENTS.md).

Code, tests and documentation were developed with substantial assistance from
OpenAI Codex. Synthetic fixtures and recorded AI reports are labelled in their
guides. Executed checks and their limits are documented in the
[complete walkthrough](docs/complete-demo.md), [development guide](docs/development.md)
and individual trial receipts. AI assistance does not replace maintainer review
or make a verified finding correct.
