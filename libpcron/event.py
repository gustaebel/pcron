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

import logging


class Event(object):

    def __init__(self, name, **kwargs):
        self.name = name
        for key, value in kwargs.items():
            setattr(self, key, value)


class Bus(object):

    def __init__(self):
        self.inq = []
        self.tasks = {}
        self.blockmanager = BlockManager()

    def register(self, job_id, task):
        assert job_id not in self.tasks
        self.tasks[job_id] = task

    def unregister(self, job_id):
        assert job_id in self.tasks
        self.tasks.pop(job_id)

    def post(self, name, **kwargs):
        self.inq.append(Event(name, **kwargs))

    def get_event(self):
        if not self.inq:
            return None
        return self.inq.pop(0)

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
            job_id = self.blockmanager.unblock(event.block)
            if job_id is not None:
                self.post("start", job=job_id)


class BlockManager(object):

    def __init__(self):
        self.blocks = set()
        self.waiters = {}

    def block(self, name, job_id):
        log = logging.getLogger("@" + name)
        if name in self.blocks:
            assert job_id not in self.waiters.get(name, [])
            log.debug("postpone %s", job_id)
            self.waiters.setdefault(name, []).append(job_id)
            return False
        else:
            log.debug("acquired by %s", job_id)
            self.blocks.add(name)
            return True

    def unblock(self, name):
        log = logging.getLogger("@" + name)
        if name in self.waiters and self.waiters[name]:
            job_id = self.waiters[name].pop(0)
            log.debug("resume %s", job_id)
            return job_id
        else:
            log.debug("released")
            self.blocks.remove(name)

