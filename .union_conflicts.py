#!/usr/bin/env python3
"""Resolve the remaining rebase conflicts by keeping both sides.

Every conflict between PR #1573 (flag) and PR #1574 (read state) is
additive: each side adds its own declaration, parameter or prop. Taking
both sides is the intent; anything that duplicates an identifier shows up
in tsc or eslint and is fixed by hand afterwards.
"""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent
names = subprocess.run(
    ["git", "diff", "--name-only", "--diff-filter=U"],
    cwd=root, capture_output=True, text=True, check=True,
).stdout.split()

for name in names:
    path = root / name
    text = path.read_text()
    out = []
    state = None
    for line in text.splitlines(keepends=True):
        if line.startswith("<<<<<<< "):
            state = "ours"
        elif line.startswith("=======") and state == "ours":
            state = "theirs"
        elif line.startswith(">>>>>>> ") and state == "theirs":
            state = None
        else:
            out.append(line)
    path.write_text("".join(out))
    print(name)
