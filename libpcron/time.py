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
import time
import signal
import datetime

from .shared import ParserError, Interrupt


class TimeSpecError(ParserError):
    pass

class IntervalSpecError(ParserError):
    pass


class TimeSpec:

    r_asterisk = re.compile(r"(?P<asterisk>\*)(?:/(?P<step>\d+))?$")
    r_single = re.compile(r"(?P<first>[a-zA-Z0-9]+)$")
    r_range = re.compile(r"(?P<first>[a-zA-Z0-9]+)-(?P<last>[a-zA-Z0-9]+)(?:/(?P<step>\d+))?"\
                         r"(?P<except>(?:~[a-zA-Z0-9]+)*)$")
    r_except = re.compile(r"~([a-zA-Z0-9]+)")

    def __init__(self, value):
        self.value = value

        # NOTE @reboot is handled somewhere else.
        if self.value in ("@yearly", "@annually"):
            self.value = "0 0 1 1 *"
        elif self.value == "@monthly":
            self.value = "0 0 1 * *"
        elif self.value == "@weekly":
            self.value = "0 0 * * 0"
        elif self.value in ("@daily", "@midnight"):
            self.value = "0 0 * * *"
        elif self.value == "@hourly":
            self.value = "0 * * * *"

        try:
            minute, hour, days_of_month, month, days_of_week = self.value.split()
        except ValueError:
            raise TimeSpecError("malformed timestamp:%r" % self.value)

        self.minutes = self.parse_minute(minute)
        self.hours = self.parse_hour(hour)
        self.days_of_month = self.parse_day_of_month(days_of_month)
        self.months = self.parse_month(month)
        self.days_of_week = self.parse_day_of_week(days_of_week)

        self.days_of_month_set = days_of_month != "*"
        self.days_of_week_set = days_of_week != "*"

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
                raise TimeSpecError("%s value %r not in range (%d-%d)" % \
                        (name, first, minimum, maximum - 1))

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
                raise TimeSpecError("%s last value %r not in range (%d-%d)" % \
                        (name, last, first, maximum - 1))

            step = int(groups.get("step") or "1")

            if not 1 <= step < maximum:
                raise TimeSpecError("%s step value %r not in range (1-%d)" % \
                        (name, first, maximum - 1))

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
                    raise TimeSpecError("%s except value %r not in range (%d-%d)" % \
                            (name, exc, first, last))
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
        return cls._parse_spec(
            "month", 1, 13, value,
            {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12})

    @classmethod
    def parse_day_of_week(cls, value):
        # We use a range of 0 to 8, because sunday is either 0 or 7.
        return cls._parse_spec(
            "day of week", 0, 8, value,
            {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6})

    def match(self, dt):
        # NOTE This is used in the unittests only.
        return dt == next(self.timestamp_generator(dt))

    def iter_days(self, year, month, day):
        """Generate a list of the days of a particular month (starting at day).

        The days have to be recalculated for every month, because weekdays
        must be taken into account. If both "day of month" and "day of week" are
        not `*` placeholders, they both add up instead of restricting each other.
        """
        for day in range(day, 32):
            try:
                date = datetime.date(year, month, day)
            except ValueError:
                break

            if not self.days_of_month_set and not self.days_of_week_set:
                yield date.day
            elif self.days_of_month_set and date.day in self.days_of_month:
                yield date.day
            elif self.days_of_week_set and date.isoweekday() % 7 in self.days_of_week:
                yield date.day

    def timestamp_generator(self, now):
        """Infinite generator of time stamps.
        """
        year = now.year
        months = [m for m in self.months if m >= now.month]
        day = now.day if (months and months[0] == now.month) else 1
        hours = [h for h in self.hours if h >= now.hour]
        minutes = [m for m in self.minutes if m >= now.minute]

        # FIXME this looks horrible and has to be fixed.
        while True:
            for month in months:
                for day in self.iter_days(year, month, day):
                    for hour in hours:
                        for minute in minutes:
                            try:
                                yield datetime.datetime(year=year, month=month, day=day,
                                                        hour=hour, minute=minute)
                            except ValueError:
                                continue
                        minutes = self.minutes
                    minutes = self.minutes
                    hours = self.hours
                day = 1
                minutes = self.minutes
                hours = self.hours
            day = 1
            minutes = self.minutes
            hours = self.hours
            months = self.months
            year += 1

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


class IntervalSpec:

    # XXX be more permissive?
    r_interval = re.compile(r"(?:(?P<month>\d+)m)?(?:(?P<week>\d+)w)?(?:(?P<day>\d+)d)?"\
                            r"(?:(?P<hour>\d+)h)?(?:(?P<minute>\d+))?$", re.IGNORECASE)

    def __init__(self, value, allow_zero=False):
        self.value = value
        self.interval = self.parse(value)
        if not allow_zero and not self.interval:
            raise IntervalSpecError("interval must not be zero")
        self.dt = None

    def parse(self, value):
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
        return dt >= last + self.interval

    def timestamp_generator(self, dt):
        """Infinite generator of time stamps.
        """
        self.dt = dt
        while True:
            yield self.dt
            self.dt += self.get_timedelta()

    def reset_timestamp_generator(self, dt):
        self.dt = dt

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        return other.interval == self.interval

    def __ne__(self, other):
        return not other == self

    def __str__(self):
        return format_time(self.interval)


def format_time(t):
    # FIXME unfold this and work around the pylint warning
    # pylint:disable=no-member
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


class TimeProvider:
    """The TimeProvider class provides time-related functions that are based on
       the datetime module that offer real-time operation. The sole purpose of
       this class is that we can have the TestTimeProvider subclass.
    """

    origin = datetime.datetime.min
    infinity = datetime.datetime.max

    timedelta = datetime.timedelta
    datetime = datetime.datetime

    def now(self):
        return datetime.datetime.now()

    def next_minute(self):
        return datetime.datetime.now().replace(second=0, microsecond=0) + self.timedelta(seconds=60)

    def sleep(self, seconds):
        """Sleep for a certain amount of seconds. If sleeping is interrupted by
           a signal an Interrupt exception is raised.
        """
        # FIXME what about race conditions?
        stop_time = time.time() + seconds
        time.sleep(seconds)


