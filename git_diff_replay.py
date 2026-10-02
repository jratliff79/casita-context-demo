#!/usr/bin/env python3
"""Replay the recorded capsule-only review of public PR 16 without a model."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import git_diff_example as example

SOURCE = json.loads((Path(__file__).parent / "fixtures/diff-review-source.json").read_bytes())


def run(args):
    return example.run(args, source=SOURCE, report_path=args.report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path(__file__).parent / "docs/diff-review-report.json")
    parser.add_argument("--output", type=Path, required=True)
    try:
        os.umask(0o077)
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Recorded Git diff replay failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
