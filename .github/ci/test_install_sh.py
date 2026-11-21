"""Run with python3 .github/ci/test_install_sh.py; needs bash and git, not Docker.

The installer clones the repository, writes the environment file, pulls and
starts the containers and waits for the health endpoint. These tests drive it
with stub docker, curl and git commands, so what a user hits when they run the
installer a second time is covered here rather than by hand.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "install.sh"
COMPOSE = "docker-compose.prod.yml"

DOCKER_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SECURO_TEST_LOG/docker.log"
case "$1" in
  version) echo "Docker version 27.0.0, build test" ;;
  info) echo "Server Version: 27.0.0" ;;
esac
exit 0
"""

CURL_STUB = """#!/bin/sh
exit 0
"""

# Real git would reach for the network, so the first install is a copy of a
# fixture checkout; the tests that need a repository history use real git.
CLONE_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$SECURO_TEST_LOG/git.log"
if [ "$1" = "clone" ]; then
  cp -R "$SECURO_TEST_FIXTURE/." "$3"
fi
exit 0
"""


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.gitbin = self.root / "gitbin"
        self.log = self.root / "log"
        self.fixture = self.root / "fixture"
        self.git_home = self.root / "git-home"
        for folder in (self.bin, self.gitbin, self.log, self.fixture, self.git_home):
            folder.mkdir()
        self.stub(self.bin / "docker", DOCKER_STUB)
        self.stub(self.bin / "curl", CURL_STUB)
        self.stub(self.gitbin / "git", CLONE_STUB)
        (self.fixture / COMPOSE).write_text((REPO / COMPOSE).read_text())
        (self.fixture / "README.md").write_text("fixture checkout\n")
        self.env = {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "HOME": str(self.git_home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "SECURO_TEST_LOG": str(self.log),
            "SECURO_TEST_FIXTURE": str(self.fixture),
            "TERM": "dumb",
        }

    def stub(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def run_installer(self, cwd=None, with_clone_stub=True):
        env = dict(self.env)
        if with_clone_stub:
            env["PATH"] = f"{self.gitbin}:{env['PATH']}"
        return subprocess.run(
            ["bash", str(INSTALLER)],
            cwd=str(cwd or self.home),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def install(self):
        result = self.run_installer()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result, self.home / "securo"

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-c", "commit.gpgSign=false", "-c", f"core.hooksPath={os.devnull}", *args],
            cwd=str(cwd),
            env=dict(self.env, PATH=os.environ.get("PATH", "")),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit_all(self, cwd, message):
        self.git(cwd, "config", "user.name", "Installer Test")
        self.git(cwd, "config", "user.email", "installer@example.invalid")
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-qm", message)

    def clone_calls(self):
        calls = self.log / "git.log"
        if not calls.exists():
            return []
        return [line for line in calls.read_text().splitlines() if line.startswith("clone ")]

    def docker_calls(self):
        calls = self.log / "docker.log"
        return calls.read_text() if calls.exists() else ""

    def environment_fingerprint(self, install):
        state = (install / ".env").stat()
        return (state.st_size, state.st_mtime_ns)

    def test_first_run_installs_into_a_new_folder(self):
        result, install = self.install()

        self.assertTrue((install / COMPOSE).is_file())
        self.assertTrue((install / ".env").is_file())
        self.assertIn(f"compose -f {COMPOSE} pull", self.docker_calls())
        self.assertIn(f"compose -f {COMPOSE} up -d", self.docker_calls())
        self.assertIn("installer again", result.stdout)

    def test_second_run_reuses_the_install_in_the_same_folder(self):
        _, install = self.install()
        before = self.environment_fingerprint(install)
        clones = len(self.clone_calls())

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(clones, len(self.clone_calls()))
        self.assertEqual(before, self.environment_fingerprint(install))
        self.assertIn("Found an existing Securo installation", result.stdout)
        self.assertNotIn("fatal:", result.stdout)
        self.assertNotIn("Cloning Securo repository", result.stdout)
        self.assertIn("installer again", result.stdout)
        self.assertEqual(2, self.docker_calls().count("up -d"))

    def test_a_folder_that_is_not_a_git_checkout_is_reused_as_is(self):
        _, install = self.install()

        result = self.run_installer(with_clone_stub=False)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Not a git checkout", result.stdout)
        # The files cannot update, so the images are how this install moves
        # forward: both runs pull.
        self.assertEqual(2, self.docker_calls().count(" pull"))
        self.assertEqual(2, self.docker_calls().count("up -d"))

    def test_second_run_updates_a_git_checkout(self):
        _, install = self.install()
        origin = self.root / "origin.git"
        self.git(self.root, "init", "--bare", "-q", "-b", "main", str(origin))
        self.git(install, "init", "-q", "-b", "main")
        self.commit_all(install, "installed")
        self.git(install, "remote", "add", "origin", str(origin))
        self.git(install, "push", "-q", "-u", "origin", "main")

        publisher = self.root / "publisher"
        self.git(self.root, "clone", "-q", str(origin), str(publisher))
        (publisher / "NEWS.md").write_text("a newer version\n")
        self.commit_all(publisher, "newer version")
        self.git(publisher, "push", "-q", "origin", "main")

        result = self.run_installer(with_clone_stub=False)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((install / "NEWS.md").is_file())
        self.assertIn("Repository updated", result.stdout)
        self.assertEqual(2, self.docker_calls().count(" pull"))

    def test_an_install_inside_another_checkout_is_not_pulled(self):
        enclosing = self.home
        self.git(enclosing, "init", "-q", "-b", "main")
        (enclosing / "project.txt").write_text("another project\n")
        self.commit_all(enclosing, "another project")
        self.install()

        result = self.run_installer(with_clone_stub=False)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Not a git checkout", result.stdout)
        self.assertNotIn("Repository updated", result.stdout)
        self.assertNotIn("Could not update the repository", result.stdout)
        self.assertEqual(2, self.docker_calls().count("up -d"))

    def test_a_pull_that_fails_does_not_stop_the_install(self):
        _, install = self.install()
        self.git(install, "init", "-q", "-b", "main")
        self.commit_all(install, "installed")

        result = self.run_installer(with_clone_stub=False)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Could not update the repository", result.stdout)
        # The folder stays on the version it has, so the start reuses the
        # images already on the machine: only the first install pulled.
        self.assertEqual(1, self.docker_calls().count(" pull"))
        self.assertIn("Keeping the images already on this machine", result.stdout)
        self.assertNotIn("Pulling latest images", result.stdout)
        self.assertEqual(2, self.docker_calls().count("up -d"))

    def test_running_inside_the_install_still_works(self):
        _, install = self.install()
        before = self.environment_fingerprint(install)
        clones = len(self.clone_calls())

        result = self.run_installer(cwd=install)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(clones, len(self.clone_calls()))
        self.assertEqual(before, self.environment_fingerprint(install))
        self.assertIn(f"Found {COMPOSE} in current directory", result.stdout)

    def test_running_inside_a_checkout_updates_it(self):
        _, install = self.install()
        origin = self.root / "origin.git"
        self.git(self.root, "init", "--bare", "-q", "-b", "main", str(origin))
        self.git(install, "init", "-q", "-b", "main")
        self.commit_all(install, "installed")
        self.git(install, "remote", "add", "origin", str(origin))
        self.git(install, "push", "-q", "-u", "origin", "main")

        publisher = self.root / "publisher"
        self.git(self.root, "clone", "-q", str(origin), str(publisher))
        (publisher / "NEWS.md").write_text("a newer version\n")
        self.commit_all(publisher, "newer version")
        self.git(publisher, "push", "-q", "origin", "main")

        before = self.environment_fingerprint(install)
        result = self.run_installer(cwd=install, with_clone_stub=False)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((install / "NEWS.md").is_file())
        self.assertIn("Repository updated", result.stdout)
        self.assertEqual(before, self.environment_fingerprint(install))

    def test_a_file_named_securo_gets_a_clear_error(self):
        (self.home / "securo").write_text("not a folder\n")

        result = self.run_installer()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("is not a directory", result.stdout)
        self.assertNotIn("fatal:", result.stdout)
        self.assertEqual([], self.clone_calls())

    def test_a_foreign_folder_named_securo_gets_a_clear_error(self):
        foreign = self.home / "securo"
        foreign.mkdir()
        (foreign / "notes.txt").write_text("someone else's folder\n")

        result = self.run_installer()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not look like a Securo installation", result.stdout)
        self.assertNotIn("fatal:", result.stdout)
        self.assertEqual([], self.clone_calls())


if __name__ == "__main__":
    unittest.main()
