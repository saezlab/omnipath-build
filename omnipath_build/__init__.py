#!/usr/bin/env python

#
# This file is part of the `omnipath_build` Python module
#
# Copyright 2025
# Heidelberg University Hospital
#
# File author(s): Jonathan Schaul (jonathan.schaul@uni-heidelberg.de)
#
# Distributed under the GPL-3.0-or-later license
# See the file `LICENSE` or read a copy at
# https://www.gnu.org/licenses/gpl-3.0.txt
#

"""A general database builder on top of pypath."""

__all__ = [
    '__version__',
    '__author__',
    'DatabaseLifecycleManager',
    'discover_all_resources',
]

from ._metadata import __author__, __version__
from .database_manager import DatabaseLifecycleManager
from .tools.list_pypath_resources import discover_all_resources
