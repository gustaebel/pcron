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

import os
import unittest
import collections
import threading
import signal
import locale

from libpcron import SUPPORTED_SHELLS
from libpcron.job import Job
from libpcron.time import TimeProvider

from .test_pcron import _SchedulerTest, TestScheduler

SUPPORTED_SHELLS.add("non_existing_shell")


class TestScheduler(TestScheduler):

    Job = Job


class TestJob(Job):

    @staticmethod
    def create_environ(directory, name, id, group):
        environ = Job.create_environ(".".join(locale.getlocale()), directory, name, id, group)
        environ["SHELL"] = "/bin/non_existing_shell"
        return environ

class TestScheduler2(TestScheduler):

    Job = TestJob


class _BaseTest(_SchedulerTest):

    Scheduler = TestScheduler
    TimeProvider = TimeProvider

    def _shutdown(self):
        self.scheduler.signals.append(signal.SIGTERM)

    def _test(self, duration, directory, **kwargs):
        self.timer = threading.Timer(duration * 60, self._shutdown)
        self.timer.start()

        super()._test(directory, **kwargs)

    def tearDown(self):
        self.timer.cancel()


class ShellTest(_BaseTest):

    Scheduler = TestScheduler2

    def test_shell(self):
        self._test(1, "test_shell")


class RealtimeTest(_BaseTest):

    def test_short(self):
        self._test(5, "test_realtime")

        self.assertEqual(self.counter["foo"], 1)
        self.assertEqual(self.counter["bar"], 1)
        self.assertEqual(self.counter["baz"], 1)
        self.assertEqual(self.counter["qux"], 5)

    def test_long(self):
        self._test(60, "test_realtime")

        self.assertEqual(self.counter["foo"], 1)
        self.assertEqual(self.counter["bar"], 12)
        self.assertEqual(self.counter["baz"], 1)


if __name__ == "__main__":
    unittest.main()

