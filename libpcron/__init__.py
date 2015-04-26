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

__version__ = "0.5-pre1"
__copyright__ = "(C) 2009-2015 Lars Gustäbel <lars@gustaebel.de>"

ENVIRONMENT_NAME = "environment.sh"
CRONTAB_NAME = "crontab.ini"
PID_NAME = "pcron.pid"
SUPPORTED_SHELLS = set(["sh", "bash", "ksh", "zsh", "dash"])

