# -*- coding: utf-8 -*-
from __future__ import unicode_literals


class Feedset(object):

    """Represents a particular set of feeds, structured around some
    ke particular attribute(s) of documents, like title or publication
    date.

    :param label: A description of this set of feeds, like "By publisher"
    :type  label: str
    :param feeds: The set of :py:class:`~ferenda.Feed` objects that makes
                  up this page set.
    :type  feeds: list
    :param predicate: The predicate (if any) that this feedset is
                      keyed on.
    :type  predicate: rdflib.term.URIRef

    """

    def __init__(self, label, feeds, predicate=None):
        self.label = label
        self.feeds = feeds
        self.predicate = predicate

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        dictrepr = "".join((" %s=%s" % (k, v) for k, v in sorted(self.__dict__.items()) if not callable(v)))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))
