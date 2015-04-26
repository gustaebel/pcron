#!/usr/bin/python3

import sys
import os
import re
import datetime
import unittest

from libpcron.time import TimeSpec, TimeSpecError, IntervalSpec, \
        IntervalSpecError, format_time
from libpcron.event import Bus, BlockManager


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
            ("* */2 * * *",      dt(2010, 3, 7, 16, 0),      True),
            ("* */2 * * *",      dt(2010, 3, 7, 15, 0),      False),
            ("0 * */2 * sun",    dt(2010, 3, 7, 0, 0),       True),
            ("0 * */2 * mon",    dt(2010, 3, 8, 0, 0),       False),
            ("0 * */7 * sun",    dt(2010, 3, 7, 0, 0),       False),
            ("0 * */7 * mon",    dt(2010, 3, 8, 0, 0),       True),
            ("0 * * mar *",      dt(2010, 3, 7, 15, 0),      True),
            ("0 * * jan-aug *",  dt(2010, 3, 7, 15, 0),      True),
            ("0 * * aug *",      dt(2010, 3, 7, 15, 0),      False),
            ("0 * * * sun",      dt(2010, 3, 7, 15, 0),      True),
            ("0 * * * sun-thu",  dt(2010, 3, 8, 15, 0),      True),
        ]
        for value, check, bool in values:
            t = TimeSpec(value)
            self.assertEqual(t.match(check), bool, "TimeSpec(%r) did not match %r" % (value, check))


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


class BusTest(unittest.TestCase):

    def _task(self):
        events = ["a", "b", "c", "stop"]
        while events:
            event = yield
            self.assertEqual(event.name, events.pop(0))

    def test_bus(self):
        bus = Bus()

        task = self._task()
        next(task)

        events = ["a", "b", "c", "stop"]

        bus.register("foo", task)
        for name in events:
            bus.post(name)

        while events:
            event = bus.get_event()
            self.assertEqual(event.name, events.pop(0))
            bus.process_event(event)


class BlockTest(unittest.TestCase):

    def test_block1(self):
        manager = BlockManager()

        self.assertRaises(AssertionError, manager.unblock, "block", "foo")

        self.assertTrue(manager.block("block", "foo"))
        self.assertRaises(AssertionError, manager.block, "block", "foo")

        self.assertFalse(manager.block("block", "bar"))
        self.assertRaises(AssertionError, manager.block, "block", "bar")

        self.assertEqual(manager.unblock("block", "foo"), "bar")
        self.assertRaises(AssertionError, manager.unblock, "block", "foo")

    def test_block2(self):
        manager = BlockManager()

        self.assertTrue(manager.block("block", "foo"))
        self.assertFalse(manager.block("block", "bar"))
        self.assertEqual(manager.unblock("block", "foo"), "bar")
        self.assertTrue(manager.unblock("block", "bar") is None)
        self.assertRaises(AssertionError, manager.unblock, "block", "bar")


if __name__ == "__main__":
    unittest.main()

