"""Fold the per-shard test durations CI measured back into backend/.test_durations.

Each backend shard runs with --store-durations, which rewrites its copy of
.test_durations with fresh timings for the tests it ran and keeps every other
entry. So a test's new duration is in whichever shard copy differs from the
committed file, and a test added since the last refresh is in the copy of the
shard that ran it. Entries for deleted tests stay; pytest-split ignores tests
it doesn't collect.

Usage, from the repository root, with the id of a CI run on the branch:

    gh run download <run-id> --pattern 'test-durations-*' --dir /tmp/durations
    python3 .github/ci/merge_test_durations.py /tmp/durations/*/.test_durations
"""

import json
import sys
from pathlib import Path

COMMITTED = Path(__file__).resolve().parents[2] / "backend" / ".test_durations"


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    committed: dict[str, float] = json.loads(COMMITTED.read_text())
    merged = dict(committed)
    updated = 0
    for path in paths:
        for test, duration in json.loads(Path(path).read_text()).items():
            if committed.get(test) != duration:
                merged[test] = duration
                updated += 1
    # Same layout pytest-split writes: sorted keys, four-space indent.
    COMMITTED.write_text(json.dumps(merged, indent=4, sort_keys=True))
    print(f"{len(merged)} tests, {updated} durations updated from {len(paths)} shards.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
