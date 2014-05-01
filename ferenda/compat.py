# -*- coding: utf-8 -*-
"""kind of like six.moves but primarily for py26 support.

Client code uses this like::

    from ferenda.compat import OrderedDict
    from ferenda.compat import quote, unquote, urlsplit, urlunsplit
    from ferenda.compat import name2codepoint
    # and for testing
    from ferenda.compat import unittest 
    from ferenda.compat import Mock, patch
"""
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

try:
    from collections import OrderedDict
except ImportError: # pragma: no cover
    # if on python 2.6
    from ordereddict import OrderedDict

try:
    from urllib.parse import quote, unquote, urlsplit, urlunsplit, parse_qsl, urlencode, urljoin
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
    from urllib import unquote, urlencode
    from urlparse import urlsplit, urlunsplit, parse_qsl, urljoin

try:
    from html.entities import name2codepoint
except ImportError:
    from htmlentitydefs import name2codepoint
    
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
