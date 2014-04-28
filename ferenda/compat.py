# -*- coding: utf-8 -*-
"""kind of like six.moves but primarily for py26 support.

Client code uses this like::

    from ferenda.compat import OrderedDict
    from ferenda.compat import unittest 
    from ferenda.compat import Mock, patch
    from ferenda.compat import quote
"""
from __future__ import unicode_literals
from six import text_type as str
import sys
try:
    from collections import OrderedDict
except ImportError: # pragma: no cover
    # if on python 2.6
    from ordereddict import OrderedDict

try:
    from urllib.parse import quote
except ImportError:
    # urllib.quote in python 2 cannot handle unicode values for the s
    # parameter (2.6 cannot even handle unicode values for the safe
    # parameter). We therefore redefine quote with a wrapper.
    from urllib import quote as _quote
    def quote(s, safe='/'):
        if isinstance(s, str):
            s = s.encode('utf-8')
        if isinstance(safe, str):
            safe = safe.encode('ascii')
        return _quote(s, safe).decode('ascii')

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
