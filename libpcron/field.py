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

import re
import types

from .shared import CrontabError
from .time import TimeSpec, TimeSpecError, IntervalSpec, IntervalSpecError


NODEFAULT = object()


class _Field:

    def __init__(self, default=NODEFAULT, schedule=False):
        self.default = default
        self.schedule = schedule

    def __call__(self, value):
        return self._convert(value)

    def _convert(self, value):
        return value

    def get_default(self, job):
        if self.default is NODEFAULT:
            raise CrontabError("variable is required")
        elif isinstance(self.default, types.FunctionType):
            return self.default(job)
        else:
            return self.default


class String(_Field):

    def __init__(self, default=NODEFAULT, schedule=False, choices=None, regex=None):
        super().__init__(default, schedule)
        self.choices = choices if choices else None
        self.regex = re.compile(regex) if regex else None

    def _convert(self, value):
        value = super()._convert(value)

        if self.choices is not None and value not in self.choices:
            raise CrontabError("invalid choice:%r" % value)
        if self.regex is not None and self.regex.match(value) is None:
            raise CrontabError("invalid string:%r" % value)
        return value


class Time(_Field):

    def _convert(self, value):
        if value == "@reboot":
            return None

        value = super()._convert(value)
        try:
            return TimeSpec(value)
        except TimeSpecError as exc:
            raise CrontabError(str(exc))


class Interval(_Field):

    def _convert(self, value):
        value = super()._convert(value)
        try:
            return IntervalSpec(value)
        except IntervalSpecError as exc:
            raise CrontabError(str(exc))


class ListOfStrings(String):

    def _convert(self, value):
        values = []
        for value in value.split():
            values.append(super()._convert(value))
        return values


class Boolean(_Field):

    def _convert(self, value):
        value = super()._convert(value)
        true = ("true", "yes", "t", "y", "1")
        false = ("false", "no", "f", "n", "0")
        try:
            if value.lower() not in true + false:
                raise ValueError
            value = value.lower() in true
        except ValueError:
            raise CrontabError("invalid boolean value:%r" % value)
        return value

