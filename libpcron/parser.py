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
import re
import configparser
import logging

from .shared import CrontabError, CrontabEmptyError
from .time import TimeSpec, TimeSpecError, IntervalSpec, IntervalSpecError
from .job import Job


def extract_loglevel_from_crontab(path):
    parser = configparser.RawConfigParser(default_section="default")
    try:
        parser.read([path])
    except configparser.DuplicateSectionError as exc:
        pass

    value = parser.defaults().get("loglevel")
    if value is None:
        return logging.WARN

    else:
        try:
            return {"quiet": logging.WARN,
                    "info": logging.INFO,
                    "debug": logging.DEBUG}[value]
        except KeyError:
            raise CrontabError("invalid conflict value:%r" % value)


class CrontabParser:

    def __init__(self, path):
        self.path = path
        self.parser = configparser.RawConfigParser(default_section="default")

        self.converters = {
            "command":   self.convert_command,
            "time":      self.convert_time,
            "interval":  self.convert_interval,
            "post":      self.convert_post,
            "condition": self.convert_condition,
            "block":     self.convert_block,
            "mail":      self.convert_mail,
            "mailto":    self.convert_mailto,
            "sendmail":  self.convert_sendmail,
            "active":    self.convert_active,
            "conflict":  self.convert_conflict
        }

    def parse(self):
        try:
            self.parser.read([self.path])
        except configparser.DuplicateSectionError as exc:
            raise CrontabError("duplicate job %s" % exc.section)
        except configparser.DuplicateOptionError as exc:
            raise CrontabError("duplicate option %s in job %s" % (exc.option, exc.section))

        if not self.parser.sections():
            raise CrontabEmptyError("crontab is empty")

        jobs = {}
        for section in self.parser.sections():
            job = {}
            for option in self.parser.options(section):
                if option == "loglevel":
                    continue

                value = self.parser.get(section, option)

                try:
                    converter = self.converters[option]
                except KeyError:
                    raise CrontabError("invalid variable:%r" % option)

                job[option] = converter(value)

            job["active"] = job.get("active", True)
            job["id"] = section
            Job.check(job)
            jobs[job["id"]] = job

        return jobs

    # ------------------------------------------------------------------------
    #  Check and eval methods for job variables.
    # ------------------------------------------------------------------------
    @classmethod
    def _convert_boolean(cls, value):
        true = ("true", "yes", "t", "y", "1")
        false = ("false", "no", "f", "n", "0")
        try:
            if value.lower() not in true + false:
                raise ValueError
            value = value.lower() in true
        except ValueError:
            raise CrontabError("invalid active value:%r" % value)
        return value

    @classmethod
    def convert_command(cls, value):
        return value

    @classmethod
    def convert_time(cls, value):
        try:
            return TimeSpec(value)
        except TimeSpecError as exc:
            raise CrontabError(str(exc))

    @classmethod
    def convert_interval(cls, value):
        try:
            return IntervalSpec(value)
        except IntervalSpecError as exc:
            raise CrontabError(str(exc))

    @classmethod
    def convert_post(cls, value):
        # FIXME check job ids
        return set(value.split())

    @classmethod
    def convert_condition(cls, value):
        return cls.convert_command(value)

    @classmethod
    def convert_block(cls, value):
        # FIXME case?
        if not re.match(r"^[\w\-]+$", value):
            raise CrontabError("invalid block:%r" % value)
        return value

    @classmethod
    def convert_mail(cls, value):
        if value not in ("never", "always", "error", "output"):
            raise CrontabError("invalid mail value:%r" % value)
        return value

    @classmethod
    def convert_mailto(cls, value):
        # We don't care for valid email addresses.
        return value

    @classmethod
    def convert_sendmail(cls, value):
        if not os.path.isabs(value):
            raise CrontabError("sendmail path must be absolute:%r" % value)
        if not os.access(value, os.R_OK | os.X_OK):
            raise CrontabError("sendmail path not executable:%r" % value)
        return value

    @classmethod
    def convert_active(cls, value):
        try:
            return cls._convert_boolean(value)
        except ValueError:
            raise CrontabError("invalid active value:%r" % value)

    @classmethod
    def convert_conflict(cls, value):
        if value not in ("skip", "mail", "kill", "postpone"):
            raise CrontabError("invalid conflict value:%r" % value)
        return value

