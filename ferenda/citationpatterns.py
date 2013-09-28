# -*- coding: utf-8 -*-
"""General ready-made grammars for use with
:py:class:`~ferenda.CitationParser`. See :doc:`../citationparsing` for
examples.

"""
from __future__ import unicode_literals
from pyparsing import *

#
#
# ferenda.citationpatterns.url
#
# Adapted from http://pyparsing.wikispaces.com/file/view/urlparse_.py/31853197/urlparse_.py
url_scheme = oneOf("http https ftp")
url_netloc = delimitedList(
    Word(printables, excludeChars="/."), ".", combine=True)
# To avoid matching trailing punctuation, eg "(see http://foo.org/)":
url_tokens_not_at_end = Word(",).")
url_path_tokens = Word(printables, excludeChars="?#,).")
url_path = delimitedList(url_path_tokens, url_tokens_not_at_end, combine=True)
url_query_tokens = Word(printables, excludeChars="#,).")
url_query = delimitedList(
    url_query_tokens, url_tokens_not_at_end, combine=True)
url_fragment_tokens = Word(printables, excludeChars=",).")
url_fragment = delimitedList(
    url_fragment_tokens, url_tokens_not_at_end, combine=True)
url = (url_scheme.setResultsName("scheme") + Literal("://").suppress() +
       url_netloc.setResultsName("netloc") +
       Optional(url_path).setResultsName("path") +
       Optional(Literal("?").suppress() + url_query).setResultsName("query") +
       Optional(Literal("#").suppress() + url_fragment).setResultsName("fragment")).setResultsName("url")
"""Matchs any URL like 'http://example.com/ or
'https://example.org/?key=value#fragment' (note: only the
schemes/protocols 'http', 'https' and 'ftp' are supported)"""

#
#
# ferenda.citationpatterns.eulaw
#
LongYear = Word(nums, exact=4)
ShortYear = Word(nums, exact=2)
Month = oneOf(
    "januari februari mars april maj juni juli augusti september oktober november december")
DayInMonth = Word(nums, max=2)
Date = (DayInMonth + Month + LongYear)

Ordinal = Word(nums).setResultsName("Ordinal")
Year = (ShortYear | LongYear).setResultsName("Year")
Association = oneOf("EG EEG").setResultsName("Association")

Institution = Literal('rådets') | Literal(
    'Europaparlamentets och rådets') | Literal('kommissionens')

LegalactType = oneOf("direktiv förordning").setResultsName("LegalactType")
Directive = Group(
    Year + "/" + Ordinal + "/" + Association).setResultsName("Directive")
Regulation = Group("(" + Association + ")" + "nr" + Ordinal + "/" + Year)

# "artikel 42.1" => Article: 42, Subarticle: 1
Article = "artikel" + Word(nums).setResultsName(
    "ArticleID") + Optional("." + Word(nums).setResultsName("SubarticleID"))
Legalact = Institution + LegalactType + (Directive | Regulation) + \
    "av den" + Date
ArticleLegalact = Article + "i" + Legalact

eulaw = MatchFirst(
    [ArticleLegalact, Legalact, Article]).setResultsName("EULegislation")
"""Matches EU Legislation references like 'direktiv 2007/42/EU'."""
