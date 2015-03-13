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
except ImportError:  # pragma: no cover
    # if on python 2.6
    from ordereddict import OrderedDict

try:
    from collections import Counter
except ImportError:  # pragma: no cover
    # this is a minimal backport of Counter, for use until we mode to
    # python-future. Does not contain .most_common
    class Counter(dict):
        def __init__(self, iterable=None, **kwds):
            super(Counter, self).__init__()
            self.update(iterable, **kwds)

        def __missing__(self, key):
            return 0

        def update(self, iterable=None, **kwds):
            if iterable is not None:
                if isinstance(iterable, Mapping):
                    if self:
                        self_get = self.get
                        for elem, count in iterable.iteritems():
                            self[elem] = self_get(elem, 0) + count
                    else:
                        super(Counter, self).update(iterable) # fast path when counter is empty
                else:
                    self_get = self.get
                    for elem in iterable:
                        self[elem] = self_get(elem, 0) + 1
            if kwds:
                self.update(kwds)



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
