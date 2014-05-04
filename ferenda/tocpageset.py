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


class TocPageset(object):

    """Represents a particular set of TOC pages, structured around some
particular attribute(s) of documents, like title or publication
date. :py:meth:`~ferenda.DocumentRepository.toc_pagesets` returns a
list of these objects, override that method to provide custom
TocPageset objects.

    :param label: A description of this set of TOC pages, like "By publication year"
    :type  label: str
    :param pages: The set of :py:class:`~ferenda.TocPage` objects that makes up this page set.
    :type  pages: list

    """

    def __init__(self, label, pages):
        self.label = label
        self.pages = pages

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        dictrepr = "".join((" %s=%s" % (k, v) for k, v in sorted(self.__dict__.items())))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))
