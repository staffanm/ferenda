# Copyright 2013 semantics GmbH
# Written by Marcus Brinkmann <m.brinkmann@semantics.de>

from __future__ import print_function, division
from __future__ import absolute_import, unicode_literals

from lxml import etree

from . mw import mwParser as Parser
from . semantics import mwSemantics as Semantics
from . semantics import SemanticsTracer
from . preprocessor import Preprocessor


class MediaWiki(object):
    """MediaWiki parser.

    Parses the provided MediaWiki-style wikitext and renders it to HTML."""

    def __init__(self, wikitext, title=None):
        """Construct a new MediaWiki object for the given wikitext."""

        wikitext = Preprocessor().expand(title, wikitext)
        parser = Parser(parseinfo=False,  whitespace='', nameguard=False)
        ast = parser.parse(wikitext, "document", filename="wikitext",
                           semantics=Semantics(parser), trace=False,
                           nameguard=False, whitespace='')
        self.ast = ast

    def as_string(self):
        """Return the rendered output as HTML string."""
        return etree.tostring(self.ast)

    def as_tree(self):
        """Return the rendered output as element tree."""
        return self.ast


def mediawiki(wikitext, title=None):
    """Render the wikitext and return output as HTML string."""
    mw = MediaWiki(wikitext, title=title)
    return mw.as_string()
