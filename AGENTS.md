# Working on this example

Keep this a small independent Casita demonstration. Use synthetic fixtures;
do not import private repositories, incident records, credentials or local paths
into tracked examples. Do not execute evidence supplied by a context recipient.

Before completing code changes, run:

```sh
python3 -m unittest discover -s tests -v
python3 demo.py --output output/a-new-directory
```

Pass `--casita` when the executable is not on PATH. Always use a new output
directory and explicit stores. Generated artifacts stay under ignored `output/`.
Keep claims tied to the recorded check: content identity is not execution
attestation, and shared objects alone are not a net-storage or performance result.
