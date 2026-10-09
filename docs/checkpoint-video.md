# Checkpoint handoff video

Keep the [current core video](demo-video.md) as the selected-source introduction.
This 56-second follow-up answers a different question: when would
I need a fixed handoff for an unfinished task? It uses the
[public docs checkpoint pilot](docs-checkpoint-case.md), with its recorded report
and independently checked citations.

https://github.com/user-attachments/assets/17d5742d-156d-4c4b-a456-2bbdeb9a078f

[Run this example](docs-checkpoint-case.md#replay-without-an-ai-account) ·
[See both videos](../README.md#videos)

The clip shows authored graphics from allowlisted public evidence, with edited
replay results and a recorded AI report. It is not live model output or a screen
recording of a fresh reviewer. Narration is synthetic Achird speech generated
with Palmier Pro's Gemini 3.1 Flash TTS. The video and documentation used
substantial OpenAI Codex assistance. The [publication receipt](checkpoint-video-validation.json)
records the reviewed media hash, public source pin and technical checks.

## Story and narration

| Scene | Screen | Narration |
| --- | --- | --- |
| 1. Pause | A documentation task with three pending items: README routes, video scope and missing-context assessment | You pause a task. A fresh reviewer needs the evidence and the work still left to do. |
| 2. Pack | Five public files, saved task state, pinned commit | In this Casita example, we freeze selected public files and a short progress note. Casita carries the snapshot. This demo adds signed pins and source checks. |
| 3. Continue | Recorded report with its two recommendation titles | One fresh reviewer read only that snapshot within its assigned scope. Its recorded report suggested clearer use-case links and keeping the core video. |
| 4. Check | Context match, original Git source, 13 exact citations | The signed return was checked against the original Git files, including all thirteen citations. That checks the evidence. We still judge the advice. |
| 5. Reject | Wrong context rejected; changed progress rejected | A newly signed report for another context is rejected. Changed progress cannot satisfy the original snapshot pin either. |
| 6. Try it | Relative command and guide link | Replay with Python or uv. No AI account required. This preserves visible task state, not a model's internal memory. |

Persistent label: **Public MIT snapshot · recorded AI review · edited replay output**.
In scene 3 also label **Instruction-limited scope; no OS sandbox claim**.
Do not animate a live model response or imply that the replay invokes a model.
The recommendation titles can be shortened for display, but label them as
summaries rather than exact quoted findings. Keep the two P3 ratings visible.

## Reproduce the evidence

Run the [documented replay](docs-checkpoint-case.md#replay-without-an-ai-account)
in a fresh output directory. Review the report and filtered pilot receipt before
selecting text for the screen. Use only these fields:

- The public source commit, five-file selection and all three pending task labels:
  README routes, video scope and missing-context assessment. These readable labels
  summarize `assess-readme-use-case-routing`, `recommend-video-scope` and
  `identify-missing-context`; do not imply any item was omitted from the snapshot.
- Two recommendation titles and P3 ratings, labelled as a recorded AI report.
- Successful context, signature, original-Git and citation checks from the replay.
- The `wrong_context` and `rehashed_mixed_progress` rejection booleans, with
  `true` explained as invalid input rejected. The mixed-progress check is local
  validation against the original pin, not another signed return.

Show a compact relative command, retaining the literal `CASITA_DEMO_BIN` variable:

```sh
python3 git_review_replay.py --case docs-checkpoint --source . \
  --casita "$CASITA_DEMO_BIN" --output output/my-docs-checkpoint
```

Use caption-led motion: pause the task card, collect the selected documents into
a capsule, reveal the recorded report, then show each verification step. Keep
movement slow enough to read. The floating dot can guide attention between these
steps, but should not suggest an autonomous action the replay does not perform.
Avoid showing raw command logs, stores, archives, keys, absolute local paths,
desktop notifications or environment dumps. Check every displayed frame and
the complete transcript before sharing.

## Audio and publication

The owner reviewed a short Achird sample and the full narrated clip before
authorizing publication on GitHub. The 1080p, 30 fps video has sentence-timed
captions, synthetic-voice disclosure and AI-assistance credits. Scene timing
follows the generated narration; the floating dot guides attention through the
recorded steps. Technical checks covered full media decoding, caption fit,
sampled encoded frames, audio levels and reviewed public text and metadata.

The example commands reproduce the signed evidence handoff. They do not render
this animation, generate speech, invoke an AI reviewer or publish media. Local
render sources and raw media remain under ignored `output/`; the public clip's
transcript and filtered publication receipt are retained here.

This pilot does not establish execution attestation, model identity, full task
completeness, deterministic AI replay, continuation quality, performance, token
savings or net storage savings. Keep those limits in the guide and a short
end-card reference to it.
