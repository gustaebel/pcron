# -----------------------------------------------------------------------
#
# pcron - a periodic cron-like job scheduler.
# Copyright (C) 2009-2015 Lars Gustäbel <lars@gustaebel.de>
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
import pwd
import signal
import collections

from .time import format_time
from .run import Runner, RunnerError
from .shared import CrontabError, create_environ
from .field import String, Boolean, Time, Interval, ListOfStrings


class Job:
    """A class that contains a single job from a crontab.ini file.
    """
    # pylint:disable=no-member

    Runner = Runner

    _name_regex = r"^\w+(-\w+|\.\w+)*$"

    fields = collections.OrderedDict([
        ("name",        String(regex=_name_regex)),
        ("command",     String()),
        ("active",      Boolean(default=True)),

        ("condition",   String(default=None)),
        ("group",       String(default=lambda j: j.name, regex=_name_regex)),
        ("conflict",    String(default="ignore", choices=("ignore", "skip", "mail", "kill"))),

        ("time",        Time(default=None, schedule=True)),
        ("interval",    Interval(default=None, schedule=True)),
        ("post",        ListOfStrings(default=[], schedule=True)),

        ("mail",        String(default="error", choices=("never", "always", "error", "output"))),
        ("mailto",      String(default=pwd.getpwuid(os.getuid()).pw_name)),
        ("sendmail",    String(default="/usr/lib/sendmail"))
    ])

    _serial = collections.Counter()

    #
    # === Base methods
    #
    @classmethod
    def new(cls, name, info):
        job = type(name, (cls,), {})

        info = info.copy()
        has_schedule = False
        for name, field in job.fields.items():
            try:
                value = info.pop(name)
            except KeyError:
                setattr(job, name, field.get_default(job))
            else:
                setattr(job, name, field(value))
                if field.schedule:
                    has_schedule = True

        for key in info:
            raise CrontabError("variable %r not allowed" % key)

        if not has_schedule:
            raise CrontabError("missing scheduling information")

        job.last_run = None
        return job

    def __init__(self, trigger):
        self.trigger = trigger
        self.id = "%s-%04d" % (self.name, self._serial[self.name])
        self._serial[self.name] += 1

        self.log = self.logger.new(self.id)

        # FIXME rename this_run?
        self.this_run = self.time_provider.now()
        self.__class__.last_run = self.this_run
        self.runner = None

        self.environ = self.create_environ(self.directory, self.name,
                                           self.id, self.group)

        self.working_dir = os.path.join(self.directory, "jobs", self.name)
        self.username = self.environ["USER"]

    @staticmethod
    def create_environ(directory, name, id, group):
        # Prepare a basic environment for the job.
        record = pwd.getpwuid(os.getuid())
        return create_environ(record, PCRONDIR=directory, JOB_NAME=name, JOB_ID=id, JOB_GROUP=group)

    def __str__(self):
        return self.id

    def __repr__(self):
        return "<job-%s %s>" % (self.id, self.next_run)

    #
    # === Scheduling
    #
    @classmethod
    def init(cls, time_provider, logger, directory, init_code):
        cls.init_code = init_code
        cls.time_provider = time_provider
        cls.logger = logger
        cls.directory = directory

        if cls.time != "@reboot":
            if cls.last_run is None:
                now = cls.time_provider.next_minute()
            else:
                now = cls.last_run
            cls._timestamp_generator = cls.timestamp_generator(now)
            cls.advance()

    @classmethod
    def advance(cls):
        cls.next_trigger, cls.next_run = next(cls._timestamp_generator)
        log = cls.logger.new(cls.name)
        log.debug("advance: %s %s", cls.next_trigger, format_time(cls.next_run))

    @classmethod
    def timestamp_generator(cls, now):
        infinity = cls.time_provider.infinity

        if cls.time is not None:
            time_generator = cls.time.timestamp_generator(now)
        else:
            time_generator = None

        if cls.interval is not None:
            interval_generator = cls.interval.timestamp_generator(now)
        else:
            interval_generator = None

        time = infinity
        interval = infinity
        while True:
            if time is infinity and time_generator is not None:
                time = next(time_generator)
            if interval is infinity and interval_generator is not None:
                interval = next(interval_generator)

            if time <= interval:
                yield "time", time
                time = infinity
            else:
                yield "interval", interval
                interval = infinity

    #
    # === Process
    #
    def has_finished(self):
        return self.runner is None or self.runner.has_finished()

    def start(self):
        """Start the job process.
        """
        self.log.debug("start (%s)", self.trigger)
        if self.condition is None or self.test_condition():
            # Start the process.
            self.log.info("execute: %s", self.command)
            try:
                self.runner = self.Runner(self.working_dir, self.time_provider,
                                          self.command, self.environ, self.init_code)
            except (OSError, RunnerError) as exc:
                self.log.warn(str(exc))
                return False
            else:
                return True

    def terminate(self):
        """Terminate the running job process ahead of time.
        """
        if self.runner is None:
            return

        try:
            self.runner.terminate()
        except (OSError, RunnerError) as exc:
            self.log.warn(str(exc))

    def finalize(self):
        if self.runner is None:
            return

        assert self.runner.has_finished()

        self.runner.finalize()
        self.log.debug("duration: %s", format_time(self.runner.get_duration()))
        if self.next_run < self.time_provider.infinity:
            self.log.info("next run: %s", self.next_run)

    def close(self):
        if self.runner is None:
            return

        self.runner.close()

    def test_condition(self):
        # FIXME Do this asynchronously.
        try:
            with Runner(self.working_dir, self.time_provider, self.condition,
                        self.environ, self.init_code) as runner:
                if runner.wait() == 0:
                    self.log.debug("test %r: true", self.condition)
                    return True
        except (OSError, RunnerError) as exc:
            self.log.warn(str(exc))

        self.log.debug("test %r: false", self.condition)
        return False

