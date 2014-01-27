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

import sys
import os
import time
import tempfile
import signal

STATE_NAMES = ["SLEEPING", "WAITING", "RUNNING"]
SLEEPING, WAITING, RUNNING = range(len(STATE_NAMES))


class Interrupt(Exception):
    pass

class ParserError(Exception):
    pass

class CrontabError(ParserError):
    pass

class CrontabEmptyError(CrontabError):
    pass


def sleep(seconds=None):
    """Go to sleep for a certain amount of seconds. If the sleep() call is
       interrupted by a signal a RuntimeError is raised.
    """
    if seconds is None:
        signal.pause()
    else:
        start_time = time.time()
        time.sleep(seconds)
        if time.time() - start_time < seconds:
            raise Interrupt


class AtomicFile(object):

    def __init__(self, path):
        self.path = path
        self.directory, self.basename = os.path.split(path)
        fd, self.temppath = tempfile.mkstemp(dir=self.directory, prefix=self.basename)
        self.fileobj = os.fdopen(fd, "w+b")

    def write(self, buf):
        self.fileobj.write(buf)

    def close(self):
        self.fileobj.close()
        os.rename(self.temppath, self.path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.close()


class DaemonContext(object):

    def __init__(self, path, daemonize=True):
        self.path = path
        self.daemonize = daemonize

        try:
            with open(self.path, "r") as fileobj:
                pid = int(fileobj.read().strip())
        except (FileNotFoundError, ValueError):
            pass
        except OSError as exc:
            raise SystemExit(str(exc))
        else:
            raise SystemExit("%s seems to be running on pid %s" % (os.path.basename(sys.argv[0]), pid))

        if self.daemonize:
            try:
                # pylint:disable=protected-access
                if os.fork() > 0:
                    os._exit(0)
                os.setsid()
                os.chdir("/")
                if os.fork() > 0:
                    os._exit(0)
            except EnvironmentError as exc:
                raise SystemExit(str(exc))

            fd = os.open(os.devnull, os.O_RDWR)
            os.dup2(fd, 0)
            os.dup2(fd, 1)
            os.dup2(fd, 2)

    def __enter__(self):
        try:
            with open(self.path, "w") as fileobj:
                print(os.getpid(), file=fileobj)
        except OSError as exc:
            raise SystemExit(str(exc))
        return self

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False

