# -*- coding: utf-8 -*-
"""kind of like six.moves but primarily for py26 support.

Client code uses this like::

    from ferenda.compat import OrderedDict
    from ferenda.compat import unittest 
    from ferenda.compat import Mock, patch
"""
from __future__ import unicode_literals
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
    from unittest.mock import Mock, patch, call
except ImportError:
    from mock import Mock, patch, call
