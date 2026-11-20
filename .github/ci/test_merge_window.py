"""Run with python3 .github/ci/test_merge_window.py; no dependencies.

The merge window is a module so it can be pinned off the clock: these cases are
the rule itself, Friday 18:00 and Monday 01:00 in America/Sao_Paulo, on both
sides of each boundary, through the UTC clock the workflow actually has, and
across a date the zone changed its offset.
"""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "merge_window", Path(__file__).with_name("merge_window.py")
)
merge_window = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_window)


class MergeWindowTest(unittest.TestCase):
    def opened(self, text):
        return merge_window.is_open(datetime.fromisoformat(text))

    def test_friday_before_1800_is_open(self):
        self.assertTrue(self.opened("2026-11-20T17:59:00-03:00"))

    def test_friday_1800_is_closed(self):
        self.assertFalse(self.opened("2026-11-20T18:00:00-03:00"))

    def test_friday_late_is_closed(self):
        self.assertFalse(self.opened("2026-11-20T23:59:00-03:00"))

    def test_saturday_is_closed(self):
        self.assertFalse(self.opened("2026-11-21T00:00:00-03:00"))
        self.assertFalse(self.opened("2026-11-21T12:00:00-03:00"))

    def test_sunday_is_closed(self):
        self.assertFalse(self.opened("2026-11-22T12:00:00-03:00"))
        self.assertFalse(self.opened("2026-11-22T23:59:00-03:00"))

    def test_monday_before_0100_is_closed(self):
        self.assertFalse(self.opened("2026-11-23T00:00:00-03:00"))
        self.assertFalse(self.opened("2026-11-23T00:59:00-03:00"))

    def test_monday_0100_is_open(self):
        self.assertTrue(self.opened("2026-11-23T01:00:00-03:00"))

    def test_the_rest_of_the_week_is_open(self):
        self.assertTrue(self.opened("2026-11-19T12:00:00-03:00"))  # Thursday
        self.assertTrue(self.opened("2026-11-23T09:00:00-03:00"))  # Monday later
        self.assertTrue(self.opened("2026-11-24T00:00:00-03:00"))  # Tuesday

    def test_the_utc_clock_gets_the_same_answer(self):
        # The workflow's cron is UTC and nothing else is: 21:00Z is 18:00 local
        # on a Friday, and 04:00Z on Monday is 01:00 local.
        self.assertTrue(self.opened("2026-11-20T20:59:00+00:00"))
        self.assertFalse(self.opened("2026-11-20T21:00:00+00:00"))
        self.assertFalse(self.opened("2026-11-23T03:59:00+00:00"))
        self.assertTrue(self.opened("2026-11-23T04:00:00+00:00"))

    def test_a_zone_that_changed_its_offset_still_reads_the_wall_clock(self):
        # Brazil still had daylight saving in 2018: on 5 Nov 2018 the zone was
        # -02:00, so 03:00Z is 01:00 Monday, the moment the window opens. Read
        # with a fixed -03:00 it would be 00:00, still closed. Today the zone
        # is -03:00 all year and this is what keeps a future change correct.
        self.assertTrue(self.opened("2018-11-05T03:00:00+00:00"))
        self.assertFalse(self.opened("2018-11-05T02:59:00+00:00"))

    def test_a_naive_datetime_is_refused(self):
        with self.assertRaises(ValueError):
            merge_window.is_open(datetime(2026, 11, 20, 17, 59))

    def test_the_command_writes_the_answer_and_says_it(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "github-output"
            log = io.StringIO()
            with redirect_stdout(log):
                code = merge_window.main(
                    ["--at", "2026-11-21T12:00:00-03:00", "--github-output", str(output)]
                )
            self.assertEqual(code, 0)
            self.assertEqual(output.read_text().strip(), "open=false")
            self.assertIn("outside the merge window", log.getvalue())

    def test_the_command_says_when_it_is_open(self):
        log = io.StringIO()
        with redirect_stdout(log):
            code = merge_window.main(["--at", "2026-11-23T01:00:00-03:00"])
        self.assertEqual(code, 0)
        self.assertIn("inside the merge window", log.getvalue())


if __name__ == "__main__":
    unittest.main()
