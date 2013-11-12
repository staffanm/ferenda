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
except ImportError: # pragma: no cover
    # if on python 2.6
    from ordereddict import OrderedDict

if sys.version_info < (2,7,0): # pragma: no cover
    try:
        import unittest2 as unittest
    except ImportError: # pragma: no cover
        # means unittest2 isn't installed -- which is OK for a non-dev install
        unittest = None
else: 
    import unittest

try:
    from unittest.mock import Mock, MagicMock, patch, call
except ImportError: # pragma: no cover
    try:
        from mock import Mock, MagicMock, patch, call
    except ImportError: # pragma: no cover
        # this means Mock isn't installed -- which is OK for a non-dev install
        Mock = MagicMock = patch = call = None
