# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# stdlib
import os
import re
import json
import difflib
import logging
import collections
from math import sqrt, pi, e, floor
# 3rd party
from layeredconfig import LayeredConfig, Defaults
from rdflib import URIRef, RDF, Namespace, Literal, Graph, BNode
from rdflib.namespace import DCTERMS
import six
from bs4 import BeautifulSoup
from cached_property import cached_property

# own
from ferenda import util, errors
from ferenda import PDFReader, FSMParser, Describer, Facet
from ferenda.elements import (Link, Body, CompoundElement,
                              Preformatted, UnorderedList, ListItem, serialize)
from ferenda.elements.html import P
from ferenda.pdfreader import BaseTextDecoder, Page, Textbox
from ferenda.decorators import newstate
from ferenda.errors import ParseError, DocumentSkippedError, FSMStateError

from . import SwedishLegalSource, RPUBL
from .legalref import Link, LegalRef, RefParseError
from .elements import *

class Offtryck(SwedishLegalSource):
    """This is a common mixin-type class for all document types in
    "Offentliga trycket" (kommittedirektiv, kommittébetänkanden and
    propositioner), regardless of source or physical file format. It
    should be used in multiple inheritance together with a repo-type
    class.

    """

    DS = "ds"
    FORORDNINGSMOTIV = "fm"
    KOMMITTEDIREKTIV = "dir"
    PROPOSITION = "prop"
    SKRIVELSE = "skr"
    SOU = "sou"
    SO = "so"
    LAGRADSREMISS = "lr" # no established abbreviation
    
    storage_policy = "dir"
    alias = "offtryck"

    parse_types = LegalRef.RATTSFALL, LegalRef.FORARBETEN, LegalRef.ENKLALAGRUM, LegalRef.KORTLAGRUM
    xslt_template = "xsl/forarbete.xsl"
    sparql_annotations = "sparql/describe-with-subdocs.rq"
    sparql_expect_results = False
    # Correct some invalid identifiers spotted in the wild:
    # 1999/20 -> 1999/2000
    # 2000/2001 -> 2000/01
    # 1999/98 -> 1999/2000
    # 2007/20:08123 -> 2007/08:123
    def sanitize_basefile(self, basefile):
        if self.document_type == self.PROPOSITION:
            (y1, y2, idx) = re.split("[:/]", basefile)
            assert len(
                y1) == 4, "Basefile %s is invalid beyond sanitization" % basefile
            assert idx.isdigit(), "Basefile %s has a non-numeric ordinal" % basefile
            idx = int(idx) # remove any leading zeroes
            if y1 == "1999" and y2 != "2000":
                sanitized = "1999/2000:%s" % idx
                self.log.warning("Basefile given as %s, correcting to %s" %
                                 (basefile, sanitized))
            elif (y1 != "1999" and
                  (len(y2) != 2 or  # eg "2000/001"
                   int(y1[2:]) + 1 != int(y2))):  # eg "1999/98
                sanitized = "%s/%02d:%s" % (y1, int(y1[2:]) + 1, idx)
                self.log.warning("Basefile given as %s, correcting to %s" %
                                 (basefile, sanitized))
            else:
                sanitized = basefile
        else:  # KOMMITTEDIREKTIV, SOU, DS
            y, idx = basefile.split(":")
            idx = int(idx)
            assert len(y) == 4, "Basefile %s is invalid beyond sanitization" % basefile
            assert len(str(idx)) < 4, "Basefile %s is invalid beyond sanitization" % basefile
            assert 1900 < int(y) < 2100, "Basefile %s has improbable year %s" % (basefile, y)
            sanitized = "%s:%s" % (y, idx)
        return sanitized

    @property
    def urispace_segment(self):
        return {self.PROPOSITION: "prop",
                self.DS: "ds",
                self.SOU: "sou",
                self.KOMMITTEDIREKTIV: "dir"}.get(self.document_type)


    def metadata_from_basefile(self, basefile):
        a = super(Offtryck, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = basefile.split(":", 1)
        return a

    def sanitize_body(self, rawbody):
        sanitized = super(Offtryck, self).sanitize_body(rawbody)
        if isinstance(sanitized, PDFReader):
            sanitized.analyzer = self.get_pdf_analyzer(sanitized)
        elif isinstance(sanitized, (list, BeautifulSoup)):
            pass
        else:
            raise ParseError("can't sanitize object of type %s" % type(rawbody))
        return sanitized

    # This is a fallback "parser" used when we can't get access to the
    # actual document text and instead conjure up a placeholder in the
    # form of a list of strings. This converts that to a Body object
    def textparser(self, chunks):
        b = Body()
        for p in chunks:
            if not p.strip():
                continue
            b.append(Preformatted([p.replace("\n"," ")]))
        return b
    

    def parse_body_parseconfigs(self):
        return ("default", "noappendix", "simple")
        

    def get_parser(self, basefile, sanitized, initialstate=None,
                   startpage=None, pagecount=None, parseconfig="default"):
        """should return a function that gets any iterable (the output
        from tokenize) and returns a ferenda.elements.Body object.
        
        The default implementation calls :py:func:`offtryck_parser` to
        create a function/closure which is returned IF the sanitized
        body data is a PDFReader object. Otherwise, returns a function that
        justs packs every item in a recieved iterable into a Body object.
        
        If your docrepo requires a FSMParser-created parser, you should
        instantiate and return it here.
        """
        # FIXME: we should return textparser iff parseconfig=="textparser"
        if not isinstance(sanitized, PDFReader):
            return self.textparser

        # most of this method is just calculating metrics and enabling
        # plot/debuganalysis
        if startpage is None:
            if self.document_type == self.PROPOSITION:
                # the first page of a prop has a document-unique title
                # font, larger than h1. To avoid counting that as h1, and
                # subsequently counting h1 as h2, etc, we skip the first
                # page.
                startpage = 1
            else:
                startpage = 0
        if pagecount is None:
            pagecount = len(sanitized)

        if hasattr(sanitized, 'analyzer'):
            analyzer = sanitized.analyzer
        else:
            analyzer = self.get_pdf_analyzer(sanitized)
        # This should be done in get_pdf_analyzer
        # if "hocr" in sanitized.filename:
        #     analyzer.scanned_source = True
        metrics_path = self.store.path(basefile, 'intermediate',
                                       '.metrics.json')
        if os.environ.get("FERENDA_PLOTANALYSIS"):
            plot_path = self.store.path(basefile, 'intermediate',
                                        '.plot.png')
        else:
            plot_path = None
        self.log.debug("%s: Calculating PDF metrics for %s pages "
                       "starting at %s" % (basefile, pagecount,
                                           startpage))
        metrics = analyzer.metrics(metrics_path, plot_path,
                                   startpage=startpage,
                                   pagecount=pagecount,
                                   force=self.config.force)
        if os.environ.get("FERENDA_DEBUGANALYSIS"):
            pdfdebug_path = self.store.path(basefile, 'intermediate',
                                            '.debug.pdf')

            self.log.debug("Creating debug version of PDF")
            gluefunc = self.get_gluefunc(basefile, analyzer)
            analyzer.drawboxes(pdfdebug_path, gluefunc,
                               metrics=metrics)

        if self.document_type == self.PROPOSITION:
            preset = 'proposition'
        elif self.document_type == self.SOU:
            preset = 'sou'
        elif self.document_type == self.DS:
            preset = 'ds'
        elif self.document_type == self.KOMMITTEDIREKTIV:
            preset = 'dir'
        else:
            preset = 'default'
        parser = offtryck_parser(basefile, metrics=metrics, preset=preset,
                                 identifier=self.infer_identifier(basefile),
                                 debug=os.environ.get('FERENDA_FSMDEBUG', 0),
                                 initialstate=initialstate, parseconfig=parseconfig)
        return parser.parse


    def get_gluefunc(self, basefile, analyzer):
        scanned_source = analyzer.scanned_source

        def unreliable_familymatch(prevbox, nextbox):
            def normalize_family(fontfamily):
                return re.sub("[\-,](Italic|Bold|BoldItalic)", "", fontfamily)
            
            # older native (non-scanned) pdfs from regeringen.se
            # contains very unreliable font information sometimes
            if getattr(nextbox[0], 'skippedempty', None):
                # usually within a textbox that contains some initial
                # empty italic or bold textelement. PDFReader filtes
                # out such empty elements, but tells us that we did
                # through the skippedempty attribute
                return True
            elif (len(prevbox) > 1 and prevbox[0].tag == "b" and
                  re.match("\d+(| \w) §", prevbox[0]) and
                  not nextbox[0][0].isupper()):
                # looks like the start of a paragraph? See if nextbox
                # looks like the continuation of a sentence (ie not
                # starting with a capital letter)
                return True
            elif (normalize_family(prevbox.font.family) ==
                  normalize_family(nextbox.font.family) and
                  not nextbox[0][0].isupper()):
                return True
            else:
                return prevbox.font.family in ("Symbol", nextbox.font.family)
        
        def offtryck_gluefunc(textbox, nextbox, prevbox):
            linespacing = nextbox.font.size / 1.2 # bboxes for scanned
                                                  # material seem very tight,
                                                  # so that lines appear to
                                                  # have greater linespacing
            parindent = nextbox.font.size
            if textbox.lines < 1:
                textbox.lines = 1
                textbox.lineheight = textbox.height
            # if we're using hOCR data, take advantage of the paragraph
            # segmentation that tesseract does through the p.ocr_par mechanism
            if (hasattr(prevbox, 'parid') and hasattr(nextbox, 'parid') and
                prevbox.parid == nextbox.parid):
                textbox.lines += 1
                textbox.lineheight = ((textbox.lines - 1 * textbox.lineheight) + nextbox.height) / textbox.lines
                return True
            strtextbox = str(textbox).strip()
            strprevbox = str(prevbox).strip()
            strnextbox = str(nextbox).strip()
            if scanned_source:
                # allow for slight change in fontsize and vert
                # align. Allow for more change if nextbox is a single
                # char, as font size calculation is highly unreliable
                # in these cases
                if len(strnextbox) == 1:
                    sizematch = lambda p, n: abs(p.font.size - n.font.size) <= 4
                else:
                    sizematch = lambda p, n: abs(p.font.size - n.font.size) <= 1
                alignmatch = lambda p, n: abs(p.left - n.left) <= 2
                valignmatch = lambda p, n: abs(p.bottom - n.bottom) <= 3 or abs(p.top - n.top) <= 3
            else:
                sizematch = lambda p, n: p.font.size == n.font.size
                # allow for slight variations in vert align since
                # left margin in practice is not always straight.
                alignmatch = lambda p, n: abs(p.left - n.left) <= 2  
                valignmatch = lambda p, n: p.bottom == n.bottom

            if strnextbox == "–" or strprevbox == "–":
                # dir 2016:15 page 15: a textbox with a single hyphen
                # uses different fontsize
                sizematch = lambda p, n: True
            # A bullet (or dash) always signals the start of a new chunk
            if strnextbox.startswith(("\u2022", "\uf0b7", "−")):
                return False

            if scanned_source:
                familymatch = lambda p, n: p.font.family == n.font.family
            else:
                familymatch = unreliable_familymatch
            
            # allow for more if prevbox starts with a bullet and
            # nextbox startswith lowercase, allow for a large indent
            # (but not hanging indent)
            ul = False
            if strtextbox.startswith(("\u2022", "\uf0b7", "−")):
                ul = True
                if strnextbox[0].islower():
                    alignmatch = lambda p, n: n.left - p.left < 30
            if strtextbox.startswith("\uf0b7"):
                # U+F0B7 is Private use -- probably using symbol font
                # for bullet. Just accept any font family or size change
                sizematch = lambda p, n: True
                # also acccept a slight mismatch in vertical align because of reasons
                valignmatch = lambda p, n: abs(p.bottom - n.bottom) <= 1
                
            # numbered section headings can have large space between
            # the leading number and the rest of the heading, and the
            # top/bottom of the leading number box might differ from
            # the heading with one or a few points. These special
            # conditions helps glue these parts *vertically* by
            # checking that the vertical space is not unreasonable and
            # that horizontal alignment is at least 50 % overlapping
            if nextbox.font.size > 13: # might be a heading -- but we have no
                                      # real way of guessing this at this
                                      # stage (metrics are not available to
                                      # this function)
                if (sizematch(textbox, nextbox) and
                    familymatch(textbox, nextbox) and 
                    nextbox.top < prevbox.top + (prevbox.height / 2) < nextbox.bottom and
                    textbox.left - (prevbox.right) < (prevbox.width * 3)):
                    # don't add to textbox.lines as we haven't
                    # switched lines
                    return True

            # Any line that ONLY contains a section reference should
            # probably be interpreted as a header. (also handle dual
            # refs "4 kap. 9 c och 10 §§", prop 1997/98:44 s 148)
            sectionref = re.compile("(\d+ kap. |)\d+( \w och \d+| \w| och \d+|) §§?$")
            if ((sectionref.match(strprevbox) or sectionref.match(strnextbox))
                and prevbox.bottom <= nextbox.top):
                return False

            # these text locutions indicate a new paragraph (normally, this is
            # also catched by the conditions below, but if prevbox is unusally
            # short (one line) they might not catch it.:
            if re.match("Skälen för (min bedömning|mitt förslag): ", strnextbox):
                return False
            if re.match("\d\. +", strnextbox):  # item in ordered list
                return False
            if re.match("[a-z]\) +", strnextbox):  # item in alphabetized ordered list
                return False
            if (re.match("\d+ §", strnextbox) and
                 (strprevbox[-1] not in ("–", "-") and # make sure this isn't really a continuation
                  not strprevbox.endswith(("och", "enligt", "kap.", "lagens", "före", "i"))) and
                (nextbox.top - prevbox.bottom >= (prevbox.font.size * 0.3))):  # new section (with a suitable linespacing (30% of a line))
                return False
            if nextbox[0].tag == "i" and nextbox[0].startswith("dels"):
                # A common form of itemized list (without bullet or
                # dash indicators) in act preambles
                return False
            # These final conditions glue primarily *horizontally*
            if (sizematch(textbox, nextbox) and
                familymatch(textbox, nextbox) and
                textbox.top + textbox.height + linespacing > nextbox.top and
                (prevbox.left < nextbox.right or
                 textbox.left < parindent * 2 + nextbox.left) and
                (valignmatch(prevbox, nextbox) or  # compare baseline, not topline
                 alignmatch(prevbox, nextbox) or # compare previous line to next
                 alignmatch(textbox, nextbox) or # compare entire glued box so far to next FIXME -- is this a good idea? Tends to glue rows in tables...
                 (parindent * 2 >= (prevbox.left - nextbox.left) >= parindent / 2) or
                 (not ul and (parindent * 2 >= (textbox.left - nextbox.left) >= parindent / 2)) or  # Too permitting when processing unordered lists
                 (re.match(r"[\d\.]+\s+[A-ZÅÄÖ]", strtextbox) and nextbox.left - textbox.left < parindent * 5) # hanging indent (numbered) heading -- FIXME: we'd like to increase the parindent multiplier depending on the len of the initial number
                 )):
                # if the two boxes are on the same line, but have a
                # wide space between them, the nextbox is probably a
                # pagenumber
                if (valignmatch(prevbox, nextbox) and
                    (nextbox.left - textbox.right) > 50 and
                    len(strnextbox) < 10):
                    return False
                textbox.lines += 1
                # update the running average lineheight
                textbox.lineheight = (((textbox.lines - 1) * textbox.lineheight) + nextbox.height) / textbox.lines
                return True

        return offtryck_gluefunc


    def parse_body(self, fp, basefile):
        # this version of parse_body knows how to:
        #
        # - use an appropriate analyzer to segment documents into
        #   subdocs and use the appropritate parsing method on each
        #   subdoc. NOTE: this requires that sanitize_body has set up
        #   a PDFAnalyzer subclass instance as a property on the
        #   sanitized object (normally a PDFReader or
        #   StreamingPDFReader)
        # - handle the case when a document is not available as a PDF,
        #   only in simple HTML/plaintext, and use a simpler parser

        # FIXME: Both the "simple" case and the "plaintext" case below
        # should be folded into the parse_body_parseconfigs()
        # mechanism (and maybe options.py should influence which
        # parserconfig(s) are tried.
        rawbody = self.extract_body(fp, basefile)
        sanitized = self.sanitize_body(rawbody)
        if not hasattr(sanitized, 'analyzer') or isinstance(sanitized, BeautifulSoup):
            # fall back into the same logic as
            # SwedishLegalSource.parse_body at this point
            parser = self.get_parser(basefile, sanitized)
            tokenstream = self.tokenize(sanitized)
            body = parser(tokenstream)
            for func, initialstate in self.visitor_functions(basefile):
                self.visit_node(body, func, initialstate)
            if self.config.parserefs and self.parse_types:
                body = self.refparser.parse_recursive(body)
            return body
        lastexception = None
        physicalmap = [(page.src, page.number) for page in sanitized]
        for parseconfig in self.parse_body_parseconfigs():
            try:
                allbody = Body()
                initialstate = {'pageno': 1}
                serialized = False
                gluefunc = self.get_gluefunc(basefile, sanitized.analyzer)
                # FIXME: temporary non-API workaround -- need to figure out
                # what PDFAnalyzer.documents really need in terms of
                # doc-specific magic in order to reliably separate document
                # parts
                sanitized.analyzer.gluefunc = gluefunc
                documents = sanitized.analyzer.documents

                if len(documents) > 1:
                    self.log.debug("%s: segmented into docs %s" % (basefile, documents))
                self.paginate(sanitized, physicalmap, basefile, parseconfig)
                for (startpage, pagecount, tag) in documents:
                    if tag == 'main':
                        initialstate['pageno'] -= 1  # argh....
                        parser = self.get_parser(basefile, sanitized, initialstate,
                                                 startpage, pagecount,
                                                 parseconfig=parseconfig)
                        tokenstream = sanitized.textboxes(gluefunc,
                                                          pageobjects=True,
                                                          startpage=startpage,
                                                          pagecount=pagecount)
                        body = parser(tokenstream)
                        for func, initialstate in self.visitor_functions(basefile):
                            # could be functions for assigning URIs to particular
                            # nodes, extracting keywords from text etc. Note: finding
                            # references in text with LegalRef is done afterwards
                            self.visit_node(body, func, initialstate)
                        # For documents with more than one subdocument, only
                        # serialize the first (presumably most important) part
                        if not serialized:
                            self._serialize_unparsed(body, basefile)
                            serialized = True

                        # print("%s: self.config.parserefs: %s, self.parse_types: %s" %
                        #       (basefile, self.config.parserefs, self.parse_types))
                        if self.config.parserefs and self.parse_types:
                            # FIXME: There should be a cleaner way of telling
                            # refparser the base uri (or similar) for the
                            # document
                            if self.document_type == self.PROPOSITION:
                                self.refparser._currentattribs = {
                                    "type": RPUBL.Proposition,
                                    "year": basefile.split(":")[0],
                                    "no": basefile.split(":")[1]
                                }
                                if 'kommittensbetankande' in initialstate:
                                    self.refparser._legalrefparser.kommittensbetankande = initialstate['kommittensbetankande']
                                else:
                                    self.refparser._legalrefparser.kommittensbetankande = None
                            if hasattr(self, 'sfsparser'):
                                # the parsing of section titles by
                                # find_commentary might have picked up some
                                # IDs for some named laws. Reuse these when
                                # parsing the bulk of the text.
                                self.refparser._legalrefparser.currentlynamedlaws.update(self.sfsparser.currentlynamedlaws)
                            # FIXME: This fails on py2
                            hits_before = self.refparser._legalrefparser.tuple_to_uri.cache_info().hits
                            body = self.refparser.parse_recursive(body)
                            seen = self.refparser.seen_strings
                            proc = self.refparser.parsed_strings
                            refs = self.refparser.found_refs
                            hits = self.refparser._legalrefparser.tuple_to_uri.cache_info().hits - hits_before
                            if refs:
                                avoided = (hits/refs)
                            else:
                                avoided = 1
                            if seen:
                                processed_percent = (proc / seen) * 100
                            else:
                                processed_percent = 0
                            self.log.debug("refparser: Seen %s, processed %s (%.3f %%) - "
                                           "found %s refs. %s coin calls (%.3f %%) were avoided)" %
                                           (seen, proc, processed_percent, refs,
                                            hits, avoided * 100))
                            self.refparser.reset()
                    elif tag in ('frontmatter', 'endregister'):
                        # Frontmatter and endregister is defined as pages with
                        # no meaningful content (cover page, edition notice,
                        # half title and other crap) -- we can just skip them
                        body = []
                    else:
                        # copy pages verbatim -- make no attempt to glue
                        # textelements together, parse references etc. In
                        # effect, this will yield pages full of absolute
                        # positioned textboxes that don't reflow etc
                        s = VerbatimSection()
                        for relidx, page in enumerate(sanitized[startpage:startpage+pagecount]):
                            sb = Sidbrytning(ordinal=util.increment(initialstate['pageno'],
                                                                    relidx),
                                             width=page.width,
                                             height=page.height,
                                             src=page.src)
                            s.append(sb)
                            s.append(page)
                        body = Body([s])
                    # regardless of wether we used real parsing or verbatim
                    # copying, we need to update the current page number
                    lastpagebreak = self._find_subnode(body, Sidbrytning)
                    if lastpagebreak is None:
                        initialstate['pageno'] = 1
                    else:
                        initialstate['pageno'] = util.increment(lastpagebreak.ordinal, 1)
                    allbody += body[:]
                self.validate_body(allbody, basefile)  # Throws exception if invalid
                return allbody
            except Exception as e:
                if type(e).__name__ in ("BdbQuit",):
                    raise
                errmsg = str(e)
                loc = util.location_exception(e)
                self.log.warning("%s: Parsing with config '%s' failed: %s (%s)" %
                                 (basefile, parseconfig, errmsg, loc))
                lastexception = e
                # "reset" the sanitized body since the parsing process might have mutated it
                if fp.closed:  # pdfreader.parse closes the fp given to it, we'll have to re-open it
                    fp = self.parse_open(basefile)
                else:
                    fp.seek(0)
                rawbody = self.extract_body(fp, basefile)
                sanitized = self.sanitize_body(rawbody)
        else:
            raise lastexception


    def validate_body(self, body, basefile):
        # add an extra test to check for empty forfattningskommentarer
        super(Offtryck, self).validate_body(body, basefile)
        def validate_forfattningskommentar(node):
            if isinstance(node, Forfattningskommentar) and len(node) == 0:
                raise errors.InvalidTree("%s: Kommentar for %s has no content" %
                                         (basefile, getattr(node, 'comment_on', '(Unknown)')))
            else:
                for thing in node:
                    if (isinstance(thing, collections.Iterable) and
                        not isinstance(thing, six.string_types)):
                        validate_forfattningskommentar(thing)
        validate_forfattningskommentar(body)
                                
    def paginate(self, sanitized, physicalmap, basefile, parseconfig):
        """Use a PDF analyzer to determine probable pagenumbering, then set
        the page numbers of the PDFReader object according to that

        """
        # NOTE: this mutates the passed-in `sanitized` document.
        baseuri = self.canonical_uri(basefile)
        pagemapping_path = self.store.path(basefile, 'intermediate',
                                           '.pagemapping.json')
        if parseconfig == "simple":
            # redo the pagination to use physical page numbers. maybe
            # fix the json file at pagemapping_path as well?
            for idx, page in enumerate(sanitized):
                sanitized[idx].number = idx + 1
                sanitized[idx].src = "%s/sid%s.png" % (baseuri, idx+1)
        else:
            analyzer = sanitized.analyzer
            if not os.path.exists(pagemapping_path) or self.config.force:
                # But in order to find out font sizes, we do a minimal
                # metrics calculation to find out probable foootnoteref
                # size. Page numbers must be larger than those.
                styles = analyzer.analyze_styles(analyzer.count_styles(0, len(sanitized)))
                if 'footnoteref' in styles:
                    analyzer.pagination_min_size = styles['footnoteref']['size'] + 1

            pagemapping = analyzer.paginate(paginatepath=pagemapping_path,
                                            force=self.config.force)
            # apply the pagenumbers to the pdf object in a 2-step process
            # 1. first map the PDFReader pages to the physical pages in ine
            #    or more pdf files
            filemapping = {}
            for idx, pagetuple in enumerate(physicalmap):
                pagesrc, pagenumber = pagetuple
                filemapping[(pagesrc.split("/")[-1], str(pagenumber))] = idx
            # 2. then assign the analyzed pagenumbers
            for k, v in pagemapping.items():
                pdffile, pp = k.split("#page=")
                idx = filemapping.get((pdffile, pp))
                if idx is not None:
                    sanitized[idx].number = v
                    sanitized[idx].src = "%s/sid%s.png" % (baseuri, v)
        

    def postprocess_doc(self, doc):
        # loop through the textboxes on page 1 try to find
        # dcterms:identifier, dcterms:title and dcterms:issued (then
        # compare them to what's present in doc.meta, ie data that has
        # been picked up from index.html or some other non-PDF source.
        def _check_differing(describer, predicate, newval):
            try:
                describer.getvalue(predicate)
                if describer.getvalue(predicate) != newval:
                    self.log.debug("%s: HTML page: %s is %r, document: it's %r" %
                                   (doc.basefile,
                                    doc.meta.qname(predicate),
                                    describer.getvalue(predicate),
                                    newval))
                    # remove old val
                    d.graph.remove((d._current(),
                                    predicate,
                                    d.graph.value(d._current(), predicate)))
            except KeyError:
                # old val didn't exist
                pass
            d.value(predicate, newval)

        # WHAT does this even DO?!
        def helper(node, meta):
            for subnode in list(node):
                if isinstance(subnode, Textbox):
                    pass
                elif isinstance(subnode, list):
                    helper(subnode, meta)
        helper(doc.body, doc.meta)


        if not doc.meta.value(URIRef(doc.uri), RPUBL.departement):
            # We have no information about which departement is
            # responsible in the metadata -- try to find it from the
            # doc itself. This is done differently depending on doctype
            if self.rdf_type == RPUBL.Kommittedirektiv:
                candidate = str(doc.body[-1][-1]).strip()
                if candidate.endswith("departementet)"):
                    dep = candidate[1:-1]  # remove enclosing paren
                    doc.meta.add((URIRef(doc.uri), RPUBL.departement, self.lookup_resource(dep)))
                else:
                    self.log.warning("%s: No ansvarig departement found in either metadata or doc" % doc.basefile)
            elif self.rdf_type == RPUBL.Utredningsbetankande:
                # look for the first PreambleSection and determine if the title endswith "departementet"  (gonna be rare)
                pass
        
        # the following postprocessing code is so far only written for
        # Propositioner
        if self.rdf_type != RPUBL.Proposition:
            return doc.body
        if len(doc.body) == 0:
            self.log.warning("%s: doc.body is empty" % doc.basefile)
            return doc.body

        # move the first pagebreak into the first pseudo-section
        if (isinstance(doc.body[0], Sidbrytning) and
            isinstance(doc.body[1], FrontmatterSection)):
            doc.body[1].insert(0, doc.body.pop(0))

        # d = Describer(self._resource.graph, self._resource.identifier)
        d = Describer(doc.meta, doc.uri)
        title_found = identifier_found = issued_found = False
        # look only in frontmatter
        if isinstance(doc.body[0], FrontmatterSection):
            frontmatter = doc.body[0]
        else:
            # Maybe warn here?
            frontmatter = []
        for idx, element in enumerate(frontmatter):
            if not isinstance(element, Textbox):
                continue
            str_element = str(element).strip()

            # dcterms:identifier
            if not identifier_found and hasattr(self, 're_basefile_lax'):
                m = self.re_basefile_lax.search(str_element)
                if m:
                    try:
                        # even identifiers inside of the document can be irregular
                        new_identifier = "Prop. " + m.group(1)
                        identifier = self.sanitize_identifier(new_identifier)
                        _check_differing(d, DCTERMS.identifier, identifier)
                        identifier_found = True
                    except ValueError:
                        self.log.warning("%s: Irregular identifier %s in document" %
                                         (doc.basefile, m.group(1)))

            # dcterms:title FIXME: The fontsize comparison should be
            # done with respect to the resulting metrics (which we
            # don't have a reference to here, since they were
            # calculated in parse_pdf....)
            if not title_found and isinstance(element, PropRubrik):
                # sometimes part of the the dcterms:identifier (eg " Prop."
                # or " 2013/14:51") gets mixed up in the title
                # textbox. Remove those parts if we can find them.
                if " Prop." in str_element:
                    str_element = str_element.replace(" Prop.", "").strip()
                if self.re_basefile_lax.search(str_element):
                    str_element = self.re_basefile_lax.sub("", str_element)
                _check_differing(d, self.ns['dcterms'].title, str_element)
                title_found = True

            # dcterms:issued
            if not issued_found and str_element.startswith("Stockholm den"):
                datestr = str_element[13:]
                if datestr.endswith("."):
                    datestr = datestr[:-1]
                try:
                    pubdate = self.parse_swedish_date(datestr)
                    _check_differing(d, self.ns['dcterms'].issued, pubdate)
                except ValueError:
                    # eg datestr might be an incomplete date like "6 mars" (w/o year)
                    pass
                issued_found = True

            if (isinstance(element, Sidbrytning) or
                (title_found and identifier_found and issued_found)):
                break

        # For old-style structured props, make sure we don't
        # accidentally create duplicate URIs
        protokollsutdrag_found = False
        for toplevelnode in doc.body:
            if isinstance(toplevelnode, Protokollsutdrag):
               if protokollsutdrag_found:
                   # this is the 2nd or 3rd node of this type. The 1st
                   # is the main one and the one where sections need
                   # to be referrable. For latter, we should make sure
                   # that no URIs are created for contained sections.
                   for subnode in toplevelnode:
                       if isinstance(subnode, Avsnitt):
                           # first remove the safety feature that
                           # keeps us from adding new attributes to
                           # initialized elements. FIXME: it's not a
                           # good smell that we need to do this.
                           setattr(subnode, '__initialized', False)
                           subnode.uri = None  # this'll keep as_xhtml from generating an URI
                           setattr(subnode, '__initialized', True)
                        # note: we should really recurse to process
                        # subsections et al, but in practice they
                        # don't seem to occur in the 2nd/3rd
                        # protokollsutdrag
               else:
                   protokollsutdrag_found = True

    def visitor_functions(self, basefile):
        # the .metrics.json file must exist at this point, but just in
        # case it doesn't
        metrics_path = self.store.path(basefile, "intermediate", ".metrics.json")
        if os.path.exists(metrics_path):
            with open(metrics_path) as fp:
                metrics = json.load(fp)
                defaultsize = metrics['default']['size']
        else:
            self.log.warning("%s: visitor_functions: %s doesn't exist" %
                             (basefile, metrics_path))
            defaultsize = 16
        sharedstate = {'basefile': basefile,
                       'defaultsize': defaultsize}
        functions = [(self.find_primary_law, sharedstate),
                     (self.find_commentary, sharedstate)]
        if not hasattr(self, 'sfsparser'):
            self.sfsparser = LegalRef(LegalRef.LAGRUM)
        self.sfsparser.currentlynamedlaws.clear()
        if self.document_type == self.PROPOSITION:
            functions.append((self.find_kommittebetankande, sharedstate))
        return functions


    def sanitize_identifier(self, identifier):
        pattern = {self.KOMMITTEDIREKTIV: "%s. %s:%s",
                   self.DS: "%s %s:%s",
                   self.PROPOSITION: "%s. %s/%s:%s",
                   self.SKRIVELSE: "%s. %s/%s:%s",
                   self.SOU: "%s %s:%s",
                   self.SO: "%s %s:%s"}
        try:
            parts = re.split("[\.:/ ]+", identifier.strip())
            id_template = pattern[self.document_type]
            # do we have enough parts for our template?
            if len(parts) == id_template.count("%s") - 1:
                # we're probably missing the first part (eg "Prop",
                # "Ds") and so what we have is a basefile-like
                # thing. Reconstruct the first part.
                parts.insert(0, re.split("[\.:/ ]+", self.infer_identifier(identifier))[0])
            # make sure the initial char is capitalized (this is
            # preferred to .capitalize() for strings that should be
            # all-caps, eg "SOU"
            parts[0] = parts[0][0].upper() + parts[0][1:]
            return pattern[self.document_type] % tuple(parts)
        except:
            self.log.warning("Couldn't sanitize identifier '%s'" % identifier)
            return identifier


    def get_pdf_analyzer(self, reader):
        if self.document_type == self.KOMMITTEDIREKTIV:
            from ferenda.sources.legal.se.direktiv import DirAnalyzer
            cls = DirAnalyzer
        elif self.document_type == self.SOU:
            from ferenda.sources.legal.se.sou import SOUAnalyzer
            cls = SOUAnalyzer
        elif self.document_type == self.DS:
            from ferenda.sources.legal.se.ds import DsAnalyzer
            cls = DsAnalyzer
        elif self.document_type == self.PROPOSITION:
            from ferenda.sources.legal.se.propositioner import PropAnalyzer
            cls = PropAnalyzer
        else:
            cls = PDFAnalyzer
        analyzer = cls(reader)

        if ".hocr." in reader.filename:
            analyzer.scanned_source = True
        return analyzer

    def find_primary_law(self, node, state):
        if 'primarylaw' in state:
            return None
        if not isinstance(node, Avsnitt) or not re.match("Förslag(|et) till lag om ändring i", node.title):
            if isinstance(node, Body):
                return state
            else:
                return None  # visit_node won't call any subnode
        state['primarylaw'] = self._parse_uri_from_text(node.title, state['basefile'])
        state['primarylawname'] = node.title
        self.log.debug("%s: find_primary_law finds %s (%s)" % (
            state['basefile'], state['primarylaw'], state['primarylawname']))
        return None

    def find_kommittebetankande(self, node, state):
        if not isinstance(node, Avsnitt) or (node.title not in ("Ärendet och dess beredning")):
            if isinstance(node, Body):
                return state  
            else:
                return None  # visit_node won't call any subnode
        commentary = []
        sectiontext = util.normalize_space(str(node))
        m = re.search("(SOU|Ds) (\d+:\d+)", sectiontext)
        if m:
            state['kommittensbetankande'] = m.group(2)
        return None

    def find_commentary(self, node, state):
        if not isinstance(node, Avsnitt) or (node.title not in ("Författningskommentar",
                                                                "Författningskommentarer",
                                                                "Specialmotivering")):
            if isinstance(node, Body):
                if 'commented_paras' not in state:
                    state['commented_paras'] = {}
                return state
            else:
                return None  # visit_node won't call any subnode
        cf = CommentaryFinder(state['basefile'], self._parse_uri_from_text, self.temp_sfs_uri)
        commentaries = []
        found = False
        for subsection in node: # nb: Node is the "Författningskommentar" chapter
            if cf.is_commentary_section(subsection):
                found = True
                uri, lawname = cf.identify_law(subsection.title)
                commentaries.append((subsection, uri, lawname))
        if not found: #  # no subsecs, ie the prop changes a single law
            if 'primarylaw' in state:
                commentaries.append((node, state['primarylaw'], state['primarylawname']))
            else:
                self.log.warning("%s: Författningskommentar does not specify name of law and find_primary_law didn't find it either" % state['basefile'])
                return  # there is absolutely nothing to analyze

        metrics = cf.analyze(commentaries)
        metrics["defaultsize"] = state["defaultsize"]
        for section, uri, name in commentaries:
            try:
                cf.markup_commentary(section, uri, name, metrics)
            except FSMStateError as e:
                self.log.warning("%s: %s" % (state['basefile'], e))
    
    re_urisegments = re.compile(r'([\w]+://[^/]+/[^\d]*)(\d+:(bih\.[_ ]|N|)?\d+([_ ]s\.\d+|))#?(K([a-z0-9]+)|)(P([a-z0-9]+)|)(S(\d+)|)(N(\d+)|)')
    def _parse_uri_from_text(self, text, basefile, baseuri=""):
        """Given some text, identifies the first reference to a part of a
        statute (possibly a relative reference) and returns the URI
        for that part. 

        Emits warning (and returns None) if not exactly one reference
        was found.

        """

        # OCR sources sometimes lack the space between digit and
        # section mark, ie "1§". It should be low-risk to expand this
        # with a space, at least when interpreting a subsection title.
        # FIXME: This doesn't fix "20a§"
        text = re.sub("(\d+)(§)", r"\1 \2", text)
        m = self.re_urisegments.match(baseuri)
        if m:
            attributes = {'law':m.group(2),
                          'chapter':m.group(6),
                          'section':m.group(8),
                          'piece':m.group(10),
                          'item':m.group(12)}
        else:
            attributes = {}  # should we warn here?
        res = self.sfsparser.parse(text,
                                   minter=self.minter,
                                   metadata_graph=self.commondata,
                                   baseuri_attributes=attributes,
                                   allow_relative=True)
        links = [n for n in res if isinstance(n, Link)]
        if not len(links):
            self.log.warning("%s: _parse_uri_from_text found %s links in '%s',"
                             "expected single link" %
                             (basefile, len(links), text))
            return None

        # if one OR MORE links found, use the first one (eg if text is
        # "8-10 §§", return only the uri for "8 §"
        return links[0].uri


    def _find_subnode(self, node, cls, reverse=True):
        # Finds the first (or last if reversed=True) subnode of a
        # certain type in the given node, recursively
        if isinstance(node, cls):
            return node
        elif isinstance(node, (CompoundElement, list)):
            if reverse:
                iterable = reversed(node)
            else:
                iterable = node
            for subnode in iterable:
                res = self._find_subnode(subnode, cls)
                if isinstance(res, cls):
                    return res

    def parse_entry_summary(self, doc):
        summary = doc.meta.value(URIRef(doc.uri), DCTERMS.abstract)
        if not summary:
            summary = ""
            # try to find a summary in the body text instead
            for section in doc.body:
                if hasattr(section, 'title') and section.title == "Sammanfattning":
                    # just use the first three paras for summary
                    return "\n\n".join([str(x) for x in section[:3]])
        return str(summary)

    def facets(self):
        return super(Offtryck, self).facets() + [Facet(DCTERMS.title,
                                                       toplevel_only=False)]
                
    def create_external_resources(self, doc):

        """Optionally create external files that go together with the
        parsed file (stylesheets, images, etc). """
        if len(doc.body) == 0:
            self.log.warning(
                "%s: No external resources to create", doc.basefile)
            return
        if not isinstance(doc.body, PDFReader):
            # The body is processed enough that we won't need to
            # create a CSS file w/ fontspecs etc
            return
        # Step 1: Create CSS
        # 1.1 find css name
        cssfile = self.store.parsed_path(doc.basefile, attachment='index.css')
        # 1.2 create static CSS
        fp = open(cssfile, "w")
        # 1.3 create css for fontspecs and pages
        # for pdf in doc.body:
        pdf = doc.body
        # this is needed to get fontspecs and other things
        for spec in list(pdf.fontspec.values()):
            fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                     (spec['id'], spec['size'], spec['family'], spec['color']))

        # 2 Copy all created png files to their correct locations
        totcnt = 0
        src_base = os.path.dirname(self.store.intermediate_path(doc.basefile))

        pdf_src_base = src_base + "/" + os.path.splitext(os.path.basename(pdf.filename))[0]

        cnt = 0
        for page in pdf:
            totcnt += 1
            cnt += 1
            # src = "%s%03d.png" % (pdf_src_base, page.number)
            src = "%s%03d.png" % (pdf_src_base, cnt)

            # 4 digits, compound docs can be over 1K pages
            attachment = "%04d.png" % (totcnt)
            dest = self.store.parsed_path(doc.basefile,
                                          attachment=attachment)

            # If running under RepoTester, the source PNG files may not exist.
            if os.path.exists(src):
                if util.copy_if_different(src, dest):
                    self.log.debug("Copied %s to %s" % (src, dest))

            fp.write("#page%03d { background: url('%s');}\n" %
                     (cnt, os.path.basename(dest)))


    def tabs(self):
        if self.config.tabs:
            label = {self.DS: "Ds:ar",
                     self.KOMMITTEDIREKTIV: "Kommittédirektiv",
                     self.PROPOSITION: "Propositioner",
                     self.SOU: "SOU:er"}.get(self.document_type, "Förarbete")
            return [(label, self.dataset_uri())]
        else:
            return []

class CommentaryFinder(object):

    def __init__(self, basefile, uriparser, uriminter):
        self.basefile = basefile
        self._parse_uri_from_text = uriparser
        self.temp_sfs_uri = uriminter
        self.debug = os.environ.get("FERENDA_FSMDEBUG_COMMENTARY")
        self.log = logging.getLogger("commentary")

        
    def is_commentary_section(self, subsection):
        if hasattr(subsection, 'title'):
            return bool(re.match("Förslag(|et) (till lag om|om lag till) ändring i", subsection.title) or re.match("Förslag(|et) till", subsection.title))

    def identify_law(self, title):
        # find out which laws this section proposes to
        # change (can be new or existing)
        if "ändring i" in title:   
            lawname = title.split(" ", 6)[-1]
            # FIXME: need to provide access to parse_uri_from_text function
            uri = self._parse_uri_from_text(title, self.basefile) # do _parse_uri_from_text really need basefile?
        else:
            # create a reference that could pass for a real
            # SFS-id, but with the name (the only identifying
            # information we have at this point) encoded into
            # it. 
            lawname = title.split(" ", 2)[-1]
            # FIXME: need to provide accesss to temp_sfs_uri (or move to this class?)
            uri = self.temp_sfs_uri(lawname)
        return uri, lawname

    def plot(self, filename, linespacings, linespacing_threshold, gaps, gap_threshold):
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            raise ImportError("You need matplotlib installed")
        plot = plt.subplot2grid((2,1), (0, 0))
        plot.set_title("linespacings")
        y, x, _ = plot.hist(linespacings, bins=50)
        plot.plot([linespacing_threshold, linespacing_threshold], [0, y.max()])
        if gaps:
            plot = plt.subplot2grid((2,1), (1, 0))
            plot.set_title("gaps")
            y, x, _ = plot.hist(gaps, bins=max(gaps))
            plot.plot([gap_threshold, gap_threshold], [0, y.max()]) 
        util.ensure_dir(filename)
        plt.savefig(filename, dpi=150)
        self.log.debug("wrote %s" % filename)

    def estimate_density(self, series, resolution, bandwidth):
        # do a pseudo-KDE (but using discrete, high-resolution
        # bins instead of a continous curve because math
        start = min(series)
        stop = max(series)
        width = stop - start
        binsize = width / resolution
        bins = [0] * (resolution + bandwidth)
        scale = [0] * (resolution + bandwidth)

        # a bandwidth wide array with values forming a normal
        # (gaussian) distribution
        kernel = [0] * bandwidth
        s = bandwidth / 10   
        m = 0
        kernelrange = list(range(int(-bandwidth/2)+1, int(bandwidth/2+1)))
        kernel = [1/(sqrt(2*pi)*s)*e**(-0.5*(float(x-m)/s)**2) for x in kernelrange]
        for val in series:
            normval = val - start
            fraction = normval / width
            binidx = floor(fraction * resolution) + int(bandwidth/2)
            for kernidx, offset in enumerate(kernelrange):
                bins[binidx+offset-1] += kernel[kernidx]
        for idx, bin in enumerate(bins):
            scale[idx] = ((idx - int(bandwidth/2))/resolution * width) + start
        return bins, scale
        
    def threshold(self, series, resolution=1000, bandwidth=200):
        # in the degenerate case that we have a single element series,
        # there is no way to calculate a threshold between "low" and
        # "high" values. Just return whatever that element is.
        assert len(series), "Impossible to calculate a KDE threshold for an empty series"
        if len(series) == 1:
            return series[0]
        bins, scale = self.estimate_density(series, resolution, bandwidth)      

        # find the valley after the first (significant, not less than
        # 25% of the highest) peak
        minpeak = max(bins) * 0.25
        peak = False
        best = 0
        for idx, val in enumerate(bins):
            if not peak:
                # walk til we find the peak
                if val >= best:
                    best = val
                elif val >= minpeak:
                    peak = True
            else:
                # walk til we find the valley
                if val <= best:
                    best = val
                else:
                    break
        # now the valley is at idx - 1
        return scale[idx-1]

    def collect_features(self, commentaries):
        features = {'linespacings': [],
                    'gaps': []}
        detect_singleline_spacing = False
        for section, law, lawname in commentaries:
            for idx, subnode in enumerate(section):
                if isinstance(subnode, Sidbrytning):
                    continue
                if hasattr(subnode, 'linespacing') and subnode.linespacing:
                    features['linespacings'].append(subnode.linespacing)
                elif detect_singleline_spacing:
                    # a single line paragraph has no easily discernable
                    # line height, but we can approximate by checking the
                    # nearest paragraph above and below
                    candidates = []
                    if (idx > 0 and
                        not isinstance(section[idx-1], Sidbrytning) and
                        subnode.bottom > section[idx-1].bottom):
                        candidates.append(subnode.bottom - section[idx-1].bottom)
                    if (idx +1 < len(section) and
                        not isinstance(section[idx+1], Sidbrytning) and
                        section[idx+1].bottom > subnode.bottom):
                        candidates.append(section[idx+1].bottom - subnode.bottom)
                    if candidates:
                        features['linespacings'].append(min(candidates) / subnode.font.size)
                if idx and subnode.top > prevnode.bottom:
                    features['gaps'].append(subnode.top - prevnode.bottom)
                prevnode = subnode
        return features
    
    def analyze(self, commentaries):
        # first, analyze gaps and linespacing constants using all sections
        features = self.collect_features(commentaries)
        gap_threshold = self.threshold(features['gaps'], resolution=1000, bandwidth=400)

        # if all we have are paragraphs with equal-ish linespacing
        # (say, because there is only comments, no acttext), minor
        # accidentally differences between paragraphs might fall on
        # different sides of this threshold. require that the spread is at least 20% (ie max ls must be at least 20% larger than min ls)
        max_ls = max(features['linespacings'])
        min_ls = min(features['linespacings'])
        if (max_ls - min_ls) / min_ls < 0.20:
            linespacing_threshold = min_ls # ie interpret everything as comment linespacing
        else:
            linespacing_threshold = self.threshold(features['linespacings'], resolution=1000, bandwidth=500)

        if os.environ.get("FERENDA_PLOTANALYSIS"):
            #datadir = self.store.datadir
            #self.store.datadir = "plots/%s" % self.alias
            # FIXME: We don't have access to a store object yet
            plot_path = self.store.path(state['basefile'], 'intermediate',
                                        '.commentary.plot.png')
            self.plot(plot_path, linespacings, linespacing_threshold, gaps, gap_threshold)
            #self.store.datadir = datadir
        return {'linespacing_threshold': linespacing_threshold,
                'gap_threshold': gap_threshold}
                
          

    def markup_commentary(self, section, uri, name, metrics):
        section[:] = self.find_commentary(section, uri, name, metrics)

    def make_commentary_parser(self, metrics, lawname, lawuri):
        # recognizers
        # "3 kap." or "3 kap. Om domare"
        def is_chapter_header(parser):
            text = str(parser.reader.peek()).strip()
            return bool(len(text) < 20 and text.endswith((" kap.", " kap")) or
                        re.match("\d+( \w|)\s[Kk]ap. +[^\d]", text))

        # "4 §" or "4 kap. 4 §"
        def is_section_header(parser):
            text = str(parser.reader.peek()).strip()
            return len(text) < 20 and text.endswith("§")

        # "4 § Lagtext lagtext och mera lagtext"
        def is_section_start(parser):
            text = str(parser.reader.peek()).strip()
            return bool(re.match("\d+(| \w) § +[A-ZÅÄÖ]", text))

        def is_transition_regs(parser):
            return str(parser.reader.peek()).strip() in  (
                'Ikraftträdande- och övergångsbestämmelse',
                'Ikraftträdande- och övergångsbestämmelser',
                'Ikraftträdandebestämmelser'
                'Övergångsbestämmelser')

        def is_header(parser):
            return probable_header(parser.reader.peek())

        def is_comment(parser):
            comment = probable_comment(parser.reader.peek())
            # if we're not in a commentary section we should not
            # assume commentary unles probable_comment returns True
            if comment is True:
                return True
            elif comment is False:
                return False
            else:
                # do extra work if we have no assumptions about
                # whether this is comment or not -- take a look at the
                # following para, if not separated by a gap.
                if (state["assume"] is None and
                    parser.reader.peek(2).top - parser.reader.peek().bottom < metrics['gap_threshold'] and
                    probable_comment(parser.reader.peek(2)) is True):
                    return True
                return state["assume"] == "comment"

        def is_acttext(parser):
            acttext = probable_acttext(parser.reader.peek())
            if acttext is True:
                return True
            elif acttext is False:
                return False
            else:
                return state["assume"] == "acttext"

        def is_pagebreak(parser):
            para = parser.reader.peek()
            if not isinstance(para,
                              (Textbox, Sidbrytning, UnorderedList)):
                raise ValueError("Got a %s instead of a Textbox/Sidbrytning/UnorderedList, this indicates broken parsing" % type(para))
            return isinstance(para, Sidbrytning)

        def is_paragraph(parser):
            return True
            
        # constructors
        @newstate('body')
        def make_body(parser):
            return p.make_children(Body())

        @newstate('comment')
        def make_comment(parser):
            state["assume"] = "comment"
            text = str(parser.reader.peek())
            if not state["comment_on"]:
                if state["beginning"]:
                    state["comment_on"] = lawuri
                    state["beginning"] = False
                    label = "Författningskommentar till %s" % lawname
                else:
                    self.log.warning("%s: Creating un-anchored comment '%s...'" % (self.basefile, text[:40]))
                    label = "Författningskommentar i %s" % lawname
            else:
                label = "Författningskommentar till %s %s" % (state['reftext'], lawname)
            if not state["skipheader"]:
                title = ""
            else:
                title = state["reftext"]
            f = Forfattningskommentar(title=title,
                                      comment_on=state["comment_on"],
                                      uri=None,
                                      label=label)
            f.append(make_paragraph(parser))
            comment = parser.make_children(f)
            state["comment_on"] = None
            state["reftext"] = None
            state["skipheader"] = False
            return comment
        
        def make_acttext(parser):
            state["assume"] = "acttext"
            return make_paragraph(parser)

        def make_header(parser):
            state["assume"] = "acttext" 
            return make_paragraph(parser)

        def make_paragraph(parser):
            ret = parser.reader.next()
            try:
                nextchunk = parser.reader.peek()
            except StopIteration:
                return ret
            # determine whether we need to change assumptions about
            # the following paragraph based on gap size
            if (not isinstance(nextchunk, Sidbrytning) and
                nextchunk.top - ret.bottom > metrics["gap_threshold"]):
                if state["assume"] == "acttext":
                    state["assume"] = "comment"
                elif state["assume"] == "acttext":
                    state["assume"] = "comment"
                else:
                    pass
            return ret

        def handle_pagebreak(parser):
            pagebreak = parser.reader.next()
            try:
                nextbox = parser.reader.peek()
                nextbox.font  # trigger an AttributeError if nextbox is also a Sidbrytning
                if probable_acttext(nextbox):
                    state["assume"] = "acttext"
                elif probable_comment(nextbox):
                    state["assume"] = "comment"
                else:
                    state["assume"] = None
            except (StopIteration, AttributeError):
                state["assume"] = None
            return pagebreak

        def setup_transition_header(parser):
            # ideally, we'd like URIs of the form
            # https://lagen.nu/1942:740#L2018:324, but at this
            # stage we don't have the change SFS URI. Create a
            # fake URI instead with just a #L fragment.
            state["comment_on"] = state["law"].split("#")[0] + "#L"
            state["reftext"] = str(parser.reader.next()).strip()
            state["skipheader"] = True

        def setup_section_header(parser):
            # a header might be followed by acttext or commmenttext --
            # it's not easy to tell. examine each box until we get a
            # definite answer (or run out of adjacent boxes)
            idx = 2
            prevbox = None
            acttext = False
            while True:
                box = parser.reader.peek(idx)
                if isinstance(box, Sidbrytning) or (prevbox and box.top - prevbox.bottom > metrics['gap_threshold']):
                    break
                acttext = probable_acttext(box)
                if acttext in (True, False):
                    break
                prevbox = box
                idx += 1

            if acttext:
                ret = make_section(parser)
                state["assume"] = "acttext"
                state["skipheader"] = False
                return ret
            else:
                make_section(parser) # throw away the result, which will be exactly the header we want to skip
                state["assume"] = "comment"
                state["skipheader"] = True

        def setup_section_start(parser):
            state["assume"] = "acttext"
            state["skipheader"] = False 
            return make_section(parser)

        def make_section(parser):
            text = str(parser.reader.peek())
            state["reftext"] = text[:text.index("§")+ 1]
            state["comment_on"] = self._parse_uri_from_text(state["reftext"], self.basefile, state["law"])
            state["comment_start"] = False
            return make_paragraph(parser)
            
            
        def setup_chapter_start(parser):
            text = str(parser.reader.peek())
            newlaw = self._parse_uri_from_text(text, self.basefile, state["law"])
            if newlaw:
                state["law"] = newlaw
                state["comment_on"] = state["law"]
            state["skipheader"] = True
            state["reftext"] = text
            return parser.reader.next()
            
        
        # helpers

        # The helpers are tristate functions:
        # True: This is probably <thing>
        # False: This is most likely not <thing>
        # None: I have no idea whether this is <thing> or not
        def probable_header(para):
            text = str(para).strip()
            if text == 'Bestämmelse Kommentarerna finns i avsnitt':
                # This is a table heading (not real header) type of thing
                # occurring in SOU 2017:66, but similar constructs might
                # appear elsewhere.
                return False
            # headers are less than 100 chars and do not end with a period
            # or other non-hederish thing
            return (len(text) < 100 and
                    not text.endswith((")", " i", " §")) and
                    not text.endswith(".") or text.endswith((" m.m.",
                                                             " m.fl.")))
                                      

        def probable_comment(para):
            text = str(para).strip()
            if re.match("(Av p|P)aragrafen (framgår|innehåller|har behandlats|är ny|, som är ny|avgränsar|innebär)", text):
                return True
            # elif re.match("(I f|F)örsta stycket", text):  # this overmatches, eg ÅRL 7:31 2 st
            elif re.match("I första stycket", text): 
                return True
            elif re.match("\((Jfr|Paragrafen)", text):
                return True
            elif metrics['defaultsize'] >= para.font.size + 2:
                return False
            elif hasattr(para, 'lines') and para.lines > 1:
                return bool(metrics['linespacing_threshold'] and
                            para.linespacing and 
                            para.linespacing >= metrics['linespacing_threshold'])
            else:
                return None 


        def probable_acttext(para):
            # returns True iff this text is probably acttext
            # returns False iff it's probably not acctext
            # returns None if we don't have enough data
            # (maybe because it's a single line or a Sidbrytning)
            if isinstance(para, Sidbrytning):
                return None

            # 2 clear indicators of acttext: font size is smaller
            if metrics['defaultsize'] >= para.font.size + 2:
                return True
            elif hasattr(para, 'lines') and para.lines > 1:
                # or linespacing is tighter than average
                return bool(metrics['linespacing_threshold'] and
                            para.linespacing and 
                            para.linespacing < metrics['linespacing_threshold'])
            else:
                return None

        # setup
        state = {"skipheader": False,
                 "comment_on": None,
                 "beginning": True,
                 "assume": "comment",
                 "law": lawuri}
        p = FSMParser()
        p.set_recognizers(is_pagebreak,
                          is_chapter_header,
                          is_section_header,
                          is_section_start,
                          is_transition_regs,
                          is_header,
                          is_comment,
                          is_acttext,
                          is_paragraph)
        commonstates = "body", "comment"
        p.set_transitions({(commonstates, is_pagebreak): (handle_pagebreak, None),
                           ("body", is_header): (make_header, None),
                           ("body", is_chapter_header): (setup_chapter_start, None),
                           ("body", is_section_header): (setup_section_header, None),
                           ("body", is_section_start): (setup_section_start, None),
                           ("body", is_comment): (make_comment, "comment"),
                           ("body", is_acttext): (make_acttext, None),
                           ("body", is_transition_regs): (setup_transition_header, None),
                           ("comment", is_section_start): (False, None),
                           ("comment", is_header): (False, None),
                           ("comment", is_chapter_header): (False, None),
                           ("comment", is_section_header): (False, None),
                           ("comment", is_acttext): (False, None),
                           ("comment", is_paragraph): (make_paragraph, None),
                           })
        p.initial_state = "body"
        p.initial_constructor = make_body
        p.debug = self.debug
        return p

    def find_commentary(self, section, uri, name, metrics):
        textnodes = self.make_commentary_parser(metrics, name, uri).parse(section)
        return textnodes
        

def offtryck_parser(basefile="0", metrics=None, preset=None,
                    identifier=None, debug=False, initialstate=None, parseconfig="default"):
    # First: merge the metrics we're provided with with a set of
    # defaults (for fallback), and wrap them in a LayeredConfig
    # structure
    if not metrics:
        metrics = {}
    defaultmetrics = {'header': 0,  # fix these
                      'footer': 1000,  # -""-
                      'leftmargin': 172,
                      'odd_parindent': 187,
                      'rightmargin': 619,
                      'leftmargin_even': 278,
                      'even_parindent': 293,
                      'rightmargin_even': 725,
                      'bottommargin': 800,
                      'topmargin': 100,
                      'h1': {'family': 'TimesNewRomanPS-BoldMT',  # should also be weight: bold?
                             'size': 20},
                      'h2': {'family': 'TimesNewRomanPS-BoldMT',
                             'size': 17},
                      'h3': {'family': 'TimesNewRomanPS-BoldMT',
                             'size': 15},
                      'default': {'family': 'TimesNewRomanPSMT',
                                  'size': 13}
                      }
    metrics = LayeredConfig(Defaults(defaultmetrics),
                            Defaults(metrics))

    # another mutable variable, which is accessible from the nested
    # functions
    defaultstate = {'pageno': 0,
                    'page': None,
                    'appendixno': None,
                    'appendixstarted': False,  # reset at every page,
                                               # set to True once
                                               # "Bilaga (\n)" found
                                               # in margin
                    'preset': preset,
                    'sectioncache': True}
    if initialstate:
        defaultstate.update(initialstate)
    state = LayeredConfig(Defaults(defaultstate))
    state.sectioncache = {}

    def is_pagebreak(parser):
        return isinstance(parser.reader.peek(), Page)

    # page numbers, headers
    def is_nonessential(parser, chunk=None):
        if not chunk:
            chunk = parser.reader.peek()
        strchunk = str(chunk).strip()
        # everything above or below these margins should be
        # pagenumbers -- always nonessential
        if chunk.top > metrics.bottommargin or chunk.bottom < metrics.topmargin:
            return True
        if metrics.scanned_source:
            # this is some sort of printer's instruction at the bottom
            # of the page, but not reliably within
            # metrics.bottommargin.
            # eg "4 Riksdagen 1987/88. I saml. Nr 155" (and with OCR errors like
            # '5 Rikxdzguøn /987/88. I .su/nl. Nr [55' or "6 Rikxtltrguwi I987/':'\'3. I smul. iVI' /55"
            if (chunk.top > metrics.pageheight * 0.8 and
                re.match(r"\d+ rik(sdagen|xdzguøn|xtltrguwi) [\d\./ :'I\\]+(saml|smul|su/nl)\. (nr|iVI') [\[/]?\d", strchunk, flags=re.IGNORECASE)):
                return True
            # very old props (only up till about 1971:20) have
            # something like "Kungl. Maj:ts proposition nr 4 år 1971"
            # at the top. When setting up the parser, the attribute
            # current_long_identifier might have been created. We use
            # get_close_matches instead of straight comparison because
            # OCR (and also because page numbers might be mixed in).
            if (hasattr(parser, 'current_long_identifier') and
                (chunk.bottom < metrics.pageheight * 0.2) and
                (difflib.get_close_matches(strchunk, [parser.current_long_identifier]))):
                return True
        
        if metrics.scanned_source:
            digitmatch = lambda s: s.replace("l", "1").isdigit()
        else:
            digitmatch = lambda s: s.isdigit()

        # pagenumbers can be in the left/right margin as well
        if ((chunk.right < metrics_leftmargin() or
             chunk.left > metrics_rightmargin()) and
                digitmatch(strchunk)):
            return True

        # Propositioner has the identifier in the left or right
        # margin, set in the default style (or smaller) (but in OCR
        # source the style might appear to be as much as 2 points
        # bigger)
        tolerance = 2 if metrics.scanned_source else 0
        if metrics.scanned_source:
            textmatch = lambda a, b: len(difflib.get_close_matches(a, [b], n=1, cutoff=0.6)) == 1
        else:
            # the match func used to be operator.eq, but we'd like to
            # match "Prop 2013/14:34\nBilaga 2" as nonessential as
            # well
            textmatch = lambda a, b: a.startswith(b)


        if (chunk.font.size <= metrics.default.size + tolerance and
            (chunk.right < metrics_leftmargin() or
             chunk.left > metrics_rightmargin()) and
            textmatch(strchunk, parser.current_identifier)):
            # print("%s on p %s is deemed nonessential" % (str(chunk), state.pageno))
            return True
        # the first page of a prop has it in the right margin, with larger font
        if (state.pageno == 1 and chunk.left > metrics_rightmargin() and
            textmatch(strchunk, parser.current_identifier)):
            return True
        
        # Direktiv first page has a similar identifier, but it starts
        # slightly before the right margin (which in itself might be
        # quantized slightly to the left, hence +20), and is set in
        # larger type.
        if (chunk.left + 20 > metrics_rightmargin() and
                strchunk == parser.current_identifier):
            return True

    def is_protokollsutdrag(parser):
        chunk = parser.reader.peek()
        return (chunk.font.size > metrics.default.size and
                chunk.top < metrics.pageheight / 5 and
                (str(chunk).strip().endswith("departementet") or
                 str(chunk).strip().startswith("Lagrådet")) and
                str(parser.reader.peek(2)).startswith("Utdrag ur protokoll vid"))
        
        
    def is_prophuvudrubrik(parser):
        if state.pageno != 1:
            return False
        chunk = parser.reader.peek()
        if isinstance(chunk, Page):
            return False
        if chunk.font.size >= metrics.h1.size:
            strchunk = str(chunk).strip()
            if re.match("Regeringens proposition \d{4}(|/\d{2,4}):\d+", strchunk):
                return True

    def is_proprubrik(parser):
        if state.pageno != 1:
            return False
        chunk = parser.reader.peek()
        if isinstance(chunk, Page):
            return False
        if (chunk.top < state.page.height / 4 and
            chunk.font.size > metrics.default.size):
            strchunk = str(chunk).strip()
            if not re.match("(Prop. \d{4}(|/\d{2,4}):\d+|Propositionens huvudsakliga innehåll)", strchunk):
                return True

    def is_preamblesection(parser):
        chunk = parser.reader.peek()
        if isinstance(chunk, Page):
            return False
        txt = str(chunk).strip()
        # Since this recognizer is hardcoded to recognize a fixed set
        # of headings we could just make sure the font is bigger than
        # defalt. For old material (scanned) we don't even look at size.
        #
        # A problem with this is that it's too easy to mistake an
        # entry for a section in the TOC for the actual section
        # heading. We probably need a more fine-grained or contextual
        # detection.
        # if not metrics.scanned_source and chunk.font.size <= metrics.default.size:
        if chunk.font.size <= metrics.default.size:
            return False
        if "...." in txt:  # probably a line in a TOC
            return False
        for validheading in ('Propositionens huvudsakliga innehåll',
                             'Innehållsförteckning',
                             'Till statsrådet',
                             'Innehåll',
                             'Sammanfattning',
                             'Propositionens lagförslag', # is preamble in older props
                             'Författningsförslag',       # and also eg Ds 2008:68
                             'Referenser',                 # more like PostambleSection (eg SOU 2007:72 p 377)
                             'Förkortningar',
                             # not really a preamblesection, but for
                             # mid-90:s propositioner, we can't get a
                             # good digital version that has numbered
                             # headings which is_section
                             # requires. This at least lets us avoid
                             # getting everything mixed up with
                             # Innehållsförteckning:
                             'Förslag till riksdagsbeslut' 
        ):
            if txt.startswith(validheading):
                return True
            if txt.endswith("departementet"): # older props, also used
                                              # for a appendix-like
                                              # "utdrag ur protokoll
                                              # vid
                                              # regeringssammanträde"
                                              # formal section
                                              # normally at the very
                                              # end.
                return True

    def is_section(parser):
        (ordinal, headingtype, title) = analyze_sectionstart(parser)
        if (getattr(parser, 'in_forfattningsforslag', False) and
            ordinal and
            re.match("Förslag(|et) [tl]ill", title)):
            return False

        if "...." in title:  # probably a line in a TOC
            return False

        if (isinstance(title, str) and
            re.search("\d+$", title) and
            "...." in str(parser.reader.peek(2))): # might still be TOC
            return False

        if ordinal:
            return headingtype == "h1" and ordinal.count(".") == 0

    def is_subsection(parser):
        (ordinal, headingtype, title) = analyze_sectionstart(parser)
        if "...." in title:  # probably a line in a TOC
            return False
        if ordinal:
            return headingtype == "h2" and ordinal.count(".") == 1

    def is_unorderedsection(parser):
        # Frontpage textboxes (title, identifier and abstract heading)
        # for this doctype should not be thought of as
        # unorderedsections, even though they're set in the same type
        # as normal sections. 
        if state.preset == "proposition":
            return False
        chunk = parser.reader.peek()
        return (chunk.font.size == metrics.h1.size and
                chunk.font.family == metrics.h1.family)


    def is_unorderedsubsection(parser):
        # Subsections in "Författningskommentar" sections are
        # not always numbered. As a backup, check font size and family as well
        chunk = parser.reader.peek()
        # avoid having sectionheaders in
        # forfattningskommentar/specialmotivering (like "5 c §") interpreted as
        # subsection headers (might otherwise happen due to
        # irregular font size estimates or irregular font size usage
        # The regex is forgiving of OCR errors.
        if re.match("\.?[l\d]\s*(|\w )§$", str(chunk).strip()):
            return False
        if (sizematch(metrics.h2.size, chunk.font.size, tolerate_less_ocr=0, tolerate_more_ocr=1) and
                chunk.font.family == metrics.h2.family):
            return True
        

    def is_subsubsection(parser):
        (ordinal, headingtype, title) = analyze_sectionstart(parser)
        if "...." in title:  # probably a line in a TOC
            return False
        if ordinal:
            return headingtype == "h3" and ordinal.count(".") == 2

    def is_forfattningsforslag(parser):
        (ordinal, headingtype, title) = analyze_sectionstart(parser)
        if getattr(parser, 'in_forfattningsforslag', False) and ordinal and title.startswith("Förslag till"):
            return True  # don't even bother checking heading size 

    def is_bulletlist(parser):
        chunk = parser.reader.peek()
        strchunk = str(chunk)
        # different ways of representing bullet points -- U+2022 is
        # BULLET, while U+F0B7 is a private use codepoint, which,
        # using the Symbol font, appears to produce something
        # bullet-like in dir 2016:15. "−" is used in dir 2011:84, but
        # is maybe semantically different from a bulleted list (a
        # "dashed list")?
        if strchunk.startswith(("\u2022", "\uf0b7", "−")):
            return True

    def is_appendix(parser):
        def is_appendix_header(chunk):
            if isinstance(chunk, Page):
                return False  # this can happen before is_pagebreak
                              # get's a chance, since we call this
                              # function with .peek(2) and .peek(3)
                              # below
            txt = str(chunk).strip()
            if (chunk.font.size == metrics.h1.size and (txt.startswith("Bilaga ") or txt.startswith("Bilagor"))):
                if txt.startswith("Bilaga "):
                    # assume that whatever follows is a number -- if
                    # not, this is not a proper appendix header anyway
                    return int(re.split(r"[ \:]", txt)[1])
                else:
                    return True 

        def is_implicit_appendix(chunk):
            # The technique of starting a new appendix without stating
            # so in the margin on the first page of the appendix
            # occurs in some older props, eg Prop 1997/98:18

            # 1. Has to be on the top 15% of page
            if chunk.bottom  > state.page.height * 0.15:
                  return False

            # 2. Has to be set in a h1 font (with some tolerances
            # because of scanned material
            tolerance = 2 if metrics.scanned_source else 0
            if abs(chunk.font.size - metrics.h1.size) <= tolerance:

                # 3. Has to match one of a few standardized appendicies headings.
                txt = str(chunk).strip()
                if txt in ("Promemorians lagförslag", "Lagrådsremissens lagförslag", "Lagrådets yttrande", "Lagrådet"):
                    return True
                elif txt.startswith("Förteckning över remissinstanser"):
                    return True
                return False

        def is_mashed_header(chunk):
            # For scanned sources, a header and the "Bilaga \d" label
            # in the margin might mashing together
            # 1. text larger than default
            if abs(chunk.font.size - metrics.default.size) <= 1:
                return False
            # 2. preferably first part of the page (or the top 15% of page)
            if chunk.bottom  > state.page.height * 0.15:
                return False
            # 3. endswith "Bilaga \d" (or has it towards the end, for
            # the case of a three-line heading like:
            # 
            #   Header header heder head header   Prop yyyy/yy:nn
            #   head header head header header    Bilaga 2
            #   heading header head
            #
            # (it's always more complicated, eg with a five-line heading)
            txtchunk = str(chunk).strip()
            txtlen = len(txtchunk)
            m = re.search("Bilaga (\d+)", txtchunk)
            # an indicator of mashed-ness is that the textbox sticks outside of the margins (maybe we should only check the rightmargin?)
            if m and (m.end() == txtlen or
                      metrics_leftmargin() > chunk.left or 
                      metrics_rightmargin() < chunk.right):
                return int(m.group(1))

        chunk = parser.reader.peek()
        txtchunk = util.normalize_space(str(chunk))
        # Sanity check 1: a header can't be longer than a certain number of characters
        if metrics.scanned_source:
            # high likelyhood of heading bleeding into the margin
            # where "Bilaga 1" appears, therefore we need to accept a
            # longer chunk.
            #
            # Bilaga 5 of prop 1992/93:30 has 262 characters...
            maxlen = 270
        else:
            maxlen = 100
        if len(txtchunk) > maxlen:
            return False

        # Sanity check 2: Differentiate between a proper header and
        # the reference to a header in the TOC (indicated by a string
        # of "...")
        if ".." in txtchunk:  
            return False
        
        is_header = False
        if not state.appendixstarted:
            is_header = is_appendix_header(chunk)
            if not is_header:
                is_header = is_implicit_appendix(chunk)
                if not is_header and metrics.scanned_source:
                    is_header = is_mashed_header(chunk)

        if not is_header:
            # check that the chunk in question is not too big
            tolerance = 2 if metrics.scanned_source else 0
            if metrics.default.size + tolerance < chunk.font.size:
                return False

            # check that the chunk is placed in the correct margin
            # NOTE: in some cases (prop 1972:105 p 145 and generally
            # everything before prop 1987/88:69) the "Bilaga" margin
            # header appears in the (extended) topmargin, not in the
            # leftmargin. 
            if (parser.current_identifier.startswith("Prop.") and
                ("Prop. 1987/88:69" > parser.current_identifier)):
                extended_topmargin = metrics.pageheight / 5
                placement = lambda c: c.bottom < extended_topmargin
            elif parser.current_identifier.startswith(("Ds", "SOU")):
                # For Ds/SOU it always appears in the topmargin
                placement = lambda c: c.bottom <= metrics.topmargin
            else:
                placement = lambda c: c.right < metrics_leftmargin() or c.left > metrics_rightmargin()

            if placement(chunk):
                # NOTE: filter out indications of nested
                # appendicies (eg "Bilaga 5 till RSVs skrivelse")
                m = re.search("Bilaga( \d+| I| l|$)(?!(\d| *till))", txtchunk)
                if m:
                    if m.group(1):
                        match = m.group(1).strip()
                        if match in ("I", "l"):   # correct for OCR mistake
                            match = "1" 
                        ordinal = int(match)
                    else:
                        ordinal = 1
                    if ordinal == state.appendixno:
                        # this is just one more page of the appendix
                        # currently being processed
                        state.appendixstarted = True
                    else:
                        # OK, this can very well be an appendix, but just
                        # to be sure, keep reading to see if we have a
                        # Appendix-like heading as well
                        try:
                            if (is_appendix_header(parser.reader.peek(2)) or
                                is_appendix_header(parser.reader.peek(3))):
                                state.appendixno = ordinal
                                return False
                            else:
                                return True
                        except StopIteration:
                            # So no more document? this might be a very
                            # short appendix...
                            return True
        else:  # is_header = True

            if isinstance(is_header, int) and is_header == state.appendixno:
                # this is just one more page of the appendix
                # currently being processed
                state.appendixstarted = True
            else:
                # should we do something about state.appendixno?
                # (ie. increment by one, or possibly more in the
                # is_appendix_header or is_mashed_header cases?)
                return True

    def is_paragraph(parser):
        return True

    @newstate('body')
    def make_body(parser):
        return p.make_children(Body())

    @newstate("protokollsutdrag")
    def make_protokollsutdrag(parser):
        title = str(parser.reader.next()).strip()
        return p.make_children(Protokollsutdrag(title=title))

    @newstate("frontmatter")
    def make_frontmatter(parser):
        return p.make_children(FrontmatterSection())

    def make_prophuvudrubrik(parser):
        return PropHuvudrubrik(str(parser.reader.next()).strip())

    def make_proprubrik(parser):
        s = str(parser.reader.next()).strip()
        # it's common that offtryck_gluefunc incorrectly glues the
        # heading and the identifier (which is at same height and same
        # size, but really outside in the margin). The easist place to
        # fix is really here (even though it would be better in
        # offtryck_gluefunc).
        if s.endswith(parser.current_identifier):
            s = s[:-len(parser.current_identifier)].strip()
        return PropRubrik(s)

    def make_paragraph(parser):
        return parser.reader.next()

    @newstate('preamblesection')
    def make_preamblesection(parser):
        chunk = parser.reader.next()
        title = str(chunk).strip()
        s = PreambleSection(title=title)
        # normally the entire title should be either of these, but due
        # to OCR problems additional text (like the "Prop. YYYY/YY:NN"
        # in the margin) might be part of the chunk). Let's try to be
        # inclusive here, we've already determined that this is a
        # preamblesection.
        if title.startswith(("Författningsförslag", "Propositionens lagförslag")):
            # If the starting Författningsförslag section is
            # unnumbered, the contained individual författningsförslag
            # will be level-1 numbered, but they are not real level-1
            # sections and will be contradicted by later true level-1
            # sections.
            parser.in_forfattningsforslag = True
        if s.title in ("Innehållsförteckning", "Innehåll"):
            parser.make_children(s)  # throw away -- FIXME: should we
                                     # really do that right in the
                                     # parsing step? shouldn't we wait
                                     # until postprocess_doc?
            return None
        else:
            ps = parser.make_children(s)
            parser.in_forfattningsforslag = False
            return ps

    @newstate('unorderedsection')
    def make_unorderedsection(parser):
        s = UnorderedSection(title=str(parser.reader.next()).strip())
        return parser.make_children(s)

    @newstate('unorderedsubsection')
    def make_unorderedsubsection(parser):
        s = UnorderedSection(title=str(parser.reader.next()).strip())
        return parser.make_children(s)

    @newstate('bulletlist')
    def make_bulletlist(parser):
        ul = UnorderedList(top=None, left=None, bottom=None, right=None, width=None, height=None, font=None)
        li = make_listitem(parser)
        ul.append(li)
        ret = parser.make_children(ul)
        ret.top = min([li.top for li in ret])
        # ret.left = min([li.left for li in ret])
        ret.bottom = max([li.bottom for li in ret])
        # ret.right = max([li.right for li in ret])
        # ret.width = ret.right - ret.left
        ret.height = ret.bottom - ret.top
        ret.font = ret[0].font
        return ret

    def make_listitem(parser):
        chunk = parser.reader.next()
        s = str(chunk)
        if " " in s:
            # assume text before first space is the bullet
            s = s.split(" ",1)[1]
        else:
            # assume the bullet is a single char
            s = s[1:]
        return ListItem([s], top=chunk.top, left=chunk.left, right=chunk.right, bottom=chunk.bottom,
                        width=chunk.width, height=chunk.height, font=chunk.font)

    @newstate('appendix')
    def make_appendix(parser):
        # now, an appendix can begin with either the actual
        # headline-like title, or by the sidenote in the
        # margin. Find out which it is, and plan accordingly.
        done = False
        title = None
        # First, find either an indicator of the appendix number, or
        # calculate our own
        chunk = parser.reader.next()
        strchunk = str(chunk)
        # correct OCR mistake
        if state.appendixno and state.appendixno > 1 and strchunk.startswith("Bilaga ll-"):
            strchunk = strchunk.replace("Bilaga ll-", "Bilaga 4")
            
        m = re.search("Bilaga( \d+| I| l|$)", str(chunk))
        if m and m.group(1):
            match = m.group(1).strip()
            if match in ("I", "l"):   # correct for OCR mistake
                match = "1" 
            state.appendixno = int(match)
            # If the text "Bilaga \d" doesn't occur at the start of
            # this chunk, whatever comes before it might be the real
            # title (maybe, at least in mashed-together scanned
            # sources).
            if metrics.scanned_source and m.start() > 0:
                title = util.normalize_space(str(chunk)[:m.start()])
                # sanity check -- what comes before might be the prop
                # identifier in the margin
                if len(title) < 20 and title.lower().startswith("prop."):
                    title = None
            chunk = None  # make sure this chunk doesn't go into spill below
        else:
            # this probably mean that we have an implicit appendix (se
            # is_appendix for when that's detected)
            if state.appendixno:
                state.appendixno += 1
            else:
                state.appendixno = 1

        # next up, read the page to find the appendix title
        spill = []  # save everyting we read up until the appendix
        if title is None:
            while not done:
                if isinstance(chunk, Page):
                    title = ""
                    done = True
                if isinstance(chunk, Textbox) and int(chunk.font.size) >= metrics.h2.size:
                    title = util.normalize_space(str(chunk))
                    chunk = None
                    done = True
                if not done:
                    if chunk and not is_nonessential(parser, chunk):
                        spill.append(chunk)
                    chunk = parser.reader.next()
            if chunk and not isinstance(chunk, Page):
                spill.append(chunk)
        s = Appendix(title=title,
                     ordinal=str(state.appendixno),
                     uri=None)
        s.extend(spill)
        return parser.make_children(s)

    # this is used for subsections and subsubsections as well --
    # probably wont work due to the newstate property
    @newstate('section')
    def make_section(parser):
        chunk = parser.reader.next()
        ordinal, headingtype, title = analyze_sectionstart(parser, chunk)
        # make sure the ordinal hasn't been used before
        if ordinal:
            short = lambda x: x if len(x) < 50 else x[:50] + "..."
            # FIXME: where were we planning to use this dcterms:identifier label?
            # identifier = "Prop. %s, avsnitt %s" % (basefile, ordinal)
            if ordinal in state.sectioncache:
                parser.log.warning("Dupe section %s '%s' at p %s, previous at %s. Ignoring." %
                                   (ordinal, short(title), state.pageno,
                                    state.sectioncache[ordinal]))
                # make it a pseudosection
                title = util.normalize_space(str(chunk))
                ordinal = None
            else:
                state.sectioncache[ordinal] = "'%s' at p %s" % (short(title), state.pageno)
        if ordinal:
            s = Avsnitt(ordinal=ordinal, title=title)
        else:
            s = PseudoSection(title=str(title))
        return parser.make_children(s)

    @newstate('forfattningsforslag')
    def make_forfattningsforslag(parser):
        chunk = parser.reader.next()
        ordinal, headingtype, title = analyze_sectionstart(parser, chunk)
        s = Forfattningsforslag(ordinal=ordinal, title=title)
        return parser.make_children(s)

    def skip_nonessential(parser):
        parser.reader.next()
        return None

    def skip_pagebreak(parser):
        # increment pageno
        state.page = parser.reader.next()
        try:
            state.pageno = int(state.page.number)
        except ValueError as e:  # state.page.number was probably a roman numeral (typed as string)
            state.pageno = 0  # or maybe convert roman numeral to int?
        
        sb = Sidbrytning(width=state.page.width,
                         height=state.page.height,
                         src=state.page.src,
                         ordinal=state.page.number)
        state.appendixstarted = False
        return sb

    # the title of a section must start with a uppercase char (This
    # eliminates misinterpretation of things like "5 a
    # kap. Referensland för..." being interpreted as ordinal "5" and
    # title "a kap. Referensland för...")
    re_sectionstart = re.compile("^(\d[\.\d]*) +([A-ZÅÄÖ].*)$").match


    def analyze_sectionstart(parser, chunk=None):
        """returns (ordinal, headingtype, text) if it looks like a section
        heading, (None, None, chunk) otherwise.

        """
        if not chunk:
            chunk = parser.reader.peek()
        strchunk = str(chunk).strip()
        # 1. clean up indata
        if metrics.scanned_source:
            if strchunk.startswith("l "): # probable OCR mistake
                strchunk = "1" + strchunk[1:]
            # "3. 12" -> "3.12" FIXME: Generalize to handle phantom
            # spaces in other places (3- or 4 level section headings)
            strchunk = re.sub("(\d+)\.\s+(\d+)", r"\1.\2", strchunk)

            # "1 1 Hemställan" -> "11 Hemställan"
            strchunk = re.sub("^(\d+) (\d+)(?= +[A-ZÅÄÖ])", r"\1\2", strchunk)

        m = re_sectionstart(strchunk)
        if not m:
            return (None, None, chunk)
        
        ordinal = m.group(1).rstrip(".")
        title = m.group(2).strip()
        headingtype = "h" + str(ordinal.count(".") + 1)

        # make sure the font is bigger than default.  NOTE: Old
        # propositioner have section headers in the same size as the
        # default text, so we dial down the required min size in that
        # case.
        min_size = metrics.default.size

        # in two cases we can accept a header size that is equal to
        # default font size: Firstly if it's a level 3 header (which
        # uses 14pt font in modern SOU templates) or if it's a old
        # proposition (that also uses small fontsizes in headings)
        if (headingtype == 'h3' or
            (parser.current_identifier.startswith("Prop.") and
             ("Prop. 1987/88:1" > parser.current_identifier))):
            min_size -= 1

        if chunk.font.size <= min_size:
            return (None, None, chunk)

        # sections doesn't end like this
        if ((strchunk.endswith(".") and not 
             (strchunk.endswith("m.m.") or
              strchunk.endswith("m. m.") or
              strchunk.endswith("m.fl.") or
              strchunk.endswith("m. fl."))) or
            strchunk.endswith(",") or
            strchunk.endswith(" och") or
            strchunk.endswith(" eller") or
            strchunk.endswith(":") or
            strchunk.endswith("-")):
            return (None, None, chunk)

        # final sanity check -- how long can a heading really be (we
        # have similar check in sfs_parser.py:isRubrik (which has a
        # threshold of 135 chars -- we allow somewhat longer
        # here. We'll primarily reach this when a paragraph is
        # interrupted by a pagebreak, and thus not end with a period
        # or other punctuation which we detect above.
        if len(title) > 200:
            return (None, None, chunk)
        
        # looks like we've made it!
        return (ordinal, headingtype, util.normalize_space(title))


    def metrics_leftmargin():
        if state.pageno % 2 == 0:  # even page
            return metrics.leftmargin_even
        else:
            return metrics.leftmargin


    def metrics_rightmargin():
        if state.pageno % 2 == 0:  # even page
            return metrics.rightmargin_even
        else:
            return metrics.rightmargin

    def sizematch(want, got, tolerate_less_ocr=1, tolerate_more_ocr=1):
        # matches a size 
        if metrics.scanned_source:
            # want: 10, got: 9, tolerate_less_ocr: 1, tolerate_more_ocr: 0 => True
            # 10 + 0 <= 9 + 1
            return want + tolerate_more_ocr <= got + tolerate_less_ocr
        else:
            return want == got
        

    p = FSMParser()

    recognizers = [is_pagebreak,
                   is_appendix,
                   is_nonessential,
                   is_section,
                   is_subsection,
                   is_subsubsection,
                   is_preamblesection,
                   is_forfattningsforslag,
                   is_unorderedsection,
                   is_unorderedsubsection,
                   is_bulletlist,
                   is_paragraph]
    if parseconfig == "noappendix":
        recognizers.remove(is_appendix)
    elif parseconfig == "simple":
        recognizers = [is_pagebreak, is_paragraph]
    if preset == "proposition":
        recognizers.insert(0, is_proprubrik)
        recognizers.insert(0, is_prophuvudrubrik)
        # for older props, using the Protokollsutdrag structure (see
        # comment for that class). Insert after is_nonessential
        recognizers.insert(5, is_protokollsutdrag)
    p.set_recognizers(*recognizers)

    commonstates = ("body", "frontmatter", "preamblesection", "forfattningsforslag", "protokollsutdrag", "section",
                    "subsection", "unorderedsection", "unorderedsubsection", "subsubsection",
                    "appendix")
    commonbodystates = commonstates[1:]
    p.set_transitions({(commonstates, is_nonessential): (skip_nonessential, None),
                       (commonstates, is_pagebreak): (skip_pagebreak, None),
                       (commonstates, is_paragraph): (make_paragraph, None),
                       (commonstates, is_bulletlist): (make_bulletlist, "bulletlist"),
                       ("bulletlist", is_paragraph): (False, None),
                       ("bulletlist", is_bulletlist): (make_listitem, None),
                       ("body", is_appendix): (make_appendix, "appendix"),
                       ("body", is_preamblesection): (make_preamblesection, "preamblesection"),
                       ("body", is_prophuvudrubrik): (make_frontmatter, "frontmatter"),
                       ("body", is_protokollsutdrag): (make_protokollsutdrag, "protokollsutdrag"),
                       ("body", is_section): (make_section, "section"),
                       ("body", is_unorderedsection): (make_unorderedsection, "unorderedsection"),
                       ("frontmatter", is_prophuvudrubrik): (make_prophuvudrubrik, None),
                       ("frontmatter", is_proprubrik): (make_proprubrik, None),
                       ("frontmatter", is_preamblesection): (False, None),
                       
                       ("preamblesection", is_preamblesection): (False, None),
                       ("preamblesection", is_forfattningsforslag): (make_forfattningsforslag, "forfattningsforslag"),
                       ("preamblesection", is_section): (False, None),
                       ("preamblesection", is_appendix): (False, None),

                       ("forfattningsforslag", is_forfattningsforslag): (False, None),
                       ("forfattningsforslag", is_section): (False, None),
                       ("forfattningsforslag", is_preamblesection): (False, None),

                       ("protokollsutdrag", is_protokollsutdrag): (False, None),
                       ("protokollsutdrag", is_appendix): (False, None),
                       ("protokollsutdrag", is_section): (make_section, "section"),
                       
                       ("section", is_section): (False, None),
                       ("section", is_subsection): (make_section, "subsection"),
                       ("section", is_unorderedsection): (make_unorderedsection, "unorderedsection"),
                       ("section", is_unorderedsubsection): (make_unorderedsection, "unorderedsubsection"),
                       ("subsection", is_section): (False, None),
                       ("subsection", is_subsection): (False, None),
                       ("subsection", is_subsubsection): (make_section, "subsubsection"),
                       ("subsubsection", is_section): (False, None),
                       ("subsubsection", is_subsection): (False, None),
                       ("subsubsection", is_subsubsection): (False, None),
                       ("unorderedsection", is_appendix): (False, None),
                       ("unorderedsection", is_preamblesection): (False, None),
                       ("unorderedsection", is_section): (False, None),
                       ("unorderedsection", is_unorderedsection): (False, None),
                       ("unorderedsection", is_unorderedsubsection): (make_unorderedsubsection, "unorderedsubsection"),
                       ("unorderedsubsection", is_appendix): (False, None),
                       ("unorderedsubsection", is_preamblesection): (False, None),
                       ("unorderedsubsection", is_section): (False, None),
                       ("unorderedsubsection", is_unorderedsection): (False, None),
                       ("unorderedsubsection", is_unorderedsubsection): (False, None),
                       (("subsubsection", "subsection", "section", "appendix"), is_preamblesection): (False, None),
                       (("subsubsection", "subsection", "section"), is_protokollsutdrag): (False, None),
                       (("appendix", "subsubsection", "subsection", "section"), is_appendix): (False, None)
                       })

    p.initial_state = "body"
    p.initial_constructor = make_body
    p.current_identifier = identifier
    # for reallly old props we set a attribute used by is_nonessential
    if identifier.startswith("Prop.") and basefile < "1972":
        year, number = basefile.split(":")
        p.current_long_identifier = "Kungl. Maj:ts proposition nr %s år %s" % (number, year)
    p.debug = bool(debug)
    return p


