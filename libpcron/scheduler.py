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
import signal
import logging
import pickle
import itertools
import collections

from . import ENVIRONMENT_NAME, CRONTAB_NAME
from .shared import AtomicFile, Logger, SIGNALS
from .time import format_time
from .parser import CrontabParser, CrontabError, extract_loglevel_from_crontab
from .job import Job
from .mail import Mailer


class Scheduler:

    Job = Job
    Mailer = Mailer

    STATE_TAG = 1

    def __init__(self, time_provider, directory, logfile=None, persistent_state=True):
        assert os.path.isabs(directory)

        self.time_provider = time_provider
        self.directory = directory
        self.logfile = logfile
        self.persistent_state = persistent_state

        self.crontab_path = os.path.join(self.directory, CRONTAB_NAME)
        self.environ_path = os.path.join(self.directory, ENVIRONMENT_NAME)
        self.state_path = os.path.join(self.directory, "state.db")

        if self.logfile is None:
            self.logfile = open(os.path.join(self.directory, "logfile.txt"), "a")

        try:
            loglevel = extract_loglevel_from_crontab(self.crontab_path)
        except CrontabError as exc:
            log_exc = exc
            loglevel = None

        self.logger = Logger(self.time_provider, self.logfile, loglevel if loglevel else Logger.INFO)

        self.mailer = self.Mailer(self.logger)

        self.log = self.logger.new("main")
        self.log.info("started with pid %d", os.getpid())

        if loglevel is None:
            self.log.error(str(log_exc))

        self.running = {}
        self.queues = {}
        self.serial = collections.Counter()

        self.init_signal_handling()

        self.load()
        self.load_state()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is not None:
            self.log.exception("a fatal internal error occurred:")
        logging.shutdown()

    #
    # === Crontab
    #
    def load(self):
        self.init_code = self.load_init_code()
        self.startup, self.crontab = self.load_crontab()

        for job in itertools.chain(self.startup.values(), self.crontab.values()):
            job.init(self.time_provider, self.logger, self.directory, self.init_code)

    def _load_init_code(self):
        with open(os.path.join(self.directory, ENVIRONMENT_NAME)) as fileobj:
            return fileobj.read()

    def load_init_code(self):
        try:
            return self._load_init_code()
        except FileNotFoundError:
            self.log.debug("%s/%s not found", self.directory, ENVIRONMENT_NAME)
        except OSError as exc:
            self.log.error(str(exc))
        except UnicodeError as exc:
            self.log.error("%s: unable to decode environment file", self.directory)
        return ""

    def _load_crontab(self):
        parser = CrontabParser(self.crontab_path, self.Job)
        return parser.parse()

    def load_crontab(self):
        try:
            return self._load_crontab()
        except OSError as exc:
            self.log.error("%s: %s", self.directory, exc)
        except UnicodeError as exc:
            self.log.error("%s: unable to decode crontab file", self.directory)
        except CrontabError as exc:
            self.log.error("%s: %s", self.directory, exc)
            self.log.error("%s: cannot use crontab because it contains errors", self.directory)
        return {}, {}

    #
    # === State
    #
    def load_state(self):
        if self.persistent_state:
            self.log.debug("load state from %r", self.state_path)
            try:
                with open(self.state_path, "rb") as fileobj:
                    state = pickle.load(fileobj)

                    if state.get("tag") == self.STATE_TAG:
                        for name, next_run in state["jobs"].items():
                            try:
                                self.log.debug("restore %s %s", name, format_time(next_run))
                                self.crontab[name].next_run = next_run
                            except KeyError:
                                continue
                    else:
                        self.log.warn("ignore obsolete state")

            except OSError as exc:
                self.log.warn(str(exc))

    def save_state(self):
        if self.persistent_state:
            state = {"tag": self.STATE_TAG, "jobs": {}}
            for job in self.crontab.values():
                state["jobs"][job.name] = job.next_run

            self.log.debug("save state to %r", self.state_path)
            try:
                with AtomicFile(self.state_path) as fileobj:
                    pickle.dump(state, fileobj)
            except OSError as exc:
                self.log.warn(str(exc))

    #
    # === Signals
    #
    def init_signal_handling(self):
        # In Python 3.5 system calls are no longer interrupted by signals.
        # Thus, we can no longer use time.sleep() or otherwise the processing
        # of signals would be delayed until the sleep has finished.
        # So, we now use signal.sigtimedwait() instead. Due to the lack of
        # documentation (and my unwillingness to spend more time on this than
        # necessary) I'm not quite sure, if I'm doing this completely right.
        # In the following, we set all signals that we're interested in as
        # blocked, so that they queue up. In TimeProvider.sleep() they're taken
        # again from the queue by signal.sigtimedwait().
        signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)

    def dump(self):
        jobs = set()

        for job in sorted(self.running.values(), key=lambda j: j.this_run):
            self.log.info("[running]   %s  %s", format_time(job.this_run), job.name)
            jobs.add(job.name)

        for queue in sorted((q for q in self.queues.values() if q), key=lambda q: q[0].this_run):
            job = queue[0]
            self.log.info("[waiting]   %s  %s", format_time(job.this), job.name)
            jobs.add(job.name)

        for job in sorted(self.crontab.values(), key=lambda j: j.next_run):
            if not job.active or job.name in jobs:
                continue
            self.log.info("[sleeping]  %s  %s", format_time(job.next_run), job.name)

        for job in self.crontab.values():
            if job.active or job.name in jobs:
                continue
            self.log.info("[inactive]  %s  %s", format_time(self.time_provider.infinity), job.name)

    #
    # === Scheduling
    #
    def mainloop(self):
        for job in self.startup.values():
            if job.active:
                self.enqueue_job(job("reboot"))

        self.log.debug("loop enter")

        signum = None
        while not self.process_signal(signum):
            self.log.debug("loop iterate")
            self.process_pending_jobs()
            self.process_finished_jobs()
            self.process_waiting_jobs()
            signum = self.wait()

        self.log.debug("loop exit")

        self.shutdown()

    def process_signal(self, signum):
        """Process the last signal, return True if a termination signal has
           been received.
        """
        # First process events that are directed at the Scheduler.
        if signum == signal.SIGINT:
            self.log.warn("received SIGINT signal, interrupting")
            return True

        elif signum == signal.SIGTERM:
            self.log.warn("received SIGTERM signal, terminating")
            return True

        elif signum == signal.SIGUSR1:
            self.log.debug("received SIGUSR1 signal, dumping state")
            self.dump()

        elif signum == signal.SIGHUP:
            self.log.debug("received SIGHUP signal, reloading crontab")
            self.load()

        elif signum == signal.SIGCHLD:
            self.log.debug("received SIGCHLD signal")

        elif signum is None:
            pass

        else:
            self.log.warn("got unsupported signal %d" % signum)

        return False

    def process_pending_jobs(self):
        """Go through the crontab and enqueue jobs that are supposed to be
           scheduled.
        """
        now = self.time_provider.now()
        for job in self.crontab.values():
            if job.active and job.next_run <= now:
                self.enqueue_job(job(job.next_trigger))
                job.advance()

    def process_finished_jobs(self):
        """Go through the list of running jobs and look for jobs that have
           finished.
        """
        state_changed = False
        for job in list(self.running.values()):
            if job.has_finished():
                self.running.pop(job.queue)
                job.finalize()
                self.mailer.send_job_mail(job)
                job.close()

                for j in self.crontab.values():
                    if j.active and job.name in j.post:
                        self.enqueue_job(j("post"))

                        # Reset the interval timestamp generator.
                        if j.interval is not None:
                            j.interval.reset_timestamp_generator(j.time_provider.next_minute())

                state_changed = True

        if state_changed:
            self.save_state()

    def process_waiting_jobs(self):
        """Go through the queues and start a waiting job for each queue that
           has currently no running job.
        """
        for name, queue in sorted(self.queues.items(), reverse=False):
            if not queue:
                continue
            while queue and queue[0].queue not in self.running:
                job = queue.pop(0)
                self.start_job(job)

    def wait(self):
        next_run = self.time_provider.infinity

        for job in self.crontab.values():
            if job.active and job.next_run < next_run:
                next_run = job.next_run

        if next_run is not self.time_provider.infinity:
            sleep = next_run - self.time_provider.now()
        else:
            sleep = self.time_provider.timedelta(minutes=60)

        seconds = sleep.total_seconds()

        if seconds > 0:
            # FIXME
            self.log.debug("sleep until %s", (self.time_provider.now() + sleep).strftime("%H:%M"))
            return self.time_provider.sleep(seconds)

    def shutdown(self):
        self.log.debug("shutting down ...")
        for job in self.running.values():
            job.terminate()
        self.process_finished_jobs()
        self.save_state()
        self.log.debug("shutting down done")

    def start_job(self, job):
        if job.start():
            self.running[job.queue] = job

    def enqueue_job(self, job):
        running_job = self.running.get(job.queue)
        queue = self.queues.get(job.queue, [])
        names = set(j.name for j in queue)

        if running_job is not None and running_job.name == job.name:
            job.log.debug("queue %s blocked by %s", job.queue, running_job)
            running_job.log.warn("scheduling conflict: exceeding runtime -> %s" % job.conflict)

            if job.conflict == "kill":
                running_job.terminate()

            if job.conflict != "skip":
                job.enqueue(queue)

            if job.conflict in ("kill", "skip") and job.warn:
                self.mailer.send_conflict_mail(job, running_job, True)

        elif job.name in names:
            job.log.debug("queue %s blocked by %s", job.queue, list(j for j in queue if j.name == job.name)[0])
            job.log.warn("scheduling conflict: wait congestion -> skip")

            if job.conflict == "ignore":
                job.enqueue(queue)

            elif job.warn:
                self.mailer.send_conflict_mail(job, running_job, False)

        else:
            job.enqueue(queue)

        self.queues[job.queue]= queue

