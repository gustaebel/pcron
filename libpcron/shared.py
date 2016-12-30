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

import sys
import os
import tempfile
import traceback
import signal


SIGNALS = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGUSR1, signal.SIGCHLD}

EXC_PREFIX = ">>> "


class ParserError(Exception):
    pass

class CrontabError(ParserError):
    pass

class CrontabEmptyError(CrontabError):
    pass


class AtomicFile:

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


class DaemonContext:
    # pylint:disable=too-few-public-methods

    def __init__(self, path, daemonize=True):
        self.path = path
        self.daemonize = daemonize

        self.fileobj = open(self.path, "a")
        self.fd = self.fileobj.fileno()

        try:
            os.lockf(self.fd, os.F_TLOCK, 0)
        except (PermissionError, BlockingIOError):
            try:
                with open(self.path, "r") as lines:
                    for line in lines:
                        pid = int(line)
            except (FileNotFoundError, ValueError):
                raise SystemExit("%s seems to be running (unable to get pid)" % \
                        os.path.basename(sys.argv[0]))
            except OSError as exc:
                raise SystemExit(str(exc))

            raise SystemExit("%s is already running as pid %s" % \
                    (os.path.basename(sys.argv[0]), pid))

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

            print(os.getpid(), file=self.fileobj)
            self.fileobj.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            os.lockf(self.fd, os.F_ULOCK, 0)
        except OSError:
            pass
        try:
            self.fileobj.close()
        except OSError:
            pass
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


class SubLogger:

    def __init__(self, logger, name):
        self.logger = logger
        self.name = name

    def exception(self, message, *args):
        self.logger.log(self.name, self.logger.ERROR, message, *args)
        for line in traceback.format_exc().splitlines():
            self.logger.log(self.name, self.logger.ERROR, EXC_PREFIX + line)

    def error(self, message, *args):
        self.logger.log(self.name, self.logger.ERROR, message, *args)

    def warn(self, message, *args):
        self.logger.log(self.name, self.logger.WARN, message, *args)

    def info(self, message, *args):
        self.logger.log(self.name, self.logger.INFO, message, *args)

    def debug(self, message, *args):
        self.logger.log(self.name, self.logger.DEBUG, message, *args)


class NullLogger:


    ERROR, WARN, INFO, DEBUG = range(4)

    levels = {
        "quiet": WARN,
        "info": INFO,
        "debug": DEBUG
    }

    level_names = {
        ERROR:  "ERROR",
        WARN:   "WARNING",
        INFO:   "INFO",
        DEBUG:  "DEBUG"
    }

    def new(self, name):
        return SubLogger(self, name)

    def log(self, name, level, message, *args):
        pass


class Logger(NullLogger):

    def __init__(self, time_provider, file, level):
        super().__init__()
        self.time_provider = time_provider
        self.file = file
        self.level = level

    def log(self, name, level, message, *args):
        if level <= self.level:
            record = (
                self.time_provider.now().strftime("%Y-%m-%d %H:%M:%S"),
                self.level_names[level],
                name,
                message % args if args else message
            )
            print("%s  %-7s  %-12s  %s" % record, file=self.file, flush=True)


def create_environ(record, **kwargs):
    if not os.access(record.pw_shell, os.X_OK):
        raise CrontabError("shell %s is inaccessible" % record.pw_shell)

    env = {
        "USER":     record.pw_name,
        "LOGNAME":  record.pw_name,
        "UID":      str(record.pw_uid),
        "GID":      str(record.pw_gid),
        "HOME":     record.pw_dir,
        "SHELL":    record.pw_shell,
        "PATH":     "/usr/local/bin:/usr/bin:/bin" if record.pw_uid > 0 else \
                    "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
        "LANG":     os.environ["LANG"]
    }
    env.update(kwargs)
    return env

