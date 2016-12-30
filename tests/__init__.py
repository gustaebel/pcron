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
from libpcron.time import TimeProvider
from libpcron.parser import CrontabParser, CrontabError
from libpcron.job import Job
from libpcron.mail import Mailer


dt = datetime.datetime
td = datetime.timedelta


class TestTimeProvider(TimeProvider):
    """The TestTimeProvider class allows the testing infrastructure to run a
       pcron process in virtual time, i.e. no sleep() calls are made so all the
       operations are executed directly in sequence. By default, the
       TestTimeProvider makes sure that the pcron process runs for one
       (virtual) day.
    """

    # FIXME Test if everything works without "realistic" time.

    def __init__(self, start=None, stop=None):
        # Use the first monday in 1970 by default (but start one second before midnight
        # so that we schedule right on 00:00).
        self._now = start if start is not None else datetime.datetime(1970, 1, 4, 23, 59, 59)
        self._stop = stop if stop is not None else datetime.datetime(1970, 1, 5, 23, 59, 59)

        self.child_signals = []

    def now(self):
        # Produce a somewhat more realistic time by adding 1 microsecond
        # for each call to now().
        self._now = self._now.replace(microsecond=self._now.microsecond + 1)
        return self._now

    def next_minute(self):
        return self._now.replace(second=0, microsecond=0) + self.timedelta(seconds=60)

    def schedule_child_signal(self, ts):
        # Tell the time provider when a job is about to end, so that
        # it can simulate a SIGCHLD signal.
        self.child_signals.append(ts)
        self.child_signals.sort()

    def sleep(self, seconds):
        # We're asked to wake up at this point in time.
        wakeup = self.now() + self.timedelta(seconds=seconds)
        raise_child_signal = False

        # Check if a running job has told us earlier that it will end before
        # that time.
        if self.child_signals and wakeup > self.child_signals[0]:
            wakeup = self.child_signals.pop(0)
            raise_child_signal = True

        # Check if the end of time has come, and give the pcron process the
        # command to shut down.
        if wakeup > self._stop:
            return signal.SIGTERM

        # Advance to the new point in time.
        self._now = wakeup

        # Simulate the SIGCHLD interrupt if necessary.
        if raise_child_signal:
            return signal.SIGCHLD


class TestRunner:
    """Within a testing infrastructure simulate a running process. No actual process
       is started, only two characteristics of a process are reproduced at the
       moment: its duration and its exit code.
    """

    # FIXME Simulate output too?

    def __init__(self, working_dir, time_provider, command, environ, init_code):
        # pylint:disable=unused-argument
        self.time_provider = time_provider
        self.environ = environ

        try:
            os.makedirs(working_dir)
        except FileExistsError:
            pass

        duration, exit_code = command.split(None, 1)

        # Reduce the duration slightly to prevent the job
        # from ending exactly one the minute-border.
        interval = IntervalSpec(duration).interval
        interval -= self.time_provider.timedelta(seconds=1)

        self.duration = interval
        self.exit_code = int(exit_code)

        self.start_time = self.time_provider.now()
        self.stop_time = self.start_time + self.duration

        # Tell the time provider when this process will finish.
        self.time_provider.schedule_child_signal(self.stop_time)

        self.output = tempfile.TemporaryFile(mode="w+")
        self.output.write(self.environ["JOB_ID"])
        self.output.seek(0)

    def wait(self):
        return self.exit_code

    def terminate(self):
        self.stop_time = self.time_provider.now()
        self.duration = self.stop_time - self.start_time
        self.exit_code = -1
        self.time_provider.schedule_child_signal(self.stop_time)

    def finalize(self):
        pass

    def has_finished(self):
        return self.time_provider.now() >= self.stop_time

    def get_start_time(self):
        return self.start_time

    def get_duration(self):
        return self.duration

    def get_pid(self):
        return -1

    @property
    def returncode(self):
        return self.exit_code

    def close(self):
        self.output.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class TestMailer(Mailer):

    def send(self, sendmail, mailto, directory, environ, text, output):
        from .test_pcron import MailTest

        if output is None:
            output = ""
        else:
            output = output.read()

        message = email.message_from_string(text + "\n" + output)
        MailTest().count(message)


class TestJob(Job):

    Runner = TestRunner


class TestScheduler(Scheduler):
    """A Scheduler subclass that uses mock objects for the testing.
    """

    Job = TestJob
    Mailer = TestMailer

    def __init__(self, time_provider, directory, logfile=None, persistent_state=True):
        super().__init__(time_provider, directory, logfile, persistent_state)
        self.counter = collections.Counter()

    def init_signal_handling(self):
        pass

    def load_init_code(self):
        try:
            return self._load_init_code()
        except FileNotFoundError:
            return ""

    def load_crontab(self):
        return self._load_crontab()

    def start_job(self, job):
        super().start_job(job)
        self.counter[job.name] += 1

