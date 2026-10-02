# Example receiver diagnosis

In v1, conversion process exit zero does not imply verification acceptance.
The source has AAC audio, but its first packet has `duration: "N/A"`.
`source/verify.py` returns unknown timing for any nonnumeric or nonpositive
packet duration, so the later numeric packets cannot rescue the span.
The verifier then rejects unavailable source timing. Output audio is present
with finite duration; these observations do not establish audio loss.

The negative first PTS is finite and passes the timestamp condition. It is
the unknown duration that matters. V2 provides the discriminating control:
only the first packet duration changes to 0.021 seconds, producing a 0.063-second
source span and acceptance against the 0.063-second output.

Output duration must also be a positive finite numeric value. Negative, zero,
nonfinite, nonnumeric and boolean values represent unavailable timing and are
rejected before the shortening comparison. This includes a JSON number such as
`1e309` that overflows to infinity when decoded by Python.

For real evidence, preserve strict rejection until complete stream timing can
be established. Capture direct trusted-tool responses and distinguish authentic
unknown timing from any adapter manipulation. Do not silently discard a packet
or substitute container duration merely to pass the guard.

This diagnosis concerns a synthetic fixture and a teaching verifier. It does
not claim media corruption, a real tool defect, a production reproduction or
attested execution. A real reviewer should cite the verified source lines and
observation fields it actually read.
