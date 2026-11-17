"""Every file path AGENTS.md and the ADRs cite exists.

The map in AGENTS.md is only useful while it's true: a renamed file it still
cites sends a reader, or a coding agent, to look for code that isn't there.
This fails the build instead.

A cited path is a backticked token that names a file or directory: it contains
a slash, is a dotfile (`.env.example`), or ends in a source extension. Paths are resolved from the places the
documents write them relative to (the repository root, backend/, backend/app/,
frontend/, frontend/src/). A bare file name, like `format.ts`, must exist
somewhere among the files git tracks. Globs (`tests/test_rule*.py`) must match at least one
file. Placeholders (`NNNN-short-title.md`, `<name>`, `{id}`) and image tags
(`name:tag`) are skipped, and so are the names in NOT_IN_REPO.
"""

import fnmatch
import posixpath
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = [ROOT / "AGENTS.md", *sorted((ROOT / "docs" / "adr").glob("*.md"))]
BASES = ["", "backend", "backend/app", "frontend", "frontend/src"]
EXTENSIONS = (".py", ".ts", ".tsx", ".json", ".toml", ".yml", ".yaml", ".css", ".sh", ".md", ".txt")
TOKEN = re.compile(r"`([^`\s]+)`")
PLACEHOLDER = re.compile(r"NNN|[<>{}:]|\.\.\.")
#: Names the documents cite that are not files in this repository, with the reason.
NOT_IN_REPO = {
    "securo-plugin.json": "the manifest lives in each plugin's own repository",
}


def is_path(token: str) -> bool:
    if token.startswith(("http", "-", "/", "@")) or "(" in token or "=" in token:
        return False
    dotfile = token.startswith(".") and len(token) > 1 and token[1].isalpha()
    return "/" in token or dotfile or token.endswith(EXTENSIONS)


def tracked() -> tuple[set[str], set[str]]:
    """Files git tracks, and every directory above them: what a CI checkout holds."""
    listing = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, timeout=30)
    files = {path for path in listing.decode().split("\0") if path}
    dirs = {str(parent) for path in files for parent in PurePosixPath(path).parents if str(parent) != "."}
    return files, dirs


def exists(token: str, files: set[str], dirs: set[str]) -> bool:
    token = token.rstrip("/")
    if "/" not in token and "*" not in token:
        return any(PurePosixPath(path).name == token for path in files | dirs)
    for base in BASES:
        candidate = posixpath.normpath(posixpath.join(base, token))
        if "*" in token:
            if any(fnmatch.fnmatchcase(path, candidate) for path in files):
                return True
        elif candidate in files or candidate in dirs:
            return True
    return False


def main() -> int:
    files, dirs = tracked()
    missing = []
    for document in DOCUMENTS:
        for number, line in enumerate(document.read_text().splitlines(), start=1):
            for token in TOKEN.findall(line):
                if token in NOT_IN_REPO or not is_path(token) or PLACEHOLDER.search(token):
                    continue
                if not exists(token, files, dirs):
                    missing.append(f"{document.relative_to(ROOT)}:{number}: {token}")
    if missing:
        print("These paths are cited but don't exist; fix the path or the document:")
        print("\n".join(missing))
        return 1
    print(f"Checked the paths cited in {len(DOCUMENTS)} documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
