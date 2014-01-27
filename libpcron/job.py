# coding: utf8
# -----------------------------------------------------------------------
#
# pcron - a periodic cron-like job scheduler.
# Copyright (C) 2009-2014 Lars Gustäbel <lars@gustaebel.de>
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

import io
import datetime
import subprocess
import logging
import signal

from libpcron.shared import RUNNING, WAITING, SLEEPING
from libpcron.time import format_time
from libpcron.run import Runner, RunnerError
from libpcron.shared import CrontabError

SIGNAL_NAMES = dict((getattr(signal, name), name) \
    for name in dir(signal) if name.startswith("SIG") and "_" not in name)


MAIL_INFO = """\
From: pcron <%(username)s>
To: %(mailto)s
Subject: pcron: %(schedule)s %(job)s

"""

MAIL_ERROR = """\
From: pcron <%(username)s>
To: %(mailto)s
Subject: pcron: ERROR! %(schedule)s %(job)s

Job %(job)s exited with error code %(exitcode)s.

"""

MAIL_KILLED = """\
From: pcron <%(username)s>
To: %(mailto)s
Subject: pcron: KILLED! %(schedule)s %(job)s

Job %(job)s was killed by signal %(signal)s.

"""

MAIL_SKIP_RUNNING = """\
From: pcron <%(username)s>
To: %(mailto)s
Subject: pcron: WARNING! %(schedule)s %(job)s

The scheduled run for job %(job)s was skipped because another instance
of the job is still running.

    %(command)s

The process is running with pid %(pid)s.
"""

MAIL_SKIP_WAITING = """\
From: pcron <%(username)s>
To: %(mailto)s
Subject: pcron: WARNING! %(schedule)s %(job)s

The scheduled run for job %(job)s was skipped because another instance
of the job is already waiting to start.
"""


class Job(object):

    last_run = datetime.datetime.min
    scheduled_run = datetime.datetime.min
    next_run = datetime.datetime.max

    duration = datetime.timedelta(seconds=0)

    mandatory_keys = ("id", "command")
    scheduling_keys = ("time", "interval", "post")
    allowed_keys = ("block", "condition", "mail", "mailto", "sendmail", "conflict")

    #
    # === Base methods
    #
    def __init__(self, bus, username, environ, environment):
        self.bus = bus
        self.username = username
        self.environ = environ
        self.environment = environment

        self.runner = None
        self.task = None
        self.state = SLEEPING

    def __str__(self):
        return self.id

    def __repr__(self):
        return "<job-%s %s>" % (self.id, self.next_run)

    #
    # === Job definition
    #
    @classmethod
    def check(cls, definition):
        for key in cls.mandatory_keys:
            if key not in definition:
                raise CrontabError("%s: mandatory variable %r not found" % (definition["id"], key))

        if not set(definition.keys()) & set(cls.scheduling_keys):
            raise CrontabError("%s: missing scheduling information" % definition["id"])

        for key in definition:
            if key not in cls.mandatory_keys + cls.scheduling_keys + cls.allowed_keys:
                raise CrontabError("%s: variable %r not allowed" % (definition["id"], key))

    def update(self, definition):
        # pylint:disable=attribute-defined-outside-init
        self.id = definition["id"]

        self.command = definition["command"]
        self.time = definition.get("time", None)
        self.interval = definition.get("interval", None)
        self.post = definition.get("post", set())

        self.block = definition.get("block", self.id)
        self.condition = definition.get("condition", None)

        self.mail = definition.get("mail", "error")
        self.mailto = definition.get("mailto", self.username)
        self.sendmail = definition.get("sendmail", "/usr/lib/sendmail")

        self.conflict = definition.get("conflict", "mail")

        self.environ["JOB_ID"] = self.id
        self.environ["JOB_BLOCK"] = self.block

        self.log = logging.getLogger(self.id)

        self.calculate_next_run()

    #
    # === Runtime
    #
    def setup(self):
        """Start the mainloop task for this job.
        """
        self.task = self.mainloop()
        next(self.task)
        return self.task

    def mainloop(self):
        """Process events from the bus in an endless loop.
        """
        while True:
            event = (yield)

            if event.name == "shutdown" or (event.name == "quit" and event.job == self.id):
                # If pcron is stopped by SIGINT or SIGTERM all running child
                # processes are terminated implicitly. This code is executed
                # only if a running job is removed from the crontab.
                if self.state == RUNNING:
                    self.terminate()
                    self.poll()

                self.log.debug("quit")
                break

            elif event.name == "child":
                self.poll()

            elif event.name == "schedule" and event.job == self.id:
                self.bus.post("block", block=self.block, job=self.id)

            elif event.name == "stop" and event.job in self.post:
                self.bus.post("block", block=self.block, job=self.id)

            elif event.name == "start" and event.job == self.id:
                self.last_run = datetime.datetime.now()

                if self.condition is not None:
                    run = self.test_condition()
                    self.log.debug("test condition: %s", run)
                else:
                    run = True

                if run:
                    self.bus.post("started", job=self.id)
                    self.start()
                else:
                    self.state = SLEEPING
                    self.bus.post("unblock", block=self.block)

    #
    # === Scheduling / Execution
    #
    def schedule(self):
        if self.state == RUNNING:
            self.log.warn("scheduling conflict: exceeding runtime")
            self.log.warn("conflict handler: %s", self.conflict)
            if self.conflict == "kill":
                self.terminate()
            elif self.conflict in ("skip", "mail"):
                if self.conflict == "mail":
                    self.send_message(MAIL_SKIP_RUNNING, _info={"pid": self.runner.get_pid()})
                self.calculate_next_run()

        elif self.state == WAITING:
            self.log.warn("scheduling conflict: wait congestion")
            if self.conflict == "mail":
                self.send_message(MAIL_SKIP_WAITING)
            self.calculate_next_run()

        else:
            self.state = WAITING
            self.bus.post("schedule", job=self.id)
            self.scheduled_run = datetime.datetime.now()
            self.calculate_next_run()

    def calculate_next_run(self):
        if self.time is None and self.interval is None:
            self.next_run = datetime.datetime.max
        else:
            # FIXME improve this
            self.next_run = datetime.datetime.now().replace(second=0, microsecond=0)
            while True:
                self.next_run += datetime.timedelta(minutes=1)
                if self.time is not None and self.time.match(self.next_run):
                    break
                elif self.interval is not None and self.interval.match(self.next_run, self.scheduled_run):
                    break

    def start(self):
        """Start the job command.
        """
        self.log.info("start")
        self.log.debug("command: %s", self.command)

        self.state = RUNNING

        # Start the process.
        self.runner = Runner(self.command, self.environ, self.environment)
        try:
            self.runner.start()
        except RunnerError as exc:
            self.log.error("an error occurred running the command:")
            self.log.error(str(exc))

    def poll(self):
        """Check if the job command is still running.
        """
        if self.runner is not None and self.runner.has_finished():
            self.finalize()
            self.runner = None
            self.state = SLEEPING
            self.bus.post("unblock", block=self.block)
            self.bus.post("stop", job=self.id)

    def terminate(self):
        """Terminate the running job command prematurely.
        """
        self.log.debug("terminate")
        try:
            self.runner.terminate()
        except RunnerError as exc:
            self.log.warn(str(exc))

    def finalize(self):
        """Check if the job command failed and send emails.
        """
        # Test if the process exited with an error condition, deciding whether
        # to send a mail or not.
        send = self.mail == "always"
        if self.runner.returncode != 0:
            send = self.mail != "never"

        if self.runner.returncode > 0:
            self.log.warn("exit status: %s", self.runner.returncode)
        else:
            self.log.debug("exit status: 0")

        self.duration = self.runner.get_duration()
        self.log.info("finished")
        self.log.debug("duration %s", format_time(self.duration))
        if self.next_run < datetime.datetime.max:
            self.log.info("next run %s", self.next_run)

        # Check whether to send a mail depending on the output.
        if self.mail == "output":
            send = self.runner.get_output_size() > 0

        # Send the message if necessary.
        if send:
            # Prepare the email message's text.
            if self.runner.returncode == 0:
                text = MAIL_INFO
                info = {}
            elif self.runner.returncode > 0:
                text = MAIL_ERROR
                info = {"exitcode": self.runner.returncode}
            else:
                text = MAIL_KILLED
                info = {"signal": SIGNAL_NAMES[abs(self.runner.returncode)]}

            self.send_message(text, self.runner.get_output(), info)

        # Pull down the Runner.
        self.runner.close()

    def send_message(self, text, payload=(), _info=None):
        # Collect required information.
        info = {
            "job":      str(self),
            "mailto":   self.mailto,
            "username": self.username,
            "schedule": format_time(self.scheduled_run),
            "command":  self.command
        }
        if _info is not None:
            info.update(_info)

        text %= info

        self.log.debug("send mail to %s", self.mailto)
        try:
            process = subprocess.Popen([self.sendmail, self.mailto], stdin=subprocess.PIPE)
            stdin = io.TextIOWrapper(process.stdin, errors="replace")
            stdin.write(text)
            for line in payload:
                stdin.write(line)
            stdin.close()
        except OSError as exc:
            self.log.error("the following error occurred using %s", self.sendmail)
            self.log.error(str(exc))

    def test_condition(self):
        runner = Runner(self.condition, self.environ, self.environment)
        try:
            try:
                runner.start()
                return runner.wait() == 0
            except RunnerError as exc:
                self.log.warn(str(exc))
        finally:
            runner.close()

