"""Run with python3 .github/ci/test_adr_index.py; no application dependencies needed."""

import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "gen_adr_index.py"

spec = importlib.util.spec_from_file_location("gen_adr_index", GENERATOR)
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

FIRST = "# 0001. First decision\n\n- Status: Accepted\n"
REPLACED = "# 0002. Replaced decision\n\n- Status: Superseded by [0003](0003-third.md)\n"
THIRD = "# 0003. Third decision\n\n- Status: Accepted\n"
TEMPLATE = "# NNNN. Title in a few words, stated as the decision\n\n- Status: Proposed | Accepted\n"

ROW_FIRST = "| [0001](0001-first.md) | First decision |"
ROW_REPLACED = "| [0002](0002-replaced.md) | Replaced decision (superseded by 0003) |"
ROW_THIRD = "| [0003](0003-third.md) | Third decision |"

PROSE = "# Architecture decision records\n\nOne decision per file.\n\n"
BEFORE = "## Index\n\n| ADR | Decision |\n|-----|----------|\n"
AFTER = "\nRead the ones that touch your area.\n"


def readme(rows):
    return PROSE + BEFORE + "\n".join(rows) + "\n" + AFTER


class AdrIndexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.adr = self.root / "docs" / "adr"
        self.adr.mkdir(parents=True)
        for name, text in (
            ("0000-template.md", TEMPLATE),
            ("0001-first.md", FIRST),
            ("0002-replaced.md", REPLACED),
            ("0003-third.md", THIRD),
        ):
            self.adr.joinpath(name).write_text(text)
        self.adr.joinpath("README.md").write_text(readme([ROW_FIRST, ROW_REPLACED, ROW_THIRD]))
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q", "-b", "main")

    def adr_path(self):
        return self.adr / "README.md"

    def check(self, base=None):
        """Run the check with its report swallowed; the boolean is what is tested."""
        return self.quietly(gen.check, self.root, base)

    def quietly(self, function, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return function(*args)

    def git(self, *args):
        return subprocess.check_output(
            [
                "git",
                "-c", "commit.gpgSign=false",
                "-c", f"core.hooksPath={os.devnull}",
                "-c", "user.name=CI Test",
                "-c", "user.email=ci@example.invalid",
                *args,
            ],
            cwd=self.root,
            env=self.env,
            stderr=subprocess.PIPE,
        ).decode().strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_a_matching_table_passes(self):
        self.assertTrue(self.check())

    def test_the_table_is_rebuilt_from_the_files(self):
        self.adr_path().write_text(readme([ROW_FIRST, "| [0002](0002-replaced.md) | Old wording |"]))
        self.assertFalse(self.check())

        self.assertTrue(self.quietly(gen.regenerate, self.root))
        self.assertEqual(
            self.adr_path().read_text(),
            readme([ROW_FIRST, ROW_REPLACED, ROW_THIRD]),
        )
        self.assertTrue(self.check())

    def test_an_adr_without_a_row_fails(self):
        self.adr_path().write_text(readme([ROW_FIRST, ROW_REPLACED]))
        self.assertFalse(self.check())

    def test_a_row_without_an_adr_fails(self):
        gone = "| [0004](0004-gone.md) | Gone |"
        self.adr_path().write_text(readme([ROW_FIRST, ROW_REPLACED, ROW_THIRD, gone]))
        self.assertFalse(self.check())

    def test_a_heading_that_names_another_adr_is_reported(self):
        self.adr.joinpath("0004-wrong.md").write_text("# 0002. Wrong number\n\n- Status: Accepted\n")
        with self.assertRaises(SystemExit) as raised:
            self.check()
        self.assertIn("0004-wrong.md", str(raised.exception))

    def test_the_template_is_not_indexed(self):
        self.assertIn(ROW_THIRD, gen.expected_rows(self.root))
        self.assertFalse(any("0000-template" in row for row in gen.expected_rows(self.root)))

    def test_a_pull_request_may_add_an_adr_without_its_row(self):
        base = self.commit("base")
        self.adr.joinpath("0004-new.md").write_text("# 0004. New decision\n\n- Status: Accepted\n")

        # On main the missing row is a failure; against the base branch it is
        # not, because the regeneration on main writes it after the merge.
        self.assertFalse(self.check())
        with mock.patch.dict(os.environ, self.env):
            self.assertTrue(self.check(base))

            # The row the pull request does add must say what the file says.
            self.adr_path().write_text(
                readme([ROW_FIRST, ROW_REPLACED, ROW_THIRD, "| [0004](0004-new.md) | Something else |"])
            )
            self.assertFalse(self.check(base))

            self.adr_path().write_text(readme([ROW_FIRST, ROW_REPLACED, ROW_THIRD]))
            self.assertTrue(self.check(base))

    def test_a_pull_request_may_fix_a_heading_number(self):
        self.adr.joinpath("0004-wrong.md").write_text("# 0002. A decision under the wrong number\n\n- Status: Accepted\n")
        self.adr_path().write_text(
            readme([ROW_FIRST, ROW_REPLACED, ROW_THIRD, "| [0004](0004-wrong.md) | A decision under the wrong number |"])
        )
        base = self.commit("base")

        # The fix lands in the pull request; the check reads the base's copy of
        # the file, which still carries the wrong number, and must not trip on it.
        self.adr.joinpath("0004-wrong.md").write_text("# 0004. A decision under the wrong number\n\n- Status: Accepted\n")
        with mock.patch.dict(os.environ, self.env):
            self.assertTrue(self.check(base))
        self.assertTrue(self.check())

    def test_a_base_row_the_table_lost_fails(self):
        base = self.commit("base")
        self.adr_path().write_text(readme([ROW_FIRST, ROW_THIRD]))
        with mock.patch.dict(os.environ, self.env):
            self.assertFalse(self.check(base))

    def test_a_base_row_the_table_rewrote_fails(self):
        base = self.commit("base")
        self.adr_path().write_text(
            readme([ROW_FIRST, "| [0002](0002-replaced.md) | Old wording |", ROW_THIRD])
        )
        with mock.patch.dict(os.environ, self.env):
            self.assertFalse(self.check(base))


if __name__ == "__main__":
    unittest.main()
