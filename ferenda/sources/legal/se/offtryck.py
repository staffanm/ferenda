# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# stdlib
import os
import re
import json
import difflib
import ast
import collections

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
                              Preformatted, UnorderedList, ListItem)
from ferenda.elements.html import P
from ferenda.pdfreader import BaseTextDecoder, Page, Textbox
from ferenda.decorators import newstate
from ferenda.errors import ParseError, DocumentSkippedError

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
            assert len(y) == 4, "Basefile %s is invalid beyond sanitization" % basefile
            assert 1900 < int(y) < 2100, "Basefile %s has improbable year %s" % (basefile, y)
            sanitized = basefile
        return sanitized

    @property
    def urispace_segment(self):
        return {self.PROPOSITION: "prop",
                self.DS: "utr/ds",
                self.SOU: "utr/sou",
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
            # older native (non-scanned) pdfs from regeringen.se
            # contains very unreliable font information sometimes
            if getattr(nextbox[0], 'skippedempty', None):
                # usually within a textbox that contains some initial
                # empty italic or bold textelement. PDFReader filtes
                # out such empty elements, but tells us that we did
                # through the skippedempty attribute
                return True
            elif len(prevbox) > 1 and prevbox[0].tag == "b" and re.match("\d+(| \w) §", prevbox[0]) and nextbox[0][0].islower():
                # looks like the start of a paragraph? See if nextbox
                # looks like the continuation of a sentence (ie not
                # starting with a capital letter)
                return True
            else:
                return prevbox.font.family in ("Symbol", nextbox.font.family)
        
        def offtryck_gluefunc(textbox, nextbox, prevbox):
            # linespacing = nextbox.font.size / 2
            linespacing = nextbox.font.size / 1.2 # bboxes for scanned
                                                  # material seem very tight,
                                                  # so that lines appear to
                                                  # have greater linespacing
            parindent = nextbox.font.size
            # if we're using hOCR data, take advantage of the paragraph
            # segmentation that tesseract does through the p.ocr_par mechanism
            if (hasattr(prevbox, 'parid') and hasattr(nextbox, 'parid') and
                prevbox.parid == nextbox.parid):
                return True
            strtextbox = str(textbox).strip()
            strprevbox = str(prevbox).strip()
            strnextbox = str(nextbox).strip()
            #if strprevbox == "1 Förslag":
            #    from pudb import set_trace; set_trace()
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
            if strtextbox.startswith(("\u2022", "\uf0b7", "−")) and strnextbox[0].islower():
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
                    return True

            # Any line that ONLY contains a section reference should probably
            # be interpreted as a header
            if re.match("(\d+ kap. |)\d+( \w|) §$", strprevbox) and prevbox.bottom <= nextbox.top:
                return False

            # these text locutions indicate a new paragraph (normally, this is
            # also catched by the conditions below, but if prevbox is unusally
            # short (one line) they might not catch it.:
            if re.match("Skälen för (min bedömning|mitt förslag): ", strnextbox):
                return False
            if re.match("\d\. +", strnextbox):  # item in ordered list
                return False
            if (re.match("\d+ §", strnextbox) and
                 (strprevbox[-1] not in ("–", "-") and # make sure this isn't really a continuation
                  not strprevbox.endswith("och") and
                  not strprevbox.endswith("enligt") and
                  not strprevbox.endswith("kap.") and
                  not strprevbox.endswith("lagens")   # OK this is getting ridiculous
                 )and
                (nextbox.top - prevbox.bottom >= (prevbox.font.size * 0.3))):  # new section (with a suitable linespacing (30% of a line))
                return False
            # These final conditions glue primarily *horizontally*
            if (sizematch(textbox, nextbox) and
                familymatch(textbox, nextbox) and
                textbox.top + textbox.height + linespacing > nextbox.top and
                (prevbox.left < nextbox.right or
                 textbox.left < parindent * 2 + nextbox.left) and
                (valignmatch(prevbox, nextbox) or  # compare baseline, not topline
                 alignmatch(prevbox, nextbox) or # compare previous line to next
                 alignmatch(textbox, nextbox) or # compare entire glued box so far to next
                 (parindent * 2 >= (prevbox.left - nextbox.left) >= parindent) or
                 (parindent * 2 >= (textbox.left - nextbox.left) >= parindent) or
                 (re.match(r"\d\s+[A-ZÅÄÖ]", strtextbox) and nextbox.left - textbox.left < parindent * 4) # hanging indent (numbered) heading
                 )):
                # if the two boxes are on the same line, but have a
                # wide space between them, the nextbox is probably a
                # pagenumber
                if (valignmatch(prevbox, nextbox) and
                    (nextbox.left - textbox.right) > 50 and
                    len(strnextbox) < 10):
                    return False
                return True

        return offtryck_gluefunc

    @cached_property
    def parse_options(self):
        # we use a file with python literals rather than json because
        # comments
        if self.resourceloader.exists("options/options.py"):
            with self.resourceloader.open("options/options.py") as fp:
                return ast.literal_eval(fp.read())
        else:
            return {}
    
    def get_parse_options(self, basefile):
        return self.parse_options.get((self.urispace_segment, basefile), None)
    

    def parse_body(self, fp, basefile):
        # this version of parse_body knows how to:
        #
        # - look up document-specific options (eg "skip",
        #   "metadataonly", "plainparse") from the resource file
        #   res/options/options.py (used to blacklist old files with
        #   no relevance today, or handle otherwise difficult
        #   documents.
        # - use an appropriate analyzer to segment documents into
        #   subdocs and use the appropritate parsing method on each
        #   subdoc. NOTE: this requires that sanitize_body has set up
        #   a PDFAnalyzer subclass instance as a property on the
        #   sanitized object (normally a PDFReader or
        #   StreamingPDFReader)
        # - handle the case when a document is not available as a PDF,
        #   only in simple HTML/plaintext, and use a simpler parser
        options = self.get_parse_options(basefile)
        if options == "skip":
            raise DocumentSkippedError("%s: Skipped because of options.py" % basefile,
                                       dummyfile=self.store.parsed_path(basefile))
        elif options == "metadataonly":
            return Preformatted("Dokumentttext saknas (se originaldokument)")
        # elif options == "simple":
        #     do something else smart

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
                            body = self.refparser.parse_recursive(body)
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
                            sb = Sidbrytning(ordinal=initialstate['pageno']+relidx,
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
                        initialstate['pageno'] = lastpagebreak.ordinal + 1
                    allbody += body[:]
                self.validate_body(allbody, basefile)  # Throws exception if invalid
                return allbody
            except Exception as e:
                errmsg = str(e)
                loc = util.location_exception(e)
                self.log.warning("%s: Parsing with config '%s' failed: %s (%s)" %
                                 (basefile, parseconfig, errmsg, loc))
                lastexception = e
                # "reset" the sanatized body since the parsing process might have mutated it
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
                sanitized[idx].src = "%s/sid%s.png" % (basefile, idx+1)
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
                idx = filemapping[(pdffile, pp)]
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
                    _check_differing(
                        d,
                        self.ns['dcterms'].identifier,
                        "Prop. " +
                        m.group(1))
                    identifier_found = True

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
            state['kommittensbetankande'] = m.group(1)
        else:
            self.log.warning("Could not find reference to kommmittens betankande")
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
        commentary = []
        # parser = SwedishLegalSource.forfattningskommentar_parser()
        for subsection in node:
            if hasattr(subsection, 'title'):
                # find out which laws this proposition proposes to
                # change (can be new or existing)
                if re.match("Förslag(|et) (till lag om|om lag till) ändring i", subsection.title):
                    uri = self._parse_uri_from_text(subsection.title, state['basefile'])
                    lawname = subsection.title.split(" ", 6)[-1]
                elif re.match("Förslag(|et) till", subsection.title):
                    # create a reference that could pass for a real
                    # SFS-id, but with the name (the only identifying
                    # information we have at this point) encoded into
                    # it. FIXME: the numslug could be shorter if we'd
                    # make sure to only allow lower-case a-z and to a
                    # base26 conversion into an integer
                    lawname = subsection.title.split(" ", 2)[-1]
                    uri = self.temp_sfs_uri(lawname)
                else:
                    uri = None
                if uri:
                    commentary.append((uri, lawname, subsection))
                    
        if commentary == []:  # no subsecs, ie the prop changes a single law
            if 'primarylaw' in state:
                commentary.append((state['primarylaw'], state['primarylawname'], node))
            else:
                self.log.warning("%s: Författningskommentar does not specify name of law and find_primary_law didn't find it either" % state['basefile'])
        for law, lawname, section in commentary:
            textnodes = self._find_commentary_for_law(law, section, state, lawname)
            # this is kinda risky but wth...
            section[:] = textnodes[:]


    def _find_commentary_for_law(self, law, section, state, lawname):
        # FIXME: this is basically a ad-hoc statemachine, with a lot
        # of ill-understood conditionals and flag settings. Luckily
        # there's a decent test harness in the
        # functionalSources.TestPropRegeringen suite
        textnodes = []
        reexamine_state = False
        comment_on = None
        skipheader = False  # whether we should skip adding a subnode
                            # to current_comment since it's only a
                            # header (eg "53 §")
        current_comment = None
        comment_start = False
        parsestate = "commenttext"
        prevnode = None
        # self.log.debug("Finding commentary for %s" % law)
        for idx, subnode in enumerate(section):
            if not isinstance(subnode, (Textbox, Sidbrytning, UnorderedList)):
                raise ValueError("_find_commentary_for_law: Got a %s instead of a Textbox/Sidbrytning/UnorderedList, this indicates broken parsing" % type(subnode))
            text = str(subnode).strip()
            # self.log.debug("Examining %s..." % text[:60])
            if reexamine_state:  # meaning the previous node was
                                 # on the previous page, so any
                                 # text gap that might have
                                 # signalled a change from acttext
                                 # to commenttext was lost.
                prev_state = parsestate
                # indicates section starting with eg "<i>Första
                # stycket</i> innehåller..." FIXME: this should be
                # detected by self._is_commentstart now.
                if isinstance(subnode, Textbox) and hasattr(subnode, '__getitem__') and (subnode[0].tag == "i"):
                    parsestate = "commenttext"
                elif self._is_headerlike(text):
                    parsestate = "acttext"
                elif re.match("\d+(| \w) §", text) and not self._is_commentstart(str(section[idx+1])):
                    parsestate = "acttext"
                elif self._is_commentstart(text):
                    parsestate = "commenttext"
                else:
                    pass  # keep parsestate as-is
                if prev_state == "acttext" and parsestate == "commenttext":
                    comment_start = True

                # Calculating linespacing easily gives false positives
                # (eg first para of prop 1997/98:44 p 116, which
                # misidentifies as acttext due to strangely low
                # linespacing.
#                 if subnode.lines > 1:
#                     horizontal_scale = 2 / 3
#                     linespacing = ((subnode.height - (subnode.font.size/horizontal_scale)) /
#                                    (subnode.lines - 1)) / subnode.font.size
#                     if linespacing > 1.0:
#                         parsestate = "commenttext"
#                     else:
#                         parsestate = "acttext"
#                 else:  # probably a header, which is part of the acttext
#                     parsestate = "acttext"
                # self.log.debug("...Reexamination gives parsestate %s" % parsestate)
                reexamine_state = False

            if isinstance(subnode, (Page, Sidbrytning)):
                # self.log.debug("...Setting reexamine_state flag")
                reexamine_state = True

            elif len(text) < 20 and (text.endswith(" kap.") or text.endswith(" kap")):
                # subsection heading indicating the start of a new
                # chapter. alter the parsing context from law to
                # chapter in law
                # self.log.debug("...detecting chapter header w/o acttext")
                newlaw = self._parse_uri_from_text(text, state['basefile'], law)
                if newlaw:
                    law = newlaw
                skipheader = True
                textnodes.append(subnode)
                subnode = None
                reftext = text
                
            elif len(text) < 20 and text.endswith("§"):
                # self.log.debug("...detecting section header w/o acttext")
                comment_on = self._parse_uri_from_text(text, state['basefile'], law)
                skipheader = True
                offset = 1
                reftext = text
                while isinstance(section[idx+offset], Sidbrytning):
                    offset += 1
                if state['defaultsize'] >= section[idx+offset].font.size + 2:
                    parsestate = "acttext"
                    comment_start = False
                    skipheader = False
                else:
                    comment_start = True

            elif re.match("\d+ kap. +[^\d]", text):  # eg "4 kap. Om domare"
                # self.log.debug("...detecting chapter header with title, no section")
                newlaw = self._parse_uri_from_text(text, state['basefile'], law)
                if newlaw:
                    law = newlaw
                skipheader = True  # really depends on whether the _next_ subnode is acttext or not 
                textnodes.append(subnode)
                parsestate = "acttext"
                subnode = None
                
            elif re.match("\d+(| \w) §", text):
                # self.log.debug("...detecting section header with acttext")
                reftext = text[:text.index("§")+ 1]
                comment_on = self._parse_uri_from_text(reftext, state['basefile'], law)
                comment_start = False
                parsestate = "acttext"
                skipheader = False

            # any big space signals a switch from acttext ->
            # commenttext or vice versa (if some other obscure
            # conditions are met). The height of the gap should really
            # be dynamically calculated, but how?
            elif (prevnode and
                  # hasattr(subnode, 'top'
                  subnode.top - prevnode.bottom >= 20):
                # self.log.debug("...node spacing is %s, switching from parsestate %s" % (subnode.top - prevnode.bottom, parsestate))
                if (re.match("\d+(| \w) §$", str(prevnode).strip())):
                    comment_start = True
                    parsestate == "commenttext"
                elif self._is_headerlike(text) or parsestate == "commenttext":
                    if current_comment is not None and len(current_comment) == 0:
                        # this means we created a
                        # Forfattningskommentar and then never added
                        # any text to it. Since we're switching into
                        # acttext state, replace that object with just
                        # the title
                        comment_on = current_comment.comment_on
                        assert current_comment.title, "Expected current_comment to have a .title"
                        titlenode = P([current_comment.title])
                        if current_comment in textnodes:
                            textnodes[textnodes.index(current_comment)] = titlenode
                            del state['commented_paras'][comment_on]
                        else:
                            self.log.warning("Failed to replace Forfattningskommentar for %s failed" %
                                             (current_comment.comment_on))
                    parsestate = "acttext"
                elif parsestate == "acttext":
                    parsestate = "commenttext"
                    skipheader = False
                    comment_start = True
                # self.log.debug("...new parsestate is %s" % parsestate)

            # FIXME: This gives too many false positives right now --
            # need to check distance to prevbox and/or nextbox. Once
            # header detection works better we can enable it
            # everywhere, not just at the start of the commentary for
            # this act.
            elif current_comment is None and self._is_headerlike(text):  
                # self.log.debug("...seems like a header part of acttext")
                parsestate = "acttext"

            elif state['defaultsize'] >= subnode.font.size + 2:
                # self.log.debug("... set in smallfont, probably acttext, maybe following an sectionheader w/o actttext")  # see prop 2005/06:180 p 62
                parsestate = "acttext"
            else:
                # self.log.debug("...will just keep on (parsestate %s)" % parsestate)
                pass

            # if comment_on and parsestate == "commenttext":
            if comment_start:
                # self.log.debug("Starting new Forfattningskommentar for %s" % comment_on)
                # OK, a new comment. Let's record which page we found it on
                page = self._find_subnode(section[idx:], Sidbrytning, reverse=False)
                if page:
                    pageno = page.ordinal - 1 
                else:
                    pageno = None
                if comment_on not in state['commented_paras']:
                    if not skipheader:  # means we have a section header
                                        # with acttext. that acttext
                                        # should already have been added
                                        # to textnodes, so current subnode
                                        # must contain first box of the
                                        # comment
                        title = ""
                    else:
                        title = text
                    if comment_on:
                        current_comment = Forfattningskommentar(title=title,
                                                                comment_on=comment_on,
                                                                uri=None,
                                                                label="Författningskommentar till %s %s" % (reftext, lawname))
                        if parsestate != "commenttext":
                            self.log.debug("%s, comment on %s, parsestate was '%s', "
                                           "setting to 'commenttext'" %
                                           (state['basefile'], comment_on, parsestate))
                            parsestate = "commenttext"
                        # the URI to the above Forfattningskommentar is
                        # dynamically constructed in
                        # Forfattningskommentar.as_xhtml
                        textnodes.append(current_comment)
                        state['commented_paras'][comment_on] = pageno
                else:
                    self.log.warning("Dupe comment on %s at p %s (previous at %s), ignoring" % (comment_on, pageno, state['commented_paras'][comment_on]))
                comment_on = None
                comment_start = False

            if parsestate == "commenttext":
                if subnode:
                    if current_comment is None:
                        # self.log.debug("...creating
                        # Forfattningskommentar for law itself")
                        current_comment = Forfattningskommentar(title="",
                                                                comment_on=law,
                                                                uri=None,
                                                                label="Författningskommentar till %s" % lawname)
                        textnodes.append(current_comment)
                    if not skipheader:
                        current_comment.append(subnode)
                    else:
                        skipheader = False

            else:
                if subnode:
                    textnodes.append(subnode)
                    
            if isinstance(subnode, (Page, Sidbrytning)):
                prevnode = None
            else:
                prevnode = subnode
        return textnodes


    def _is_headerlike(self, text):
        # headers are less than 100 chars and do not end with a period
        # or other non-hederish thing
        return (len(text) < 100 and
                (len(text) < 2 or
                 (text[-1] not in  (".", ")") and text[-2:] not in (" i", " §"))))


    def _is_commentstart(self, text):
        if re.match("(Av p|P)aragrafen (framgår|innehåller|har behandlats|är ny|, som är ny|avgränsar|innebär)", text):
            return True
        elif re.match("(I f|F)örsta stycket", text):
            return True
        elif re.match("\((Jfr|Paragrafen)", text):
            return True
        return False
    
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
        if len(links) != 1:
            self.log.warning("%s: _parse_uri_from_text found %s links in '%s',"
                             "expected single link" %
                             (basefile, len(links), text))
            return None
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
                             'Förkortningar'
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
            return (chunk.font.size == metrics.h1.size and (txt.startswith("Bilaga ") or txt.startswith("Bilagor")))
        def is_implicit_appendix(chunk):
            # The technique of starting a new appendix without stating
            # so in the margin on the first page of the appendix
            # occurs in some older props, eg Prop 1997/98:18
            txt = str(chunk).strip()
            if chunk.font.size == metrics.h1.size:
                if txt in ("Promemorians lagförslag", "Lagrådsremissens lagförslag", "Lagrådets yttrande"):
                    return True
                elif txt.startswith("Förteckning över remissinstanser"):
                    return True
                return False

        chunk = parser.reader.peek()
        txtchunk = util.normalize_space(str(chunk))
        if ".." in txtchunk:  # probably a line in a TOC
            return False
        if len(txtchunk) > 100:  # sanity check -- should normally be a lot less
            return False
        if not state.appendixstarted:
            if is_appendix_header(chunk):
                return True
            elif is_implicit_appendix(chunk):
                return True

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
        while not done:
            if isinstance(chunk, Page):
                title = ""
                done = True
            if isinstance(chunk, Textbox) and int(chunk.font.size) >= metrics.h2.size:
                title = str(chunk).strip()
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
        strchunk = str(chunk)
        # 1. clean up indata
        if metrics.scanned_source:
            if strchunk.startswith("l "): # probable OCR mistake
                strchunk = "1" + strchunk[1:]
            # "3. 12" -> "3.12" FIXME: Generalize to handle phantom
            # spaces in other places (3- or 4 level section headings)
            strchunk = re.sub("(\d+)\.\s+(\d+)", r"\1.\2", strchunk)

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
        if ((strchunk.endswith(".") and not 
             (strchunk.endswith("m.m.") or
              strchunk.endswith("m. m.") or
              strchunk.endswith("m.fl.") or
              strchunk.endswith("m. fl."))) or
            strchunk.endswith(",") or
            strchunk.endswith("och") or
            strchunk.endswith("eller") or
            strchunk.endswith(":") or
            strchunk.endswith("-")):
            # sections doesn't end like that
            return (None, None, chunk)
        # looks like we've made it!
        return (ordinal, headingtype, title)


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


