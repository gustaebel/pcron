#!/usr/bin/env python2
# coding: utf8
# -----------------------------------------------------------------------
#
# pcron - a periodic cron-like job scheduler.
# Copyright (C) 2009-2014 Lars Gustäbel <lars@gustaebel.de>
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
import re

if sys.version_info[:2] < (2, 5):
    raise SystemExit("Python >=2.6 required")

from distutils.core import setup

# Determine version number.
text = open(os.path.join(os.path.dirname(sys.argv[0]), "libpcron/__init__.py")).read()
match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
version = match.group(1)

kwargs = {
    "name":         "pcron",
    "version":      version,
    "author":       "Lars Gustäbel",
    "author_email": "lars@gustaebel.de",
    "url":          "http://www.gustaebel.de/lars/pcron/",
    "description":  "a periodic job scheduler",
    "long_description":
                    "pcron is a periodic job scheduler inspired by fcron",
    "download_url": "http://www.gustaebel.de/lars/pcron/pcron-%s.tar.gz" % version,
    "license":      "GPL",
    "classifiers":  ["Development Status :: 3 - Alpha",
                    "Environment :: Console",
                    "Intended Audience :: System Administrators",
                    "License :: OSI Approved :: GNU General Public License (GPL)",
                    "Natural Language :: English",
                    "Operating System :: Unix",
                    "Programming Language :: Python",
                    "Topic :: Utilities"],
    "packages":     ["libpcron"],
    "scripts":      ["pcrond", "pcron", "pcrontab"]
}

setup(**kwargs)

