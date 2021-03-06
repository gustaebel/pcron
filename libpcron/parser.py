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

import configparser
import collections

from .shared import CrontabError, CrontabEmptyError, Logger


def extract_loglevel_from_crontab(path):
    parser = configparser.RawConfigParser(default_section="default")
    try:
        parser.read([path])
    except configparser.DuplicateSectionError:
        pass

    value = parser.defaults().get("loglevel")
    if value is None:
        return Logger.WARN

    else:
        try:
            return Logger.levels[value]
        except KeyError:
            raise CrontabError("invalid loglevel value:%r" % value)


class CrontabParser:

    def __init__(self, path, jobcls):
        self.path = path
        self.jobcls = jobcls

        self.parser = configparser.ConfigParser(interpolation=None)

    def parse(self):
        try:
            with open(self.path) as fobj:
                self.parser.read_file(fobj)
        except configparser.DuplicateSectionError as exc:
            raise CrontabError("duplicate job %s" % exc.section)
        except configparser.DuplicateOptionError as exc:
            raise CrontabError("duplicate option %s in job %s" % (exc.option, exc.section))

        if not self.parser.sections():
            raise CrontabEmptyError("crontab is empty")

        parents = set()
        infos = collections.OrderedDict()
        for name in self.parser.sections():
            try:
                parent, _ = name.rsplit(".", 1)
            except ValueError:
                info = infos.get("default", {}).copy()
            else:
                if parent in infos:
                    info = infos[parent].copy()
                    parents.add(parent)
                else:
                    raise CrontabError("missing parent job %s" % parent)

            for option in self.parser.options(name):
                if option == "loglevel":
                    # FIXME ATM loglevel option is global only.
                    continue
                info[option] = self.parser.get(name, option)

            info["name"] = name
            infos[name] = info

        try:
            infos.pop("default")
        except KeyError:
            pass

        startup = collections.OrderedDict()
        jobs = collections.OrderedDict()
        for name, info in infos.items():
            job = self.jobcls.new(name, info, name in parents)
            if info.get("time") == "@reboot":
                startup[name] = job
            else:
                jobs[name] = job

            if job.active:
                posts = set(job.post) - set(infos.keys())
                if posts:
                    raise CrontabError("post job %s not found" % sorted(posts)[0])

        return startup, jobs
