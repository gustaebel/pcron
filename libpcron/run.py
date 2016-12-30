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

# FIXME Rename to process.Process.

import os
import time
import subprocess
import tempfile
import shutil
import hashlib

from . import SUPPORTED_SHELLS
from .time import IntervalSpec


class RunnerError(Exception):
    pass

# Prepare shell script code with allexport and errexit properties set during
# the execution of init_code. We need a bourne-compatible shell for that.
SHELL_CODE = """
set -ea
%s
set +ea
%s
"""


class Runner:

    def __init__(self, working_dir, time_provider, command, environ, init_code):
        self.working_dir = working_dir
        self.time_provider = time_provider
        self.command = command
        self.environ = environ
        self.init_code = init_code

        try:
            os.makedirs(self.working_dir)
        except FileExistsError:
            pass

        # Create a temporary file for the command output.
        self.output_path = os.path.join(self.working_dir, "output.txt")

        # Create a temporary file for the command script.
        self.script_path = os.path.join(self.working_dir, "command.sh")

        with open(self.script_path, "w") as fobj:
            # Write the initialization code and the command to the script file.
            fobj.write(SHELL_CODE % (self.init_code, self.command))

        self.start_time = self.time_provider.now()
        self.stop_time = None

        shell = self.environ["SHELL"]

        # The user's shell must be bourne shell compatible.
        if os.path.basename(shell) not in SUPPORTED_SHELLS:
            raise RunnerError("unsupported shell %s" % shell)

        # Start the command in the user's shell.
        self.output = open(self.output_path, "w+b")
        self.process = subprocess.Popen([shell, self.script_path], cwd=self.working_dir,
                                        env=self.environ, stdout=self.output, stderr=subprocess.STDOUT)

    def has_finished(self):
        return self.process.poll() is not None

    def wait(self):
        return self.process.wait()

    def terminate(self):
        for t in range(3):
            # If SIGTERM doesn't succeed we send SIGKILL.
            if t < 2:
                self.process.terminate()
            else:
                self.process.kill()

            time.sleep(1)
            if self.has_finished():
                break

        if not self.has_finished():
            # We give up.
            raise RunnerError("command %r could not be terminated" % self.command)

    def finalize(self):
        # Save the time when the process ended.
        self.stop_time = self.time_provider.now()

        self.output.flush()
        self.output.seek(0)

    def get_duration(self):
        if self.has_finished():
            return self.stop_time - self.start_time
        else:
            return self.time_provider.now() - self.start_time

    def get_pid(self):
        if self.has_finished():
            return -1
        else:
            return str(self.process.pid)

    @property
    def returncode(self):
        return self.process.returncode

    def close(self):
        self.output.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

