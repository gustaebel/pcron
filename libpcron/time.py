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

import re
import datetime

from libpcron.shared import ParserError


class TimeSpecError(ParserError):
    pass

class IntervalSpecError(ParserError):
    pass


class TimeSpec(object):

    r_asterisk = re.compile(r"(?P<asterisk>\*)(?:/(?P<step>\d+))?$")
    r_single = re.compile(r"(?P<first>[a-zA-Z0-9]+)$")
    r_range = re.compile(r"(?P<first>[a-zA-Z0-9]+)-(?P<last>[a-zA-Z0-9]+)(?:/(?P<step>\d+))?(?P<except>(?:~[a-zA-Z0-9]+)*)$")
    r_except = re.compile(r"~([a-zA-Z0-9]+)")

    def __init__(self, value):
        self.value = value
        try:
            minute, hour, day_of_month, month, day_of_week = value.split()
        except ValueError:
            raise TimeSpecError("malformed timestamp:%r" % value)

        self.minutes = self.parse_minute(minute)
        self.hours = self.parse_hour(hour)
        self.days_of_month = self.parse_day_of_month(day_of_month)
        self.months = self.parse_month(month)
        self.days_of_week = self.parse_day_of_week(day_of_week)

    @classmethod
    def _parse_spec(cls, name, minimum, maximum, value, names=None):
        result = set()

        for spec in value.split(","):
            for regex in (cls.r_asterisk, cls.r_single, cls.r_range):
                match = regex.match(spec)
                if match is not None:
                    groups = match.groupdict()
                    break
            else:
                raise TimeSpecError("invalid %s value:%r" % (name, spec))

            try:
                first = groups.get("first")
                if first is None:
                    first = minimum
                else:
                    first = int(first)
            except ValueError:
                if first not in names:
                    raise TimeSpecError("invalid %s value:%r" % (name, first))
                first = names[first]

            if not minimum <= first < maximum:
                raise TimeSpecError("%s value %r not in range (%d-%d)" % (name, first, minimum, maximum - 1))

            try:
                last = groups.get("last")
                if last is None:
                    if groups.get("asterisk"):
                        last = maximum - 1
                    else:
                        last = first
                else:
                    last = int(last)
            except ValueError:
                if last not in names:
                    raise TimeSpecError("invalid %s value:%r" % (name, last))
                last = names[last]

            if not first <= last < maximum:
                raise TimeSpecError("%s last value %r not in range (%d-%d)" % (name, last, first, maximum - 1))

            step = int(groups.get("step") or "1")

            if not 1 <= step < maximum:
                raise TimeSpecError("%s step value %r not in range (1-%d)" % (name, first, maximum - 1))

            exceptions = set()
            for match in cls.r_except.finditer(groups.get("except", "")):
                exc = match.group(1)
                try:
                    exc = int(exc)
                except ValueError:
                    if exc not in names:
                        raise TimeSpecError("invalid %s value:%r" % (name, exc))
                    exc = names[exc]
                if not first <= exc <= last:
                    raise TimeSpecError("%s except value %r not in range (%d-%d)" % (name, exc, first, last))
                exceptions.add(exc)

            for minute in range(first, last + 1, step):
                if minute not in exceptions:
                    result.add(minute)

        return list(sorted(result))

    @classmethod
    def parse_minute(cls, value):
        return cls._parse_spec("minute", 0, 60, value)

    @classmethod
    def parse_hour(cls, value):
        return cls._parse_spec("hour", 0, 24, value)

    @classmethod
    def parse_day_of_month(cls, value):
        return cls._parse_spec("day of month", 1, 32, value)

    @classmethod
    def parse_month(cls, value):
        return cls._parse_spec("month", 1, 13, value,
            {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12})

    @classmethod
    def parse_day_of_week(cls, value):
        return cls._parse_spec("day of week", 0, 8, value,
            {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6})

    def match(self, dt):
        # pylint:disable=invalid-name
        if dt.minute in self.minutes:
            if dt.hour in self.hours:
                if dt.day in self.days_of_month:
                    if dt.month in self.months:
                        if dt.isoweekday() % 7 in self.days_of_week:
                            return True
        return False

    def match(self, dt):
        return dt.minute in self.minutes and dt.hour in self.hours and \
                dt.day in self.days_of_month and dt.month in self.months and \
                dt.isoweekday() % 7 in self.days_of_week

    def as_tuple(self):
        return (self.minutes, self.hours, self.days_of_month, self.months, self.days_of_week)

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return other.as_tuple() == self.as_tuple()

    def __ne__(self, other):
        return not other == self

    def __str__(self):
        return self.value


class IntervalSpec(object):

    # XXX be more permissive?
    r_interval = re.compile(r"(?:(?P<month>\d+)m)?(?:(?P<week>\d+)w)?(?:(?P<day>\d+)d)?(?:(?P<hour>\d+)h)?(?:(?P<minute>\d+))?$", re.IGNORECASE)

    def __init__(self, value, allow_zero=False):
        self.value = value
        self.interval = self.parse(value)
        if not allow_zero and not self.interval:
            raise IntervalSpecError("interval must not be zero")

    def parse(self, value):
        # pylint:disable=invalid-name
        match = self.r_interval.match(value)
        if match is None:
            raise IntervalSpecError("malformed interval:%r" % value)

        values = {}
        for key, val in match.groupdict().items():
            if val is None:
                continue
            values[key] = int(val)

        td = datetime.timedelta()
        td += datetime.timedelta(weeks=values.get("month", 0) * 4)
        td += datetime.timedelta(weeks=values.get("week", 0))
        td += datetime.timedelta(days=values.get("day", 0))
        td += datetime.timedelta(hours=values.get("hour", 0))
        td += datetime.timedelta(minutes=values.get("minute", 0))
        return td

    def get_timedelta(self):
        return self.interval

    def match(self, dt, last):
        # pylint:disable=invalid-name
        return dt >= last + self.interval

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return other.interval == self.interval

    def __ne__(self, other):
        return not other == self

    def __str__(self):
        return format_time(self.interval)


def format_time(t):
    if isinstance(t, float):
        t = datetime.datetime.fromtimestamp(t)

    if isinstance(t, datetime.timedelta):
        seconds = t.total_seconds()
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        result = ""
        if days:
            result += "%dd" % days
        if hours:
            result += "%dh" % hours
        if minutes:
            result += "%dm" % minutes
        if seconds:
            result += "%ds" % seconds
        if not result:
            result = "0s"
        return result

    elif isinstance(t, datetime.time):
        return t.strftime("%H:%M")

    elif isinstance(t, datetime.datetime):
        if t == datetime.datetime.max:
            return "--------/----"
        else:
            return t.strftime("%Y%m%d/%H%M")

    else:
        return str(t)

