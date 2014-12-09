# -*- coding: utf-8 -*-
from __future__ import unicode_literals


class Feed(object):

    """Represents a particular Feed of new or updated items selected by
    some criteria.
    
    :param label: A description of this feed, like "Documents published by XYZ"
    :type  label: str
    :param binding: The variable binding used for defining this feed, like
                    "title" or "issued"
    :type  binding: str
    :param value: The particular value of bound variable that corresponds to
                  this TOC page, like "a" or "2013". The ``selector``
                  function of a :py:class:`~ferenda.Facet` object is used
                  to select this value out of the raw data.
    :type  value: str

    """

    def __init__(self, slug, title, binding, value):
        self.slug = slug
        self.title = title
        self.binding = binding
        self.value = value

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __repr__(self):
        dictrepr = "".join((" %s=%s" % (k, v) for k, v in sorted(self.__dict__.items())))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))

    @classmethod
    def all(cls, row, entry):
        return True
