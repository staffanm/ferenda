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

from rdflib import Graph

class Document(object):

    """A document represents the content of a document together with a
    RDF graph containing metadata about the document. Don't create
    instances of :class:`~ferenda.Document` directly. Create them
    through :meth:`~ferenda.DocumentRepository.make_document` in order
    to properly initialize the ``meta`` property.

    :param meta: A RDF graph containing metadata about the document
    :param body: A list of :mod:`ferenda.elements` based objects representing the content of the document
    :param uri: The canonical URI for this document
    :param lang: The main language of the document as a IETF language tag, i.e. "sv" or "en-GB"
    :param basefile: The basefile of the document
    """

    def __init__(self, meta=None, body=None, uri=None, lang=None, basefile=None):
        if meta is None:
            self.meta = Graph()
        else:
            self.meta = meta
        if body is None:
            self.body = []
        else:
            self.body = body
        self.uri = uri
        self.lang = lang
        self.basefile = basefile

    def __eq__(self, other):
        return self.__dict__ == other.__dict__
