# Checkpoint follow-up video plan

Keep the [current core video](demo-video.md) as the selected-source introduction.
This proposed 45-to-60-second follow-up answers a different question: when would
I need a fixed handoff for an unfinished task? Use the
[public docs checkpoint pilot](docs-checkpoint-case.md), with its recorded report
and independently checked citations. This file is a storyboard and narration
draft, not a generated or published video.

## Story and narration

| Scene | Screen | Narration |
| --- | --- | --- |
| 1. Pause | A documentation task with two pending items: README routes and video scope | You pause a task. A fresh reviewer needs the evidence and the work still left to do. |
| 2. Pack | Five public files, saved task state, pinned commit | Here, we froze five public files and a short progress note. Casita carries the snapshot. This demo adds signed pins and source checks. |
| 3. Continue | Recorded report with its two recommendation titles | One fresh reviewer read only that snapshot within its assigned scope. It suggested clearer use-case links and keeping the core video. This is its recorded report. |
| 4. Check | Context match, original Git source, 13 exact citations | The returned report was signed and checked against the original Git files, including all thirteen citations. That checks the evidence. We still judge the advice. |
| 5. Reject | Wrong parent rejected; changed progress rejected | In the replay, a newly signed report for another context is rejected. Changed progress cannot satisfy the original snapshot pin either. |
| 6. Try it | Relative command and guide link | Replay the handoff with Python or uv. No AI account required. This preserves visible task state, not a model's internal memory. |

Persistent label: **Public MIT snapshot · recorded AI review · edited replay output**.
In scene 3 also label **Instruction-limited scope; no OS sandbox claim**.
Do not animate a live model response or imply that the replay invokes a model.
The recommendation titles can be shortened for display, but label them as
summaries rather than exact quoted findings. Keep the two P3 ratings visible.

## Prepare before recording

Run the [documented replay](docs-checkpoint-case.md#replay-without-an-ai-account)
in a fresh output directory. Review the report and filtered pilot receipt before
selecting text for the screen. Use only these fields:

- The public source commit, five-file selection and two pending task labels.
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

## Audio and release status

Use the captions as the first draft. If narration is later produced, use a calm,
conversational voice and review a short sample before rendering the whole clip.
Disclose synthetic narration if used and substantial OpenAI Codex assistance.
No media generation, media-credit spend or publication is performed by these
commands or this plan. Add a second watch link to the README only after a clip
has been reviewed and published.

This pilot does not establish execution attestation, model identity, full task
completeness, deterministic AI replay, continuation quality, performance, token
savings or net storage savings. Keep those limits in the guide and a short
end-card reference to it.
