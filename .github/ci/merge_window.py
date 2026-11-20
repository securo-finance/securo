#!/usr/bin/env python3
"""When the fast lane is allowed to merge: not from Friday 18:00 to Monday 01:00.

Tassio's rule of 20 Nov 2026: from Friday 18:00 until Monday 01:00 nobody
merges, the automated lane included. The rest of the week it merges exactly as
it did before.

The window is wall-clock time in America/Sao_Paulo. The zone is named here and
never taken from the machine: the answer cannot depend on the runner's local
time zone, and the cron that starts a sweep is UTC (17 * * * *). `zoneinfo`
applies the zone's own rules, so the check also stays right on a date the zone
changed its offset; America/Sao_Paulo has had no daylight saving since 2019.

Usage:

    python3 .github/ci/merge_window.py
    python3 .github/ci/merge_window.py --at 2026-11-20T21:00:00Z

It always exits 0. The answer is `open=true|false`, written to $GITHUB_OUTPUT
when that variable is set (or to --github-output), and a line in the log:
`inside the merge window ...` or `outside the merge window ...`. The workflow
skips its merge step on `open=false`, which is how a weekend run leaves every
pull request untouched instead of failing.

`workflow_dispatch` is not held by the window: a person asking for a merge by
hand on a Saturday is asking on purpose. That exception lives in the workflow,
not here.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Sao_Paulo")

# Monday is 0, so Friday is 4. Both boundaries are wall-clock minutes.
FRIDAY = 4
MONDAY = 0
CLOSE_AT = 18 * 60      # Friday 18:00
OPEN_AGAIN_AT = 1 * 60  # Monday 01:00


def is_open(now: datetime) -> bool:
    """Whether the lane may merge at `now`, an aware datetime.

    A naive datetime is refused: reading it would need the machine's local
    time zone, and that is what this check must never depend on.
    """
    if now.tzinfo is None:
        raise ValueError("is_open needs an aware datetime, not the machine's local time")
    local = now.astimezone(ZONE)
    weekday = local.weekday()
    minute = local.hour * 60 + local.minute
    if weekday == FRIDAY and minute >= CLOSE_AT:
        return False
    if weekday in (5, 6):
        return False
    if weekday == MONDAY and minute < OPEN_AGAIN_AT:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--at",
        help="ISO-8601 timestamp to judge instead of now; without an offset it is read as UTC",
    )
    parser.add_argument(
        "--github-output",
        help="file to write open=... to; default: $GITHUB_OUTPUT when it is set",
    )
    args = parser.parse_args(argv)

    if args.at:
        now = datetime.fromisoformat(args.at.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    opened = is_open(now)
    local = now.astimezone(ZONE)
    if opened:
        print(
            f"inside the merge window at {local:%Y-%m-%d %H:%M %Z} "
            f"(America/Sao_Paulo); the fast lane may merge."
        )
    else:
        print(
            f"outside the merge window at {local:%Y-%m-%d %H:%M %Z} "
            f"(America/Sao_Paulo); the fast lane merges again Monday 01:00."
        )

    target = args.github_output or os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as output:
            output.write(f"open={'true' if opened else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
