#!/usr/bin/python3
# -----------------------------------------------------------------------
#
# pcron - a periodic cron-like job scheduler.
# Copyright (C) 2009-2016 Lars Gustäbel <lars@gustaebel.de>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
#
# -----------------------------------------------------------------------
#
# TODO Test time generator.
# TODO Test mixed time/interval/post jobs.

import sys
import os
import re
import datetime
import unittest
import tempfile
import email
import signal
import collections

from libpcron.time import TimeSpec, TimeSpecError, IntervalSpec, \
        IntervalSpecError, format_time
from libpcron.scheduler import Scheduler
from libpcron.parser import CrontabParser, CrontabError
from libpcron.job import Job
from libpcron.mail import Mailer

from . import TestScheduler, TestJob, TestRunner, TestTimeProvider

data_directory = os.path.join(os.path.dirname(__file__), "data")


dt = datetime.datetime
td = datetime.timedelta


class TimeSpecTest(unittest.TestCase):

    def _test_valid_values(self, func, min, max, more_values=None):
        valid_values = {
            str(min):   (min,),
            str(max-1): (max-1,),
            "1,2,3,4":  (1, 2, 3, 4),
            "1-1":      (1,),
            "1-6":      tuple(range(1, 7)),
            "1-7,2-4":  (1, 2, 3, 4, 5, 6, 7),
            "1-3,6-7":  (1, 2, 3, 6, 7),
            "1-7/3":    (1, 4, 7),
            "*":        tuple(range(min, max)),
            "*/2":      tuple(range(min, max, 2)),
            "1-2~2":    (1,),
            "1-2~2,2-3~2~3": (1,),
            "1-4~2~3":  (1, 4),
        }
        if more_values:
            valid_values.update(more_values)

        for value, check in valid_values.items():
            try:
                result = func(value)
            except TimeSpecError as e:
                self.fail(str(e))
            result = tuple(result)
            self.assertEqual(result, check, "parsing %r failed (%r != %r)" % (value, result, check))

    def _test_invalid_values(self, func, min, max, more_values=None):
        invalid_values = [
            str(min-1),
            "-1",
            str(max),
            str(min) + "-" + str(max),
            "5-",
            "1,2,100,3,4",
            "2-1",
            "*/" + str(max),
            "*/-2",
            "1~1",
            "1-2~3",
        ]
        if more_values:
            invalid_values += more_values

        for value in invalid_values:
            try:
                func(value)
            except TimeSpecError:
                continue
            else:
                self.fail("TimeSpecError not raised for %r" % value)

    def test_valid_minutes(self):
        self._test_valid_values(TimeSpec.parse_minute, 0, 60)

    def test_invalid_minutes(self):
        self._test_invalid_values(TimeSpec.parse_minute, 0, 60)

    def test_valid_hours(self):
        self._test_valid_values(TimeSpec.parse_hour, 0, 24)

    def test_invalid_hours(self):
        self._test_invalid_values(TimeSpec.parse_hour, 0, 24)

    def test_valid_days_of_month(self):
        self._test_valid_values(TimeSpec.parse_day_of_month, 1, 32)

    def test_invalid_days_of_month(self):
        self._test_invalid_values(TimeSpec.parse_day_of_month, 1, 32)

    def test_valid_months(self):
        more_values = {
            "jan":      (1,),
            "jan-aug":  (1, 2, 3, 4, 5, 6, 7, 8),
        }
        self._test_valid_values(TimeSpec.parse_month, 1, 13, more_values)

    def test_invalid_months(self):
        more_values = ["jon", "january", "jan-"]
        self._test_invalid_values(TimeSpec.parse_month, 1, 13, more_values)

    def test_valid_days_of_week(self):
        more_values = {
            "mon":      (1,),
            "sun-thu":  (0, 1, 2, 3, 4),
        }
        self._test_valid_values(TimeSpec.parse_day_of_week, 0, 8, more_values)

    def test_invalid_days_of_week(self):
        more_values = ["mun", "monday", "sun-"]
        self._test_invalid_values(TimeSpec.parse_day_of_week, 0, 8, more_values)

    def test_timespec(self):
        values = [
            ("* */2 * * *",     dt(2010, 3, 7, 16, 0),  True),
            ("* */2 * * *",     dt(2010, 3, 7, 15, 0),  False),

            ("0 * */2 * sun",   dt(2010, 3, 7, 0, 0),   True),
            ("0 * */7 * sun",   dt(2010, 3, 7, 0, 0),   True),
            ("0 * */7 * *",     dt(2010, 3, 7, 0, 0),   False),

            ("0 * */7 * mon",   dt(2010, 3, 8, 0, 0),   True),
            ("0 * */2 * mon",   dt(2010, 3, 8, 0, 0),   True),
            ("0 * */2 * *",     dt(2010, 3, 8, 0, 0),   False),

            ("0 * * mar *",     dt(2010, 3, 7, 15, 0),  True),
            ("0 * * jan-aug *", dt(2010, 3, 7, 15, 0),  True),
            ("0 * * aug *",     dt(2010, 3, 7, 15, 0),  False),
            ("0 * * * sun",     dt(2010, 3, 7, 15, 0),  True),
            ("0 * * * sun-thu", dt(2010, 3, 8, 15, 0),  True),
        ]
        for value, ts,  success in values:
            t = TimeSpec(value)
            self.assertEqual(t.match(ts), success, "TimeSpec(%r) did not match %r" % (value, ts))


class IntervalSpecTest(unittest.TestCase):

    valid_values = {
        "1m":       td(weeks=4),
        "3m":       td(weeks=12),
        "1w":       td(weeks=1),
        "23w":      td(weeks=23),
        "123w":     td(weeks=123),
        "1d":       td(days=1),
        "7d":       td(days=7),
        "7d":       td(weeks=1),
        "1h":       td(hours=1),
        "144h":     td(hours=144),
        "23":       td(minutes=23),
        "1":        td(minutes=1),

        "1m1w1d1h1":    td(weeks=5, days=1, hours=1, minutes=1),
        "2m1d1h":       td(weeks=8, days=1, hours=1),
        "21d23":        td(weeks=3, minutes=23),
    }

    def test_valid_intervals(self):
        for value, check in self.valid_values.items():
            try:
                interval = IntervalSpec(value).get_timedelta()
            except IntervalSpecError as e:
                self.fail(str(e))
            self.assertEqual(interval, check, "parsing %r failed (%r != %r)" % (value, interval, check))


class FormatTest(unittest.TestCase):

    def test_format_time(self):
        self.assertEqual(format_time(td(weeks=1)), "7d")
        self.assertEqual(format_time(td(weeks=1, seconds=1)), "7d1s")
        self.assertEqual(format_time(td(weeks=1, days=2, hours=3, minutes=4, seconds=5)), "9d3h4m5s")


class CrontabTest(unittest.TestCase):

    def _test(self, name):
        directory = os.path.join(data_directory, "crontabs")
        parser = CrontabParser(os.path.join(directory, name), TestJob)
        return parser.parse()

    def test_crontab(self):
        self.assertRaises(CrontabError, self._test, "crontab1.ini")
        self.assertRaises(CrontabError, self._test, "crontab2.ini")
        self.assertRaises(CrontabError, self._test, "crontab3.ini")
        self.assertRaises(CrontabError, self._test, "crontab4.ini")

    def test_inheritance(self):
        startup, jobs = self._test("crontab5.ini")

        self.assertEqual(jobs["foo"].command, "foo")
        self.assertEqual(jobs["foo"].command, jobs["foo.bar"].command)
        self.assertEqual(jobs["foo"].time, jobs["foo.bar"].time)
        self.assertEqual(jobs["foo.baz"].command, "baz")
        self.assertEqual(jobs["foo"].time, jobs["foo.baz"].time)


class _SchedulerTest(unittest.TestCase):

    Scheduler = TestScheduler
    TimeProvider = TestTimeProvider

    @property
    def counter(self):
        return self.scheduler.counter

    def log_event(self, event):
        print(self.time_provider.now().strftime("%Y-%m-%d %H:%M:%S"), event, file=self.logfile, flush=True)

    def _test(self, directory, **kwargs):
        self.time_provider = self.TimeProvider(**kwargs)

        with open(os.path.join(data_directory, directory, "logfile.txt"), "w") as self.logfile:
            with self.Scheduler(self.time_provider,
                               os.path.abspath(os.path.join(data_directory, directory)),
                               self.logfile, persistent_state=False) as self.scheduler:
                self.scheduler.mainloop()


class SchedulerTest(_SchedulerTest):

    def test_day(self):
        # NOTE The default starting point is Jan 5, 1970 which is a monday.
        self._test("test_day")

        self.assertEqual(self.counter["foo"], 96) # (24*60 minutes / 15 minutes)
        self.assertEqual(self.counter["bar"], 96)
        self.assertEqual(self.counter["baz"], 24)
        self.assertEqual(self.counter["qux"], 6)
        self.assertEqual(self.counter["quux"], 1440)
        self.assertEqual(self.counter["corge"], 720)
        self.assertEqual(self.counter["grault"], 1)
        self.assertEqual(self.counter["garply"], 0)
        self.assertEqual(self.counter["waldo"], 1)
        self.assertEqual(self.counter["fred"], 0)
        self.assertEqual(self.counter["plugh"], 1)
        self.assertEqual(self.counter["xyzzy"], 0)
        self.assertEqual(self.counter["thud"], 96)

    def test_week(self):
        self._test("test_day", stop=dt(1970, 1, 11, 23, 59, 59))

        self.assertEqual(self.counter["foo"], 96 * 7)
        self.assertEqual(self.counter["bar"], 96 * 7)
        self.assertEqual(self.counter["baz"], 24 * 7)
        self.assertEqual(self.counter["qux"], 6 * 7)
        self.assertEqual(self.counter["quux"], 1440 * 7)
        self.assertEqual(self.counter["corge"], 720 * 7)
        self.assertEqual(self.counter["grault"], 1)
        self.assertEqual(self.counter["garply"], 1)
        self.assertEqual(self.counter["waldo"], 7)
        self.assertEqual(self.counter["fred"], 0)
        self.assertEqual(self.counter["plugh"], 1)
        self.assertEqual(self.counter["xyzzy"], 1)
        self.assertEqual(self.counter["thud"], 96 * 7)
        self.assertEqual(self.counter["foobar"], 1)

    def test_week2(self):
        self._test("test_day", start=dt(1970, 1, 5, 8, 0, 0), stop=dt(1970, 1, 12, 23, 59, 59))
        self.assertEqual(self.counter["foobar"], 1)

    def test_month(self):
        self._test("test_month", start=dt(1969, 12, 31, 23, 59, 59), stop=dt(1970, 1, 31, 23, 59, 59))

        self.assertEqual(self.counter["foo"], 1)
        self.assertEqual(self.counter["bar"], 0)
        self.assertEqual(self.counter["baz"], 4)
        self.assertEqual(self.counter["qux"], 4)
        self.assertEqual(self.counter["quux"], 5)
        self.assertEqual(self.counter["corge"], 31)
        self.assertEqual(self.counter["grault"], 31 * 24)
        self.assertEqual(self.counter["garply"], 6)
        self.assertEqual(self.counter["waldo"], 0)

    def test_year(self):
        # This is a period of 52 weeks.
        self._test("test_year", stop=dt(1971, 1, 3, 23, 59, 59))

        self.assertEqual(self.counter["foo"], 1)
        self.assertEqual(self.counter["bar"], 6)
        self.assertEqual(self.counter["baz"], 0)
        self.assertEqual(self.counter["qux"], 52)
        self.assertEqual(self.counter["quux"], 52)
        self.assertEqual(self.counter["corge"], 1)

    def test_decade(self):
        # This is actually less than a decade, it's 10 * 52 weeks.
        self._test("test_year", stop=dt(1979, 12, 23, 23, 59, 59))

        self.assertEqual(self.counter["foo"], 9)
        self.assertEqual(self.counter["bar"], 60)
        self.assertEqual(self.counter["baz"], 2) # count leap years
        self.assertEqual(self.counter["qux"], 520)
        self.assertEqual(self.counter["quux"], 520)
        self.assertEqual(self.counter["corge"], 1)

    def test_special(self):
        self._test("test_special")

        self.assertEqual(self.counter["foo"], 48)
        self.assertEqual(self.counter["bar"], 48)
        self.assertEqual(self.counter["baz"], 96)
        self.assertEqual(self.counter["qux"], 48)
        self.assertEqual(self.counter["quux"], 24)
        self.assertEqual(self.counter["grault"], 3)
        self.assertEqual(self.counter["garply"], 3)
        self.assertEqual(self.counter["corge"], 12)
        self.assertEqual(self.counter["fred"], 0)
        self.assertEqual(self.counter["waldo"], 1)
        self.assertEqual(self.counter["plugh"], 72)
        self.assertEqual(self.counter["xyzzy"], 24)
        self.assertEqual(self.counter["thud"], 96)


class MailTest(_SchedulerTest):

    def __new__(cls, *args):
        # Produce the same instance for every call. We use this in TestMailer,
        # to call e.g. the count_killed() method.
        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls)
        return cls._instance

    def setUp(self):
        self.mail = {}

    def test_mail(self):
        self._test("test_mail", stop=dt(1970, 1, 5, 1, 0, 0))

        self.assertEqual(self.counter["foo.1"], 4)
        self.assertEqual(self.mail["CONFLICT KILL"]["foo.1"], 0)
        self.assertEqual(self.counter["foo.2"], 4)
        self.assertEqual(self.mail["CONFLICT KILL"]["foo.2"], 0)

        self.assertEqual(self.counter["bar.1"], 2)
        self.assertEqual(self.mail["CONFLICT KILL"]["bar.1"], 0)
        self.assertEqual(self.counter["bar.2"], 2)
        self.assertEqual(self.mail["CONFLICT KILL"]["bar.2"], 0)
        self.assertEqual(self.counter["bar.3"], 2)
        self.assertEqual(self.mail["CONFLICT KILL"]["bar.3"], 1)
        self.assertEqual(self.mail["KILLED"]["bar.3"], 2)

        self.assertEqual(self.mail["CONFLICT SKIP"]["baz.1"], 3)
        self.assertEqual(self.mail["CONFLICT SKIP"]["baz.2"], 3)
        self.assertEqual(self.mail["CONFLICT SKIP"]["baz.3"], 0)

        self.assertEqual(self.mail["INFO"]["qux.1"], 1)
        self.assertEqual(self.mail["INFO"]["qux.2"], 0)
        self.assertEqual(self.mail["INFO"]["qux.3"], 0)
        self.assertEqual(self.mail["ERROR"]["qux.1"], 0)
        self.assertEqual(self.mail["ERROR"]["qux.2"], 0)
        self.assertEqual(self.mail["ERROR"]["qux.3"], 0)

        self.assertEqual(self.mail["INFO"]["quux.1"], 0)
        self.assertEqual(self.mail["INFO"]["quux.2"], 0)
        self.assertEqual(self.mail["INFO"]["quux.3"], 0)
        self.assertEqual(self.mail["ERROR"]["quux.1"], 1)
        self.assertEqual(self.mail["ERROR"]["quux.2"], 1)
        self.assertEqual(self.mail["ERROR"]["quux.3"], 0)

    def count(self, m):
        _, job_id = m["subject"].rsplit(None, 1)
        job_name, _ = job_id.split("-", 1)

        self.mail.setdefault(m["pcron-status"], collections.Counter())[job_name] += 1


class PersistenceTest(_SchedulerTest):

    TimeProvider = TestTimeProvider

    def _test(self, directory, **kwargs):
        self.time_provider = self.TimeProvider(**kwargs)

        with open(os.path.join(data_directory, directory, "logfile.txt"), "a") as self.logfile:
            with self.Scheduler(self.time_provider,
                               os.path.abspath(os.path.join(data_directory, directory)),
                               self.logfile, persistent_state=True) as self.scheduler:
                self.scheduler.mainloop()

    def test_persistence(self):
        for name in ("state.db", "logfile.txt"):
            try:
                os.remove(os.path.join(data_directory, "test_persistence", name))
            except FileNotFoundError:
                pass

        # Job runs on 0:00 on 5,12,19 etc.
        start1 = datetime.datetime(1970, 1, 4, 11, 59, 59)
        start2 = datetime.datetime(1970, 1, 11, 11, 59, 59)
        start3 = datetime.datetime(1970, 1, 18, 11, 59, 59)
        start4 = datetime.datetime(1970, 1, 18, 11, 59, 59) + td(days=28)

        self._test("test_persistence", start=start1, stop=start2)
        self.assertEqual(self.counter["foo"], 1)
        self._test("test_persistence", start=start2, stop=start3)
        self.assertEqual(self.counter["foo"], 1)
        self._test("test_persistence", start=start3, stop=start4)
        self.assertEqual(self.counter["foo"], 4)


if __name__ == "__main__":
    unittest.main()

