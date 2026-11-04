"""Select complete CI suites only when a verified PR merge proves relevance."""

import json
import os
import subprocess
from pathlib import Path

PROSE = {
    b"README.md", b"CONTRIBUTING.md",
    b"CODE_OF_CONDUCT.md", b"SECURITY.md", b"LICENSE",
}


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE, timeout=30)


def changed_jobs():
    # Every push validates the integrated codebase, including documentation pushes.
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return True, True, True

    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        pr = event["pull_request"]
        if os.environ["GITHUB_REF"] != f"refs/pull/{event['number']}/merge":
            raise ValueError("checkout is not the PR merge ref")
        parents = git("rev-list", "--parents", "-n", "1", "HEAD").decode("ascii").split()
        if parents != [os.environ["GITHUB_SHA"], pr["base"]["sha"], pr["head"]["sha"]]:
            raise ValueError("checkout does not match the event's merge and parents")

        # NULs preserve odd filenames; disabling renames retains both sides of a move.
        diff = git("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
                   "HEAD^1", "HEAD", "--")
        if not diff.endswith(b"\0"):
            raise ValueError("empty or incomplete changed-file list")
        backend = frontend = helm = False
        for path in diff[:-1].split(b"\0"):
            if path.startswith(b"backend/"):
                backend = True
            elif path.startswith(b"frontend/"):
                frontend = True
            elif path.startswith(b"charts/securo/"):
                helm = True
            elif path in PROSE or (path.startswith(b"docs/") and path.endswith(b".md")):
                continue
            else:
                # Unknown/shared paths (including workflows and this script) run everything.
                return True, True, True
        return backend, frontend, helm
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Cannot determine PR relevance ({type(error).__name__}); running all suites.")
        return True, True, True


if __name__ == "__main__":
    backend, frontend, helm = changed_jobs()
    outputs = (f"run_backend={str(backend).lower()}\n"
               f"run_frontend={str(frontend).lower()}\nrun_helm={str(helm).lower()}\n")
    # Failure to publish is a failed step, never a successful skip.
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(outputs)
    print(outputs, end="")
