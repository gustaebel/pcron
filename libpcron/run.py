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
import time
import subprocess
import datetime
import tempfile


class RunnerError(Exception):
    pass

SUPPORTED_SHELLS = set(["sh", "bash", "ksh", "zsh", "dash"])

# Prepare shell script code with allexport and errexit properties set during
# the execution of init_code. We need a bourne-compatible shell for that.
SHELL_CODE = """
set -ea
%s
set +ea
%s
"""


class Runner:
    # TODO make this a context manager.

    def __init__(self, command, environ, init_code):
        self.command = command
        self.environ = environ
        self.init_code = init_code

        self.shell = environ["SHELL"]
        self.process = None
        self.output = None

        # Create a temporary file for the command output.
        fd, self.output_name = tempfile.mkstemp(prefix="tmp.pcron.")
        os.close(fd)

        # Create a temporary file for the command script.
        self.script = tempfile.NamedTemporaryFile(prefix="tmp.pcron-cmd.", mode="w", delete=False)
        self.script_name = self.script.name

        # Write the initialization code and the command to the script file.
        code = SHELL_CODE % (self.init_code, self.command)
        self.script.write(code)
        self.script.close()

    def start(self):
        # pylint:disable=attribute-defined-outside-init
        self.start_time = datetime.datetime.now()
        self.stop_time = None
        self.finished = False
        self.output_size = None

        # The user's shell must be bourne shell compatible.
        if os.path.basename(self.shell) not in SUPPORTED_SHELLS:
            raise RunnerError("unsupported shell %s" % self.shell)

        # Start the command in the user's shell.
        try:
            self.process = subprocess.Popen([self.shell, self.script_name],
                    env=self.environ,
                    stdout=open(self.output_name, "wb"),
                    stderr=subprocess.STDOUT)

        except OSError as exc:
            raise RunnerError(str(exc))

    def wait(self):
        self.process.wait()
        return self.process.returncode

    def terminate(self):
        tries = 5
        while tries > 0 and not self.has_finished():
            # If SIGTERM doesn't succeed we send SIGKILL.
            if tries > 2:
                self.process.terminate()
            else:
                self.process.kill()
            time.sleep(1)
            tries -= 1

        if not self.has_finished():
            # We give up.
            raise RunnerError("command %r could not be terminated" % self.command)

    def has_finished(self):
        # pylint:disable=attribute-defined-outside-init
        if self.finished:
            return True

        if self.process is not None and self.process.poll() is None:
            return False
        else:
            # Save the time when the process ended.
            self.stop_time = datetime.datetime.now()

            # Save the size of the command output.
            self.output_size = os.path.getsize(self.output_name)

            # Reopen the logfile for reading.
            self.output = open(self.output_name, "rb")

            self.finished = True
            return True

    def get_start_time(self):
        return self.start_time

    def get_duration(self):
        if self.finished:
            return self.stop_time - self.start_time
        else:
            return datetime.datetime.now() - self.start_time

    def get_output_size(self):
        return self.output_size

    def get_output(self):
        return self.output

    def get_pid(self):
        if self.finished:
            return "none"
        else:
            return str(self.process.pid)

    @property
    def returncode(self):
        return self.process.returncode

    def close(self):
        if self.output is not None:
            self.output.close()
        os.remove(self.output_name)
        os.remove(self.script_name)

