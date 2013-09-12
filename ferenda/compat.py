#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# ferenda/compat.py -- kind of like six.moves but primarily for py26 support
#
# client code uses this like:
#
# from ferenda.compat import OrderedDict
# from ferenda.compat import unittest 
# from ferenda.compat import Mock, patch
import sys
try:
    from collections import OrderedDict
except ImportError:
    # if on python 2.6
    from ordereddict import OrderedDict

if sys.version_info < (2,7,0):
    import unittest2 as unittest
else:
    import unittest

try:
    from unittest.mock import Mock, patch
except ImportError:
    from mock import Mock, patch
