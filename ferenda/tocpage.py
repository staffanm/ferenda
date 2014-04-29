# -*- coding: utf-8 -*-
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


class TocPage(object):

    """Represents a particular TOC page.
    
    :param linktext: The text used for TOC links *to* this page, like "a" or "2013".
    :param linktext: str
    :param label: A description of this page, like "Documents starting with 'a'"
    :type  label: str
    :param binding: The variable binding used for defining this TOC page, like "title" or "issued"
    :type  binding: str
    :param value: The particular value of bound variable that corresponds to this TOC page, like "a" or "2013". The ``selector`` function of a :py:class:`~ferenda.Facet` object is used to select this value out of the raw data.
    :type  value: str
    """

    def __init__(self, linktext, title, binding, value):
        self.linktext = linktext
        self.title = title
        self.binding = binding
        self.value = value

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        dictrepr = "".join((" %s=%s" % (k, v) for k, v in sorted(self.__dict__.items())))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))
