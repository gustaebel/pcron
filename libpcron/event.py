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

import logging


class Event(object):

    def __init__(self, name, **kwargs):
        self.name = name
        for key, value in kwargs.items():
            setattr(self, key, value)


class Bus(object):

    def __init__(self):
        self.queue = []
        self.tasks = {}
        self.blockmanager = BlockManager()

    def register(self, job_id, task):
        assert job_id not in self.tasks
        self.tasks[job_id] = task

    def post(self, name, **kwargs):
        self.queue.append(Event(name, **kwargs))

    def get_event(self):
        if not self.queue:
            return None
        return self.queue.pop(0)

    def process_event(self, event):
        for job_id, task in sorted(self.tasks.items()):
            try:
                task.send(event)
            except StopIteration:
                self.tasks.pop(job_id)

        if event.name == "block":
            if self.blockmanager.block(event.block, event.job):
                self.post("start", job=event.job)

        elif event.name == "unblock":
            job_id = self.blockmanager.unblock(event.block, event.job)
            if job_id is not None:
                self.post("start", job=job_id)


class BlockManager(object):
    """BlockManager keeps track of currently active blocks and those jobs
       waiting for them to be released.
    """

    def __init__(self):
        self.blocks = {}

    def block(self, name, job_id):
        log = logging.getLogger("@" + name)

        jobs = self.blocks.get(name, [])
        assert job_id not in jobs

        if not jobs:
            log.debug("acquired by %s", job_id)
            acquired = True
        else:
            log.debug("postpone %s (blocked by %s)", job_id, jobs[0])
            acquired = False

        self.blocks.setdefault(name, []).append(job_id)
        return acquired

    def unblock(self, name, job_id):
        log = logging.getLogger("@" + name)

        log.debug("released by %s", job_id)
        jobs = self.blocks.get(name, [])
        assert jobs and jobs.pop(0) == job_id

        if jobs:
            log.debug("resume %s", jobs[0])
            return jobs[0]

