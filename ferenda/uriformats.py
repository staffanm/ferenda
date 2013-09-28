# -*- coding: utf-8 -*-
"""A small set of generic functions to convert (dicts or dict-like
objects) to URIs. They are usually matched with a corresponding
citationpattern like the ones found in
:py:mod:`ferenda.citationpatterns`. See :doc:`../citationparsing` for
examples.

"""
from __future__ import unicode_literals
from six.moves.urllib_parse import quote


def generic(d):
    """Converts any dict into a URL. The domain (netlog) is always
example.org, and all keys/values of the dict is turned into a
querystring.

    >>> generic({'foo':'1', 'bar':'2'})
    "http://example.org/?foo=1&bar=2"

    """

    querystring = "&".join([quote(k) + "=" + quote(v) for (k, v) in d.items()])
    return "http://example.org/?%s" % querystring


def url(d):
    """Converts a dict with keys ``scheme``, ``netloc``, ``path`` (and
optionally query and/or fragment) into the corresponding URL.

    >>> url({'scheme':'https', 'netloc':'example.org', 'path':'test'})
    "https://example.org/test

    """

    if ('fragment' not in d and 'query' not in d):
        return "%(scheme)s://%(netloc)s%(path)s" % d
    elif 'fragment' not in d:
        return "%(scheme)s://%(netloc)s%(path)s?%(query)s" % d
    elif 'query' not in d:
        return "%(scheme)s://%(netloc)s%(path)s#%(fragment)s" % d
    else:
        return "%(scheme)s://%(netloc)s%(path)s?%(query)s#%(fragment)s" % d


def eulaw(d):
    """Converts a dict with keys like LegalactType, Directive, ArticleId
(produced by :py:data:`ferenda.citationpatterns.eulaw`) into a
CELEX-based URI.

    .. note::

       This is not yet implemented.

    """

    raise NotImplementedError()
