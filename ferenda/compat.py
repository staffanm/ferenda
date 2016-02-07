# -*- coding: utf-8 -*-
"""kind of like six.moves but primarily for py26 support.

Client code uses this like::


    from ferenda.compat import unittest
    from ferenda.compat import Mock, patch
"""
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import sys

# Backports of OrderedDict and Counter is now provided by
# future. Caveat: all modules that wants to import them from
# collections must do this in order to work with py26
#
#    from future import standard_library
#    standard_library.install_aliases()
# 
# try:
#     from collections import OrderedDict
# except ImportError:  # pragma: no cover
#     # if on python 2.6
#     from ordereddict import OrderedDict
# 
# try:
#     from collections import Counter
# except ImportError:  # pragma: no cover
#     # this is a minimal backport of Counter, for use until we mode to
#     # python-future. Does not contain .most_common
#     class Counter(dict):
#         def __init__(self, iterable=None, **kwds):
#             super(Counter, self).__init__()
#             self.update(iterable, **kwds)
# 
#         def __missing__(self, key):
#             return 0
# 
#         def update(self, iterable=None, **kwds):
#             if iterable is not None:
#                 if isinstance(iterable, Mapping):
#                     if self:
#                         self_get = self.get
#                         for elem, count in iterable.iteritems():
#                             self[elem] = self_get(elem, 0) + count
#                     else:
#                         super(Counter, self).update(iterable) # fast path when counter is empty
#                 else:
#                     self_get = self.get
#                     for elem in iterable:
#                         self[elem] = self_get(elem, 0) + 1
#             if kwds:
#                 self.update(kwds)
# 
# 

if sys.version_info < (2, 7, 0):  # pragma: no cover
    try:
        import unittest2 as unittest
    except ImportError:  # pragma: no cover
        # means unittest2 isn't installed -- which is OK for a non-dev install
        unittest = None
else:
    import unittest

try:
    from unittest.mock import Mock, MagicMock, patch, call
except ImportError:  # pragma: no cover
    try:
        from mock import Mock, MagicMock, patch, call
    except ImportError:  # pragma: no cover
        # this means Mock isn't installed -- which is OK for a non-dev install
        Mock = MagicMock = patch = call = None

from urllib.parse import urljoin as urljoin_orig

def urljoin(root, other):
    # on py26, it seems that BeautifulSoup Tag objects sometimes can
    # return a bytestr instead of unicode (eg img["src"] =>
    # 'hello.jpg', not u'hello.jpg'. This means that it cannot be used
    # as input to urljoin, which requires that both args have the same
    # type. So we wrap it in order to have simple (albeit somewhat
    # misleading) example code.
    if isinstance(other, bytes):
        other = other.decode("utf-8")
    return urljoin_orig (root, other)
