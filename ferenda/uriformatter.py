# -*- coding: utf-8 -*-
from __future__ import unicode_literals


class URIFormatter(object):

    """Companion class to :py:class:`ferenda.CitationParser`, that handles
    the work of formatting the dicts or dict-like objects that
    CitationParser creates.

    The class is initialized with a list of formatters, where each
    formatter is a tuple (key, callable). When
    :py:meth:`~ferenda.URIFormatter.format` is passed a citation
    reference in the form of a ``pyparsing.ParseResult`` object (which
    has a ``.getName`` method), the name of that reference is matched
    against the key of all formatters. If there is a match, the
    corresponding callable is called with the parseresult object as a
    single parameter, and the resulting string is returned.

    An initialized ``URIFormatter`` object is not used
    directly. Instead, call
    :py:meth:`ferenda.CitationParser.set_formatter` with the object as
    parameter.  See :doc:`../citationparsing`.

    :param \*formatters: Formatters, each provided as a *(name, callable)* tuple.
    :type \*formatters: list

    """

    def __init__(self, *formatters):
        self._formatters = dict(formatters)

    def format(self, parseresult):
        """Given a pyparsing.ParseResult object, finds a appropriate formatter for that 
        result, and formats the result into a URI using that formatter."""
        formatter = self.formatterfor(parseresult.getName())
        if formatter:
            return formatter(dict(parseresult))
        else:
            return None

    def addformatter(self, key, func):
        """Add a single formatter to the list of registered formatters after initialization."""
        self._formatters[key] = func

    # wrapper around dict.get to allow for future lookup mechanisms. Maybe unneccesary?
    def formatterfor(self, key):
        """Returns an appropriate formatting callable for the given key, or None if not found."""
        return self._formatters.get(key, None)
