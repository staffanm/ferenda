# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
from collections import deque, defaultdict

from rdflib import URIRef

from ferenda import DocumentRepository
from ferenda.elements import CompoundElement, OrdinalElement

# More TODO: create test/files/repo/eut/source/all.json like
# {'http://eur-lex.europa.eu/LexUriServ/LexUriServ.do?uri=OJ:C:2008:115:0001:01:EN:HTML': 'treaties.html'}
#
# Create downloaded/tfeu.html and teu.html (same content or possibly a bit shortened)
#
# Create distilled/tfeu.ttl and distilled/teu.ttl, should include structural elements
#
# Create parsed/tfeu.xhtml and parsed/teu.xhtml.
#
# Then done!

# The general outline of a treaty is:
# <Body> C
#   <Paragraph> C (unicode/Link) - starting and ending titles
#   <Preamble> C
#     <Paragraph> - the typographic term, aka "Stycke"
#   <Part> CO - not present for TEU
#     <Title> CO
#       <Chapter> CO
#         <Section> CO
#           <Article> CO
#             <Subarticle> CO
#                <Paragraph> C
#                  <unicode>
#                  <Link>
#                <UnordedList leader="dash"> C
#                  <ListItem> C
#                <OrderedList type="letter"> CO

# or should we have a class method ontology_uri, complimentary to canonical_uri/dataset_uri ?
vocab_uri = "http://lagen.nu/eurlex#"


class PreambleRecital(CompoundElement, OrdinalElement):
    pass

# the most toplevel structural element. Only used for TFEU, not TEU


class Part(CompoundElement, OrdinalElement):
    pass

# nb: this is completely different from ferenda.elements.Title -- this title is a toplevel
# structural element that encompasses chapters, sections, articles etc


class Title(CompoundElement, OrdinalElement):
    pass


class Chapter(CompoundElement, OrdinalElement):
    pass


class Section(CompoundElement, OrdinalElement):
    pass


class Article(CompoundElement, OrdinalElement):
    fragment_label = "A"
    # FIXME: extend CompoundElement.as_xhtml to check for rdf_type and use it as an @about
    # attribute (using a make_graph() graph to qname it)
    rdf_type = URIRef(vocab_uri + "Article")


class Subarticle(CompoundElement, OrdinalElement):
    fragment_label = "P"
    rdf_type = URIRef(vocab_uri + "SubArticle")


class ListItem(CompoundElement):
    fragment_label = "L"
    rdf_type = URIRef(vocab_uri + "ListItem")


class EurlexTreaties(DocumentRepository):

    """Handles the foundation treaties of the European union."""
    # overrides of superclass variables
    alias = "eut"  # European Union Treaties
    start_url = "http://eur-lex.europa.eu/LexUriServ/LexUriServ.do?uri=OJ:C:2008:115:0001:01:EN:HTML"
    document_url_template = "http://eur-lex.europa.eu/LexUriServ/LexUriServ.do?uri=OJ:C:2008:115:0001:01:EN:HTML#%(basefile)s"
    rdf_type = URIRef(vocab_uri + "Treaty")

#
# Downloading

    def download(self, basefile=None):
        # NB: The very same document contains both TEU and TFEU. We download it twice
        # (wasting some storage space) and let parse() pick out the relevant parts.
        self.download_single("teu")
        self.download_single("tfeu")

#
# Parsing -- FIXME: this should be easily ported to FSMParser

    re_part = re.compile("PART (ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN)$").match
    re_title = re.compile("TITLE ([IVX]+)$").match
    re_chapter = re.compile("CHAPTER (\d+)$").match
    re_section = re.compile("SECTION (\d+)$").match
    re_article = re.compile("Article (\d+)$").match
    re_subarticle = re.compile("^(\d+)\. ").search
    re_unorderedliststart = re.compile("^- ").search
    re_orderedliststart = re.compile("^\(\w\) ").search
    re_romanliststart = re.compile("^\([ivx]+\) ").search
    ordinal_list = ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN',
                    'EIGHT', 'NINE', 'TEN', 'ELEVEN', 'TWELVE')
    ordinal_dict = dict(
        list(zip(ordinal_list, list(range(1, len(ordinal_list) + 1)))))

    def parse_metadata_from_soup(self, soup, doc):
        if not doc.uri:
            doc.uri = self.canonical_uri(doc.basefile)
        desc = Describer(doc.meta, doc.uri)
        desc.rdftype(self.rdf_type)
        if basefile == "teu":
            desc.value(self.ns['dct'].title, "Treaty on European Union", lang="en")
        elif basefile == "tfeu":
            desc.value(
                self.ns['dct'].title, "Treaty on the Functioning of the European Union", lang="en")

    def parse_document_from_soup(soup, doc):
        if basefile == "teu":
            startnode = soup.findAll(text="-" * 50)[1].parent
        elif basefile == "tfeu":
            startnode = soup.findAll(text="-" * 50)[2].parent

        lines = deque()
        for p in startnode.findNextSiblings("p"):
            if p.string == "-" * 50:
                self.log.info("found the end")
                break
            else:
                if p.string:
                    lines.append(str(p.string))

        doc.body = self.make_body(lines)
        self.process_body(doc.body, '', doc.uri)

    def make_body(self, lines):
        b = Body()
        while lines:
            line = lines.popleft()
            if line == "PREAMBLE":
                b.append(self.make_preamble(lines))
            elif self.re_title(line):
                lines.appendleft(line)
                b.append(self.make_title(lines))
            elif self.re_part(line):
                lines.appendleft(line)
                b.append(self.make_part(lines))
            else:
                b.append(Paragraph([line]))
            # print type(b[-1])
        return b

    def make_preamble(self, lines):
        p = PreambleRecital(title="PREAMBLE")
        while lines:
            line = lines.popleft()
            if (self.re_part(line) or self.re_title(line)):
                lines.appendleft(line)
                return p
            else:
                p.append(Paragraph([line]))

        self.log.warn("make_preamble ran out of lines!")
        return p

    def make_part(self, lines):
        partnumber = lines.popleft()
        ordinal = self.ordinal_dict[self.re_part(partnumber).group(1)]
        parttitle = lines.popleft()
        p = Part(ordinal=ordinal, ordinaltitle=partnumber, title=parttitle)
        while lines:
            line = lines.popleft()
            if (self.re_part(line)):
                lines.appendleft(line)
                return p
            elif (self.re_title(line)):
                lines.appendleft(line)
                p.append(self.make_title(lines))
            elif (self.re_article(line)):
                # print "make_part: %s matches article" % line
                lines.appendleft(line)
                p.append(self.make_article(lines))
            else:
                p.append(Paragraph([line]))
                self.log.warn(
                    "make_part appended naked Paragraph '%s...'" % line[:25])
        return p

    def make_title(self, lines):
        titlenumber = lines.popleft()
        ordinal = self._from_roman(self.re_title(titlenumber).group(1))
        titletitle = lines.popleft()
        t = Title(ordinal=ordinal, ordinaltitle=titlenumber, title=titletitle)
        while lines:
            line = lines.popleft()
            if (self.re_part(line) or self.re_title(line)):
                lines.appendleft(line)
                return t
            elif (self.re_chapter(line)):
                lines.appendleft(line)
                t.append(self.make_chapter(lines))
            elif (self.re_article(line)):
                # print "make_title: %s matches article" % line
                lines.appendleft(line)
                t.append(self.make_article(lines))
            else:
                t.append(Paragraph([line]))
                self.log.warn(
                    "make_title appended naked Paragraph '%s...'" % line[:25])
        return t

    def make_chapter(self, lines):
        chapternumber = lines.popleft()
        ordinal = int(self.re_chapter(chapternumber).group(1))
        chaptertitle = lines.popleft()
        c = Chapter(
            ordinal=ordinal, ordinaltitle=chapternumber, title=chaptertitle)
        while lines:
            line = lines.popleft()
            if (self.re_part(line) or
                self.re_title(line) or
                    self.re_chapter(line)):
                lines.appendleft(line)
                return c
            elif (self.re_section(line)):
                lines.appendleft(line)
                c.append(self.make_section(lines))
            elif (self.re_article(line)):
                # print "make_chapter: %s matches article" % line
                lines.appendleft(line)
                c.append(self.make_article(lines))
            else:
                c.append(Paragraph([line]))
                self.log.warn("make_chapter appended naked Paragraph '%s...'" %
                              line[:25])
        return c

    def make_section(self, lines):
        sectionnumber = lines.popleft()
        ordinal = int(self.re_section(sectionnumber).group(1))
        sectiontitle = lines.popleft()
        s = Section(
            ordinal=ordinal, ordinaltitle=sectionnumber, title=sectiontitle)
        while lines:
            line = lines.popleft()
            if (self.re_part(line) or
                self.re_title(line) or
                self.re_chapter(line) or
                    self.re_section(line)):
                lines.appendleft(line)
                return s
            elif (self.re_article(line)):
                # print "make_section: %s matches article" % line
                lines.appendleft(line)
                s.append(self.make_article(lines))
            else:
                s.append(Paragraph([line]))
                self.log.warn("make_section appended naked Paragraph '%s...'" %
                              line[:25])
        return s

    def make_article(self, lines):
        articlenumber = lines.popleft()
        ordinal = int(self.re_article(articlenumber).group(1))
        self.log.info("Making article: %s" % ordinal)
        exarticlenumber = lines.popleft()
        if not exarticlenumber.startswith("(ex Article"):
            lines.appendleft(exarticlenumber)
            a = Article(ordinal=ordinal, ordinaltitle=articlenumber)
        else:
            a = Article(ordinal=ordinal, ordinaltitle=articlenumber,
                        exarticlenumber=exarticlenumber)

        while lines:
            line = lines.popleft()
            if (self.re_part(line) or
                self.re_title(line) or
                self.re_chapter(line) or
                self.re_section(line) or
                    self.re_article(line)):
                lines.appendleft(line)
                return a
            elif (self.re_subarticle(line)):
                lines.appendleft(line)
                a.append(self.make_subarticle(lines))
            elif (self.re_unorderedliststart(line)):
                lines.appendleft(line)
                a.append(self.make_unordered_list(lines, "dash"))
            elif (self.re_orderedliststart(line)):
                lines.appendleft(line)
                a.append(self.make_ordered_list(lines, "lower-alpha"))
            else:
                # print "Appending %s" % line[:40]
                a.append(Paragraph([line]))

        return a

    def make_subarticle(self, lines):
        line = lines.popleft()
        subarticlenum = int(self.re_subarticle(line).group(1))
        # self.log.info("Making subarticle %d: %s" % (subarticlenum, line[:30]))
        s = Subarticle(ordinal=subarticlenum)
        lines.appendleft(line)
        while lines:
            line = lines.popleft()
            if (self.re_part(line) or
                self.re_title(line) or
                self.re_chapter(line) or
                self.re_section(line) or
                    self.re_article(line)):
                lines.appendleft(line)
                return s
            elif (self.re_subarticle(line) and
                  int(self.re_subarticle(line).group(1)) != subarticlenum):
                lines.appendleft(line)
                return s
            elif (self.re_unorderedliststart(line)):
                lines.appendleft(line)
                s.append(self.make_unordered_list(lines, "dash"))
            elif (self.re_orderedliststart(line)):
                lines.appendleft(line)
                s.append(self.make_ordered_list(lines, "lower-alpha"))
            else:
                # this is OK
                s.append(Paragraph([line]))
        return s

    def make_unordered_list(self, lines, style):
        ul = UnorderedList(style=style)
        while lines:
            line = lines.popleft()
            if not self.re_unorderedliststart(line):
                lines.appendleft(line)
                return ul
            else:
                ul.append(ListItem([line]))
        return ul

    def make_ordered_list(self, lines, style):
        ol = OrderedList(style=style)
        while lines:
            line = lines.popleft()
            # try romanliststart before orderedliststart -- (i) matches
            # both, but is likely the former
            if self.re_romanliststart(line):
                # print "make_ordered_list: re_romanliststart: %s" % line[:40]
                if style == "lower-roman":
                    ol.append(ListItem([line]))
                else:
                    lines.appendleft(line)
                    ol.append(self.make_ordered_list(lines, "lower-roman"))
            elif self.re_orderedliststart(line):
                # print "make_ordered_list: re_orderedliststart: %s" % line[:40]
                if style == "lower-alpha":
                    ol.append(ListItem([line]))
                else:  # we were in a roman-style sublist, so we should pop up
                    lines.appendleft(line)
                    return ol
            else:
                # print "make_ordered_list: done: %s" % line[:40]
                lines.appendleft(line)
                return ol
        return ol

    # Post-process the document tree in a recursive fashion in order to:
    #
    # Find addressable units (resources that should have unique URI:s,
    # e.g. articles and subarticles) and construct IDs for them, like
    # "A7", "A25(b)(ii)" (or A25S1P2N2 or...?)
    #
    # How should we handle Articles themselves -- they have individual
    # CELEX numbers and therefore URIs (but subarticles don't)?
    def process_body(self, element, prefix, baseuri):
        if isinstance(element, str):
            return
        # print "Starting with "  + str(type(element))
        counters = defaultdict(int)
        for p in element:
            counters[type(p)] += 1
            # print "handling " + str(type(p))
            if hasattr(p, 'fragment_label'):  # this is an addressable resource
                elementtype = p.fragment_label
                if hasattr(p, 'ordinal'):
                    elementordinal = p.ordinal
                else:
                    elementordinal = counters[type(p)]

                fragment = "%s%s%s" % (prefix, elementtype, elementordinal)
                if elementtype == "A":
                    uri = "%s%03d" % (baseuri, elementordinal)
                else:
                    uri = "%s%s%s" % (baseuri, elementtype, elementordinal)

                p.id = fragment
                p.attrs = {'id': p.id,
                           'about': uri,
                           'typeof': p.rdftype}
                if elementtype == "A":
                    uri += "#"
            else:
                fragment = prefix
                uri = baseuri

            self.process_body(p, fragment, uri)
