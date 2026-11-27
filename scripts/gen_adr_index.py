"""Rebuild the ADR index in docs/adr/README.md from the ADR files themselves.

Each ADR file already says which decision it records: the number and the title
are its heading, and whether it has been replaced is its status line. The Index
table in docs/adr/README.md repeats that, and written by hand it is the one
place two open ADR pull requests always fight over: both add a row, and the
second one conflicts on a table nobody meant to arbitrate. So the table is
generated here from `docs/adr/NNNN-*.md` instead, and a pull request that adds
an ADR adds one new file and nothing else.

    python3 scripts/gen_adr_index.py                  rewrite the table
    python3 scripts/gen_adr_index.py --check          exit 1 when it is stale
    python3 scripts/gen_adr_index.py --check --base origin/main

On a pull request `--check --base` compares the table with the base branch,
not with the working tree: every ADR the base branch has needs its row and a
matching title, and the ADR the pull request itself adds may be missing one,
because the job on main adds it after the merge. On main the check is strict:
every file has its row, every row has its file.

The table is replaced in place, so the prose around it stays hand-written.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = Path("docs") / "adr"
README = "README.md"
TEMPLATE = "0000-template.md"

FILENAME = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
HEADING = re.compile(r"^#\s+(\d{4})\.\s+(\S.*?)\s*$")
SUPERSEDED = re.compile(r"^-\s+Status:\s+Superseded by\s+\[?(\d{4})", re.MULTILINE)
SECTION = "## Index"
HEADER = "| ADR | Decision |"
SEPARATOR = "|-----|----------|"


def row_text(name: str, text: str, source: str, strict_number: bool = True) -> str:
    """The index row for one ADR file, from its heading and its status line.

    `strict_number` is off for a file read from the base branch: a pull request
    is allowed to be the thing that fixes a wrong number in a heading, and the
    check must be able to pass before that fix lands.
    """
    number = FILENAME.match(name).group(1)
    lines = [line for line in text.splitlines() if line.strip()]
    heading = HEADING.match(lines[0]) if lines else None
    if heading is None:
        raise SystemExit(f"{source}: the first line should be `# {number}. Title in a few words`")
    if strict_number and heading.group(1) != number:
        raise SystemExit(
            f"{source}: its heading says ADR {heading.group(1)}, but the file is ADR {number}"
        )
    title = heading.group(2)
    superseded = SUPERSEDED.search(text)
    if superseded:
        title = f"{title} (superseded by {superseded.group(1)})"
    return f"| [{number}]({name}) | {title} |"


def working_names(root: Path) -> list[str]:
    """The ADR files in the checkout, template aside."""
    return sorted(
        path.name
        for path in (root / ADR_DIR).iterdir()
        if FILENAME.match(path.name) and path.name != TEMPLATE
    )


def base_names(root: Path, base: str) -> list[str]:
    """The ADR files the base branch has, template aside."""
    listing = subprocess.run(
        ["git", "ls-tree", "--name-only", base, f"{ADR_DIR}/"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0:
        raise SystemExit(f"git ls-tree {base} failed: {listing.stderr.strip()}")
    return sorted(
        name
        for line in listing.stdout.splitlines()
        if FILENAME.match(name := Path(line).name) and name != TEMPLATE
    )


def base_text(root: Path, base: str, name: str) -> str:
    """An ADR file as the base branch has it."""
    shown = subprocess.run(
        ["git", "show", f"{base}:{(ADR_DIR / name).as_posix()}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        raise SystemExit(f"git show {base}:{ADR_DIR / name} failed: {shown.stderr.strip()}")
    return shown.stdout


def expected_rows(root: Path, base: str | None = None) -> list[str]:
    """The rows the table should hold; with a base, ADR files as the base has them."""
    from_base = base_names(root, base) if base else []
    names = sorted(set(working_names(root)) | set(from_base))
    rows = []
    for name in names:
        if name in from_base:
            text, source = base_text(root, base, name), f"{base}:{ADR_DIR / name}"
            rows.append(row_text(name, text, source, strict_number=False))
        else:
            text, source = (root / ADR_DIR / name).read_text(encoding="utf-8"), name
            rows.append(row_text(name, text, source))
    return rows


def table_bounds(lines: list[str]) -> tuple[int, int]:
    """Where the index rows start and end, so only they are replaced."""
    try:
        start = lines.index(SECTION)
    except ValueError:
        raise SystemExit(f"{ADR_DIR / README} has no `{SECTION}` section") from None
    header = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip() == HEADER),
        None,
    )
    if header is None:
        raise SystemExit(f"{ADR_DIR / README} has no `{HEADER}` table under `{SECTION}`")
    first = header + 1
    if first < len(lines) and lines[first].strip() == SEPARATOR:
        first += 1
    last = first
    while last < len(lines) and lines[last].lstrip().startswith("|"):
        last += 1
    return first, last


def read_rows(lines: list[str]) -> list[str]:
    first, last = table_bounds(lines)
    return [line.strip() for line in lines[first:last]]


def regenerate(root: Path) -> bool:
    """Write the table; say whether anything changed."""
    path = root / ADR_DIR / README
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    first, last = table_bounds(lines)
    rows = expected_rows(root)
    if rows == [line.strip() for line in lines[first:last]]:
        print(f"{ADR_DIR / README}: {len(rows)} rows, up to date")
        return False
    path.write_text("\n".join([*lines[:first], *rows, *lines[last:]]) + "\n", encoding="utf-8")
    print(f"{ADR_DIR / README}: wrote {len(rows)} rows")
    return True


def check(root: Path, base: str | None = None) -> bool:
    """Whether the table matches the ADR files; with a base, whether it matches the base's."""
    lines = (root / ADR_DIR / README).read_text(encoding="utf-8").splitlines()
    current = read_rows(lines)
    expected = expected_rows(root, base)
    # Without a base the table must hold exactly the rows of the checkout. With
    # one, rows for ADRs the base branch doesn't have may be missing: they
    # arrive with the regeneration on main, after the merge.
    required = len(base_names(root, base)) if base else len(expected)
    matches = len(current) >= required and current == expected[: len(current)]
    if matches:
        print(f"{ADR_DIR / README}: {len(current)} rows match the ADR files")
        return True

    compared = expected if base is None else expected[: max(len(current), required)]
    print(f"{ADR_DIR / README} does not match the ADR files:", file=sys.stderr)
    print(
        "\n".join(
            difflib.unified_diff(
                current,
                compared,
                fromfile=f"{ADR_DIR / README}",
                tofile="the ADR files",
                lineterm="",
                n=1,
            )
        ),
        file=sys.stderr,
    )
    print("\nRun `python3 scripts/gen_adr_index.py` and commit the result.", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the ADR index table in docs/adr/README.md.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write the table; exit 1 when it is out of date",
    )
    parser.add_argument(
        "--base",
        metavar="REF",
        help="with --check: require the rows for the ADRs REF has, and leave REF's newcomers optional",
    )
    args = parser.parse_args(argv)
    if args.base and not args.check:
        parser.error("--base only makes sense with --check")
    if args.check:
        return 0 if check(ROOT, args.base) else 1
    regenerate(ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
