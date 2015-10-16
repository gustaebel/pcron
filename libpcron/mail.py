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

import sys
import os
import signal
import subprocess
import email

from .time import format_time
from .shared import EXC_PREFIX


SIGNAL_NAMES = dict((getattr(signal, name), name) \
    for name in dir(signal) if name.startswith("SIG") and "_" not in name)


MAIL_INFO = """\
From: pcron <%(username)s>
To: %(mailto)s
Content-Type: text/plain; charset="%(encoding)s"
Pcron-Status: INFO
Subject: pcron: %(timestamp)s %(job)s

"""

MAIL_ERROR = """\
From: pcron <%(username)s>
To: %(mailto)s
Content-Type: text/plain; charset="%(encoding)s"
Pcron-Status: ERROR
Subject: pcron: ERROR: %(timestamp)s %(job)s

Job %(job)s exited with error code %(exitcode)s.

"""

MAIL_KILLED = """\
From: pcron <%(username)s>
To: %(mailto)s
Content-Type: text/plain; charset="%(encoding)s"
Pcron-Status: KILLED
Subject: pcron: KILLED! %(timestamp)s %(job)s

Job %(job)s was killed by signal %(signal)s.

"""

MAIL_SKIP_WAITING = """\
From: pcron <%(username)s>
To: %(mailto)s
Content-Type: text/plain; charset="%(encoding)s"
Pcron-Status: CONFLICT SKIP
Subject: pcron: WARNING! %(timestamp)s %(job)s

The scheduled run for job %(job)s was skipped because another instance
of the job is already waiting to start.
"""

MAIL_SKIP_RUNNING = """\
From: pcron <%(username)s>
To: %(mailto)s
Content-Type: text/plain; charset="%(encoding)s"
Pcron-Status: CONFLICT SKIP
Subject: pcron: WARNING! %(timestamp)s %(job)s

The scheduled run for job %(job)s was skipped because another instance
of the job is still running.

    %(command)s

The process is running with pid %(pid)s.
"""

MAIL_KILL_RUNNING = """\
From: pcron <%(username)s>
To: %(mailto)s
Content-Type: text/plain; charset="%(encoding)s"
Pcron-Status: CONFLICT KILL
Subject: pcron: WARNING! %(timestamp)s %(job)s

Running job %(job)s was killed in favor of a new instance.
"""


class Mailer:

    def __init__(self, logger):
        self.log = logger.new("mail")

    def send_job_mail(self, job):
        # Test if the process exited with an error condition and decide whether
        # to send a mail or not.
        send = job.mail == "always"
        if job.runner.returncode != 0:
            send = job.mail != "never"
            self.log.warn("exit status: %s", job.runner.returncode)
        else:
            self.log.info("exit status: 0")

        # Check whether to send a mail depending on the output.
        if job.mail == "output":
            size = os.fstat(job.runner.output.fileno()).st_size
            send = size > 0

        # Send the message if necessary.
        if send:
            # Prepare the email message's text.
            returncode = job.runner.returncode

            if returncode == 0:
                text = MAIL_INFO
            elif returncode > 0:
                text = MAIL_ERROR
            else:
                text = MAIL_KILLED

            self.send_message(text, job)

        else:
            # Tear down the Runner and close the output file object.
            job.runner.close()

    def send_conflict_mail(self, new_job, old_job, running): # FIXME
        if new_job.conflict == "kill":
            if running:
                text = MAIL_KILL_RUNNING
            else:
                text = MAIL_SKIP_WAITING

        elif new_job.conflict == "skip":
            if running:
                text = MAIL_SKIP_RUNNING
            else:
                text = MAIL_SKIP_WAITING

        self.send_message(text, new_job)

    def send_message(self, text, job):
        if job.mail == "never":
            # FIXME
            return

        text %= {
            "job":      str(job.id),
            "mailto":   job.mailto,
            "username": job.username,
            "timestamp": format_time(job.this_run),
            "command":  job.command,
            "exitcode": job.runner.returncode if job.runner is not None else -1,
            "signal":   SIGNAL_NAMES[abs(job.runner.returncode)] if job.runner and job.runner.returncode < 0 else "NONE",
            "pid":      job.runner.get_pid() if job.runner is not None else -1,
            "encoding": job.locale.split(".", 1)[1]
        }

        self.send(job.sendmail, job.mailto, job.working_dir, job.environ, text, job.runner.output if job.runner is not None else None)

        #if job.runner is not None:
        #    job.runner.output.close()

    def send(self, sendmail, mailto, directory, environ, text, output=None):
        self.log.debug("send mail to %s", mailto)

        if "{}" in sendmail:
            command = sendmail.replace("{}", mailto)
        else:
            command = sendmail + " " + mailto

        with open(os.path.join(directory, "sendmail.txt"), "w+") as fileobj:
            log_error = False
            try:
                process = subprocess.Popen(
                        command, shell=True, cwd=directory, env=environ,
                        stdin=subprocess.PIPE, stdout=fileobj, stderr=subprocess.STDOUT)
            except OSError as exc:
                self.log.error("%r failed: %s", command, exc)
                log_error = True
            else:
                # Write boilerplate.
                process.stdin.write(text.encode("utf8"))

                # Write job logfile.
                while process.poll() is None:
                    buf = output.read(512)
                    if not buf:
                        break
                    process.stdin.write(buf)

                process.stdin.close()

                if process.wait() != 0:
                    self.log.error("%r failed with exit code %s", command, process.wait())
                    log_error = True

            if log_error:
                fileobj.flush()
                fileobj.seek(0)

                for line in fileobj:
                    self.log.error(EXC_PREFIX + line)
