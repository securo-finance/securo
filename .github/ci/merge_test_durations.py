"""Fold the per-shard test durations CI measured back into backend/.test_durations.

Each backend shard runs with --store-durations, which rewrites its copy of
.test_durations with fresh timings for the tests it ran and keeps every other
entry as it was at the start of the run. So for every test, the copies of the
shards that did not run it agree on the old value, and the one that ran it
holds the new value: the value that disagrees with the others is the fresh one.
A test added since the last refresh is only in the copy of the shard that ran
it. Comparing the copies with each other, and not with the file on disk, keeps
the result the same however many times the merge is run.

Entries for deleted tests stay; pytest-split ignores tests it doesn't collect.

Usage, from the repository root, with the id of a CI run on the branch (pass
the copies of all four shards of that one run):

    gh run download <run-id> --pattern 'test-durations-*' --dir /tmp/durations
    python3 .github/ci/merge_test_durations.py /tmp/durations/*/.test_durations
"""

import json
import sys
from collections import Counter
from pathlib import Path

COMMITTED = Path(__file__).resolve().parents[2] / "backend" / ".test_durations"
SHARDS = 4


def merge(copies: list[dict[str, float]]) -> tuple[dict[str, float], int]:
    """The fresh duration of every test, and how many tests were re-measured."""
    merged: dict[str, float] = {}
    measured = 0
    for test in set().union(*copies):
        values = Counter(copy[test] for copy in copies if test in copy)
        if len(values) == 1:
            # Every copy agrees: a test only one shard knows (it's new), or one
            # whose re-measured time came out identical.
            merged[test] = next(iter(values))
            measured += sum(values.values()) == 1
            continue
        if len(values) != 2 or min(values.values()) != 1:
            raise ValueError(f"{test}: the copies disagree in a way one run can't produce: {dict(values)}")
        merged[test] = min(values, key=values.__getitem__)
        measured += 1
    return merged, measured


def main(paths: list[str]) -> int:
    if len(paths) != SHARDS:
        print(__doc__)
        print(f"Expected the {SHARDS} copies of one run, got {len(paths)}.")
        return 2
    copies = [json.loads(Path(path).read_text()) for path in paths]
    try:
        merged, measured = merge(copies)
    except ValueError as error:
        print(f"Not merged: {error}. Pass the four copies of a single CI run.")
        return 1
    # Same layout pytest-split writes: sorted keys, four-space indent.
    COMMITTED.write_text(json.dumps(merged, indent=4, sort_keys=True))
    print(f"{len(merged)} tests, {measured} re-measured.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
