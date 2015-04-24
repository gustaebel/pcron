# coding: utf8
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
import time
import signal
import logging
import pickle
import datetime

from . import ENVIRONMENT_NAME, CRONTAB_NAME
from .shared import AtomicFile, Interrupt, sleep
from .shared import RUNNING, WAITING, SLEEPING
from .time import format_time
from .parser import CrontabParser, CrontabError, extract_loglevel_from_crontab
from .event import Bus
from .job import Job


class Scheduler(object):

    def __init__(self, opts):
        self.opts = opts

        self.directory = self.opts.directory

        self.crontab_path = os.path.join(self.directory, CRONTAB_NAME)
        self.state_path = os.path.join(self.directory, "state.db")
        self.environ_path = os.path.join(self.directory, ENVIRONMENT_NAME)

        self.bus = Bus()
        self.jobs = {}
        self.running = True

        self.record, self.environ = self.create_environ(self.directory)

        self.init_logging()
        self.init_signal_handlers()

        self.load()
        self.load_state()

    @staticmethod
    def create_environ(directory):
        record = pwd.getpwuid(os.getuid())

        # Prepare the basic environment and default variables
        # for the jobs.
        environ = {
            "USER":     record.pw_name,
            "LOGNAME":  record.pw_name,
            "UID":      str(record.pw_uid),
            "GID":      str(record.pw_gid),
            "HOME":     record.pw_dir,
            "SHELL":    record.pw_shell,
            "PCRONDIR": directory
        }
        if record.pw_name == "root":
            environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        else:
            environ["PATH"] = "/usr/local/bin:/usr/bin:/bin"

        return record, environ

    def init_logging(self):
        logging.basicConfig(
                level=extract_loglevel_from_crontab(self.crontab_path),
                format="%(asctime)s  %(levelname)-7s  %(name)-10s  %(message)s",
                filename=os.path.join(self.directory, "logfile.txt") if self.opts.daemon else None)

        self.log = logging.getLogger("main")
        self.log.info("started with pid %d", os.getpid())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is not None:
            self.log.exception("a fatal internal error occurred:")
        logging.shutdown()

    #
    # === State
    #
    def load_state(self):
        try:
            with open(self.state_path, "rb") as fileobj:
                for job_id, next_run in pickle.load(fileobj).items():
                    try:
                        self.jobs[job_id].next_run = next_run
                    except KeyError:
                        continue
        except OSError as exc:
            self.log.warn(str(exc))

    def save_state(self):
        try:
            state = {}
            for job_id, job in self.jobs.items():
                state[job_id] = job.next_run
            with AtomicFile(self.state_path) as fileobj:
                pickle.dump(state, fileobj)
        except OSError as exc:
            self.log.warn(str(exc))

    #
    # === Crontab
    #
    def load(self):
        # FIXME isolate environment better from environ attr name-wise
        environment = self.load_environment()
        crontab = self.load_crontab()
        self.update_jobs(environment, crontab)

    def load_environment(self):
        try:
            with open(os.path.join(self.directory, ENVIRONMENT_NAME)) as fileobj:
                return fileobj.read()
        except FileNotFoundError:
            self.log.debug("%s/%s not found", self.directory, ENVIRONMENT_NAME)
        except OSError as exc:
            self.log.error(str(exc))
        return ""

    def load_crontab(self):
        try:
            parser = CrontabParser(self.crontab_path)
            return parser.parse()
        except OSError as exc:
            self.log.error("%s: %s", self.directory, exc)
        except CrontabError as exc:
            self.log.error("%s: %s", self.directory, exc)
            self.log.error("%s: cannot use crontab because it contains errors", self.directory)
        return {}

    #
    # === Jobs
    #
    def add_job(self, job_id, info, environment):
        job = Job(self.bus, self.record.pw_name, self.environ.copy(), environment)
        job.update(info)
        self.jobs[job_id] = job
        task = job.setup()
        self.bus.register(job_id, task)

    def remove_job(self, job_id):
        self.jobs.pop(job_id)
        self.bus.post("quit", job=job_id)
        self.bus.unregister(job_id)

    def update_jobs(self, environment, crontab):
        # Evaluate individual job definitions.
        for job_id, info in crontab.items():
            job = self.jobs.get(job_id)

            if job is None:
                # Create a new job.
                self.log.info("create job %s", job_id)
                self.add_job(job_id, info, environment)
            else:
                # Update an existing job.
                self.log.debug("update job %s", job_id)
                job.update(info)

        # Remove old jobs.
        for job_id in list(self.jobs.keys()):
            if job_id not in crontab:
                self.log.info("remove job %s", job_id)
                self.remove_job(job_id)

    #
    # === Signals
    #
    def init_signal_handlers(self):
        signal.signal(signal.SIGINT, self._signal_shutdown)
        signal.signal(signal.SIGTERM, self._signal_shutdown)
        signal.signal(signal.SIGHUP, self._signal_reload)
        signal.signal(signal.SIGUSR1, self._signal_dump)
        signal.signal(signal.SIGUSR2, signal.SIG_IGN)
        signal.signal(signal.SIGCHLD, self._signal_child)

    def _signal_shutdown(self, signum, frame):
        # pylint:disable=unused-argument
        if signum == signal.SIGINT:
            self.log.warn("keyboard interrupt")
        elif signum == signal.SIGTERM:
            self.log.warn("termination signal")

        self.bus.post("shutdown")

    def _signal_dump(self, signum, frame):
        # pylint:disable=unused-argument
        self.log.debug("received SIGUSR1 signal, dumping state")
        self.bus.post("dump")

    def _signal_reload(self, signum, frame):
        # pylint:disable=unused-argument
        self.log.debug("received SIGHUP signal, reloading crontab")
        self.bus.post("reload")

    def _signal_child(self, signum, frame):
        # pylint:disable=unused-argument
        self.log.debug("received SIGCHLD signal")
        self.bus.post("child")

    def dump(self):
        # FIXME improve
        for job in sorted(self.jobs.values(), key=lambda j: j.last_run):
            if not job.active or job.state != RUNNING:
                continue
            self.log.info("[running]   %s  %s", format_time(job.last_run), job.id)

        for job in sorted(self.jobs.values(), key=lambda j: j.scheduled_run):
            if not job.active or job.state != WAITING:
                continue
            self.log.info("[waiting]   %s  %s", format_time(job.scheduled_run), job.id)

        for job in sorted(self.jobs.values(), key=lambda j: j.next_run):
            if not job.active or job.state != SLEEPING:
                continue
            self.log.info("[sleeping]  %s  %s", format_time(job.next_run), job.id)

        for job in self.jobs.values():
            if job.active:
                continue
            self.log.info("[inactive]  %s  %s", format_time(datetime.datetime.max), job.id)

    #
    # === Scheduling
    #
    def get_waiting_jobs(self):
        """Return a list of one or more jobs that are waiting to be started
           next.
        """
        jobs = {}
        for job in self.jobs.values():
            if not job.active or (job.state == WAITING and job.next_run < datetime.datetime.now()):
                continue
            timestamp = job.next_run.timestamp()
            jobs.setdefault(timestamp, []).append(job)

        if jobs:
            return sorted(jobs.items())[0]
        else:
            return None, []

    def sleep(self):
        # Go to sleep, if there is currently no event waiting to be
        # processed. Find out when the next jobs are going to be
        # scheduled.
        timestamp, jobs = self.get_waiting_jobs()

        if timestamp is None:
            # Sleep until a signal (e.g. SIGCHLD) occurs. This means
            # that some kind of event is waiting to be processed.
            try:
                sleep()
            except Interrupt:
                return

        else:
            # Sleep until the next jobs are about to be started.
            seconds = timestamp - time.time()

            if seconds > 0:
                self.log.debug("sleep until %s", time.strftime("%H:%M", time.localtime(timestamp)))

                # If the sleep is interrupted, we throw over our plans
                # and go back and process the waiting event.
                try:
                    sleep(seconds)
                except Interrupt:
                    return

            # Schedule the waiting jobs for execution.
            for job in jobs:
                job.schedule()

    def process_event(self, event):
        # First process events that are directed at the Scheduler.
        if event.name == "reload":
            self.load()

        elif event.name == "dump":
            self.dump()

        elif event.name == "started":
            self.save_state()

        # Pass events on to the jobs.
        self.bus.process_event(event)

        if event.name == "shutdown":
            self.running = False

    def mainloop(self):
        while self.running:
            event = self.bus.get_event()

            if event is None:
                self.sleep()
            else:
                self.process_event(event)

