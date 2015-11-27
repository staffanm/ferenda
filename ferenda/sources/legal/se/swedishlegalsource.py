# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# Intermediate base class containing some small functionality useful
# for handling data sources of swedish law.

from datetime import datetime, date
import re
import os
import itertools
import logging
import warnings
from bz2 import BZ2File

from layeredconfig import LayeredConfig, Defaults
from rdflib import URIRef, RDF, Namespace, Literal, Graph, BNode
from rdflib.resource import Resource
from rdflib.namespace import DCTERMS, SKOS, FOAF
from six import text_type as str
import bs4
from cached_property import cached_property

from ferenda import (DocumentRepository, DocumentStore, FSMParser,
                     CitationParser, Describer)
from ferenda import util
from ferenda.sources.legal.se.legalref import Link, LegalRef, RefParseError
from ferenda.elements.html import A, H1, H2, H3
from ferenda.elements import (Paragraph, Section, Body,
                              OrdinalElement, CompoundElement,
                              SectionalElement)
from ferenda.pdfreader import Page
from ferenda.pdfreader import PDFReader, StreamingPDFReader
from ferenda.pdfanalyze import PDFAnalyzer
from ferenda.decorators import action, managedparsing
from ferenda.thirdparty.coin import URIMinter
from ferenda.elements.elements import E
from . import RPUBL
from .elements import *
PROV = Namespace(util.ns['prov'])

class SwedishLegalStore(DocumentStore):

    """Customized DocumentStore that better handles some pecularities in
    swedish legal document naming."""

    def basefile_to_pathfrag(self, basefile):
        # "2012/13:152" => "2012-13/152"
        # "2012:152"    => "2012/152"
        return basefile.replace("/", "-").replace(":", "/")

    def pathfrag_to_basefile(self, pathfrag):
        # "2012-13/152" => "2012/13:152"
        # "2012/152"    => "2012:152"
        return pathfrag.replace("/", ":").replace("-", "/")

    def intermediate_path(self, basefile, version=None, attachment=None):
        return self.path(basefile, "intermediate", ".xml", version=version,
                         attachment=attachment)


class SwedishLegalSource(DocumentRepository):
    documentstore_class = SwedishLegalStore
    namespaces = ['rdf', 'rdfs', 'xsd', 'dcterms', 'skos', 'foaf',
                  'xhv', 'xsi', 'owl', 'prov', 'bibo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]

    alias = "swedishlegalsource"

    lang = "sv"

    rdf_type = RPUBL.Rattsinformationsdokument  # subclasses override this

    parse_types = LegalRef.RATTSFALL, LegalRef.LAGRUM, LegalRef.FORARBETEN
    parse_allow_relative = False

    # This is according to the RPUBL vocabulary: All
    # rpubl:Rattsinformationsdokument should have dcterms:title,
    # dcterms:issued (must be a xsd:date), dcterms:publisher and
    # dcterms:identifier
    required_predicates = [RDF.type, DCTERMS.title, DCTERMS.issued,
                           DCTERMS.identifier, PROV.wasGeneratedBy]

    swedish_months = {"januari": 1,
                      "jan": 1,
                      "februari": 2,
                      "feb": 2,
                      "febr": 2,
                      "mars": 3,
                      "mar": 3,
                      "april": 4,
                      "apr": 4,
                      "maj": 5,
                      "juni": 6,
                      "jun": 6,
                      "juli": 7,
                      "jul": 7,
                      "augusti": 8,
                      "aug": 8,
                      "september": 9,
                      "sep": 9,
                      "sept": 9,
                      "oktober": 10,
                      "okt": 10,
                      "november": 11,
                      "nov": 11,
                      "december": 12,
                      "dec": 12,
                      "\xe5r": 12}

    def __init__(self, config=None, **kwargs):
        super(SwedishLegalSource, self).__init__(config, **kwargs)
        if not isinstance(self, SwedishLegalSource):
            assert self.alias != "swedishlegalsource", "Subclasses must override self.alias!"

    @cached_property
    def minter(self):
        # print("%s (%s) loading minter" % (self.alias, id(self)))
        filename = self.resourceloader.filename
        spacefile = filename("uri/swedishlegalsource.space.ttl")
        slugsfile = filename("uri/swedishlegalsource.slugs.ttl")
        self.log.debug("Loading URISpace from %s" % spacefile)
        cfg = Graph().parse(spacefile,
                            format="turtle").parse(slugsfile,
                                                   format="turtle")
        COIN = Namespace("http://purl.org/court/def/2009/coin#")
        # select correct URI for the URISpace definition by
        # finding a single coin:URISpace object
        spaceuri = cfg.value(predicate=RDF.type, object=COIN.URISpace)
        return URIMinter(cfg, spaceuri)

    @property
    def urispace_base(self):
        return self.minter.space.base

    @property
    def urispace_segment(self):
        return self.alias
        
    @classmethod
    def get_default_options(cls):
        opts = super(SwedishLegalSource, cls).get_default_options()
        opts['pdfimages'] = False
        opts['parserefs'] = True
        return opts

    def lookup_label(self, resource, predicate=FOAF.name):
        """The inverse of
        :py:meth:`~ferenda.DocumentRepository.lookup_resource `.

        """
        val = self.commondata.value(subject=URIRef(resource),
                                    predicate=predicate)
        if not val:
            raise KeyError(resource)
        else:
            return str(val)

    def attributes_to_resource(self, attributes, infer_nodes=True):
        """Given a dict of metadata attributes for a document or
        fragment, create a RDF resource for that same thing. The RDF
        graph may contain multiple nodes if the thing is a document
        fragment, in which case the root document and possibly other
        containing fragments will be present as nodes.
        
        if the values of the dict are rdflib.Identifier-derived objects,
        they will be put into the RDF graph as-is. If they're string
        literals, they're converted to rdflib.Literal
        
        The resource being returned (as well as all other nodes in the RDF
        graph will be a BNode, i.e. this method does not coin URIs
        
        :param attributes: document/fragment metadata where keys are
                           CURIE strings and values are either plain
                           strings or rdflib.term.Identifier objects
        :type attributes: dict
        :param infer_nodes: For certain attributes (pinpoint reference
                            fragments and consolidated legal acts),
                            create multiple nodes and infer
                            relationships between them.  This is
                            needed for some of our URI minting rules
                            as expressed by COIN.
        :type infer_nodes: bool
        :returns: The metadata in RDF form
        :rtype: rdflib.Resource

        """
        # FIXME: this is roughly the same code as
        # LegalRef.attributes_to_resource but with different keys.
        def uri(qname):
            (prefix, leaf) = qname.split(":", 1)
            return self.ns[prefix][leaf]

        g = self.make_graph()
        b = BNode()
        current = b
        attributes = dict(attributes)
        # create needed sub-nodes. FIXME: this includes multiple
        # rinfoex values -- these should be in a derivec lagen.nu
        # class. Maybe using similar approach as
        # SFS.ordinalpredicates?'
        if infer_nodes:
            for k in ("rinfoex:meningnummer", "rinfoex:subsubpunktnummer",
                      "rinfoex:subpunktnummer", "rinfoex:punktnummer",
                      "rinfoex:styckenummer", "rpubl:paragrafnummer",
                      "rinfoex:rubriknummer", "rpubl:kapitelnummer",
                      "rinfoex:avdelningnummer",
                      "rinfoex:bilaganummer", "rinfoex:andringsforfattningnummer"):
                if k in attributes:
                    p = uri(k)
                    g.add((current, p, Literal(attributes[k])))
                    del attributes[k]
                    new = BNode()
                    if p.endswith("nummer"):
                        rel = URIRef(str(p).replace("nummer", ""))
                    g.add((new, rel, current))
                    current = new

        # specifically for rpubl:KonsolideradGrundforfattning, create
        # relToBase things
        if (infer_nodes and
            not isinstance(self.rdf_type, (tuple, list)) and
            self.rdf_type.endswith("KonsolideradGrundforfattning") and
            "dcterms:issued" in attributes):
            rel = RPUBL.konsoliderar
            new = BNode()  # the document
            g.add((current, DCTERMS.issued,
                   Literal(attributes["dcterms:issued"])))
            del attributes["dcterms:issued"]
            g.add((current, rel, new))
            current = new

        for k, values in attributes.items():
            if ":" not in k:
                continue
            if not isinstance(values, list):
                values = [values]
            for v in values:
                if isinstance(v, Resource):
                    assert isinstance(k, URIRef)
                    if isinstance(v.identifier, BNode):
                        for p, o in v.graph.predicate_objects():
                            g.add((k, p, o))
                    else:
                        g += v.graph
                else:
                    if not isinstance(v, (URIRef, Literal)):
                        # self.log.warning("attributes_to_resources recieved "
                        #                  "naked str %s for %s, should be "
                        #                  "Literal or URIRef" % (v, k))
                        v = Literal(v)
                    g.add((current, uri(k), v))
        return g.resource(b)

    def canonical_uri(self, basefile):
        attrib = self.metadata_from_basefile(basefile)
        resource = self.attributes_to_resource(attrib)
        uri = self.minter.space.coin_uri(resource)
        # FIXME: temporary code we use while we get basefile_from_uri to work
        computed_basefile = self.basefile_from_uri(uri)
        assert basefile == computed_basefile, "%s -> %s -> %s" % (basefile, uri, computed_basefile)
        # end temporary code
        return uri

    def metadata_from_basefile(self, basefile):
        """Create a metadata dict with whatever we can infer from a document
        basefile. The dict can be passed to
        py:method:`attributes_to_resource`.

        This method is intended to be overridden by every docrepo that has
        a clear transformation rule for metadata <-> basefile.

        :param basefile: The doc we want to create metadata for
        :type basefile: str
        :returns: inferred metadata.
        :rtype: dict

        """

        attribs = {'prov:wasGeneratedBy': self.qualified_class_name()}
        if isinstance(self.rdf_type, URIRef):
            attribs['rdf:type'] = self.rdf_type
        return attribs

    def sanitize_basefile(self, basefile):
        """Given a basefile (typically during the download stage), 
        make sure it's consistent with whatever rules the repo has 
        for basefile naming, and sanitize it if it's not proper but
        still possible to guess what it should be. 
        
        Sanitazion rules may include things like converting 
        two-digit-years to four digits, removing or adding leading zeroes,
        case folding etc.
        
        Intended  to be overridden by every docrepo that has
        rules for basefiles. The default implementation returns the basefile 
        unchanged.
        
        :param basefile: The basefile to sanitize
        :type basefile: str
        :return: the sanitized basefile
        :rtype: str
        
        """
        # will primarily be used by download to normalize eg "2014:04"
        # to "2014:4" and similar Regeringen.download_get_basefiles
        # line 188- should call this method (and
        # .download_get_basefiles in general probably)
        return basefile

    def basefile_from_uri(self, uri):
        # does a very simple transform. subclasses with specic needs
        # can override and sanitize after the fact.
        #
        # examples:
        # "https://lagen.nu/prop/1999/2000:35" => "1999/2000:35"
        # "https://lagen.nu/rf/hfd/2013/not/12" => "hfd/2013/not/12"
        # "https://lagen.nu/sosfs/2015:10" => "2015:10"
        # "https://lagen.nu/sfs/2013:1127/konsolidering/2014:117" => "2013:1127/konsolidering/2014:117"
        base = self.urispace_base
        # FIXME: when doing canonical uris we should add "publ/" to
        # self.urispace_segment (or something similar)
        if uri.startswith(base) and uri[len(base)+1:].startswith(self.urispace_segment):
            offset = 2 if self.urispace_segment else 1
            return uri[len(base) + len(self.urispace_segment) + offset:]

    @action
    @managedparsing
    def parse(self, doc):
        """Parse downloaded documents into structured XML and RDF.
        
        This overrides :py:method:`ferenda.DocumentRepository.parse`
        and replaces it with a fine-grained structure of methods,
        which are intended to be overridden by subclasses as
        needed. The principal call chain looks like this::
        
        parse(doc) -> bool
        parse_open(basefile) -> file
            downloaded_to_intermediate(basefile) -> file
            patch_if_needed(file) -> file
        parse_metadata(file, basefile) -> rdflib.Resource
            extract_head(file, basefile) -> object
            extract_metadata(object, basefile) -> dict
                [metadata_from_basefile(basefile) -> dict]
            sanitize_metadata(dict, basefile) -> dict
                sanitize_identifier(str) -> str
            polish_metadata(dict) -> rdflib.Resource
                attributes_to_resource(dict) -> rdflib.Resource
            infer_metadata(rdflib.Resource, basefile) -> rdflib.Resource
                infer_identifier(basefile) -> str
        parse_body(file, basefile) -> elements.Body
            extract_body(file, basefile) -> object
            sanitize_body(object) -> object
            get_parser(basefile) -> callable
            tokenize(object) -> iterable
            callable(iterable) -> elements.Body
            visitor_functions(basefile) -> callables
            visit_node(elements.Body, callable, state) -> state
                callable(elements.CompoundElement, state) -> state
        postprocess_doc(doc)
        parse_entry_update(doc)

        :param doc: The document object to fill in.
        :type  doc: ferenda.Document

        """
        # reset global state
        UnorderedSection.counter = 0
        fp = self.parse_open(doc.basefile)
        resource = self.parse_metadata(fp, doc.basefile)
        doc.meta = resource.graph
        doc.uri = str(resource.identifier)
        if resource.value(DCTERMS.title):
            doc.lang = resource.value(DCTERMS.title).language
        doc.body = self.parse_body(fp, doc.basefile)
        self.postprocess_doc(doc)
        self.parse_entry_update(doc)
        return True

    def parse_open(self, basefile, attachment=None):
        """Open the main downloaded file for the given basefile, caching the
        contents to an intermediate representation if applicable (or
        reading from that cache if that's ok), and patching the file
        transparently if needed.

        :param basefile: The basefile to open
        :return: an open file object from which the document can be read

        """
        # 1. check if intermediate_path exists
        intermediate_path = self.store.intermediate_path(basefile)
        # FIXME: This name mangling should be done by
        # FixedLayoutSource somehow. However, the API for
        # StreamingPDFReader should first be adapted so that
        # intermediate_file is specified (maybe alongside of workdir).
        if self.config.compress == "bz2":
            intermediate_path += ".bz2"
            opener = BZ2File
        else:
            opener = open
        if not os.path.exists(intermediate_path):
            # 2. if not, call code
            #    parse_convert_to_intermediate(basefile) to convert
            #    downloaded_path -> intermediate_path (eg.
            #    WordReader.read, SFS.extract_sfst)
            fp = self.downloaded_to_intermediate(basefile)
        else:
            # 3. recieve intermediate_path as open file (binary?)
            fp = opener(intermediate_path, "rb")
        # 4. call patch_if_needed, recieve as open file (binary?)
        return self.patch_if_needed(fp, basefile)

    def patch_if_needed(self, fp, basefile):
        """Override of DocumentRepository.patch_if_needed with different,
        streamier API."""
        
        # 1. do we have a patch?
        patchstore = self.documentstore_class(self.config.patchdir +
                                              os.sep + self.alias)
        patchpath = patchstore.path(basefile, "patches", ".patch")
        descpath = patchstore.path(basefile, "patches", ".desc")
        if not os.path.exists(patchpath):
            return fp
        from patchit import PatchSet
        with open(patchpath, 'r') as pfp:
            # this might raise a PatchSyntaxError
            ps = PatchSet.from_stream(pfp)
        assert len(ps.patches) == 1
        stream = ps.patches[0].merge(fp)
        return stream

    def downloaded_to_intermediate(self, basefile):
        """Given a basefile, convert the corresponding downloaded file 
        into some suitable intermediate format and returns an open file
        to that intermediate format (if any).
        
        The default implementation does not do any conversation, simply
        opens downloaded_path. Any source that actually uses
        intermediate files should override this.
        
        """
        return open(self.store.downloaded_path(basefile))

    def parse_metadata(self, fp, basefile):
        """Given a open file containing raw document content (or intermediate
        content), return a rdflib.Resource object containing all metadata
        about the object."""
        rawhead = self.extract_head(fp, basefile)
        attribs = self.extract_metadata(rawhead, basefile)
        sane_attribs = self.sanitize_metadata(attribs, basefile)
        resource = self.polish_metadata(sane_attribs)
        self.infer_metadata(resource, basefile)
        return resource

    def extract_head(self, fp, basefile):
        """Given a open file containing raw document content (or intermediate
        content), return the parts of that document that contains
        document metadata, in some raw form that extract_metadata can
        digest."""
        soup = bs4.BeautifulSoup(fp.read(), "lxml")
        return soup.head

    def extract_metadata(self, rawhead, basefile):
        """Given the document metadata returned by extract_head, extract all
        metadata about the document as such in a flat dict where keys are
        CURIEs and values are strings (or possibly a list of strings)."""
        attribs = self.metadata_from_basefile(basefile)
        if (isinstance(rawhead, bs4.BeautifulSoup) and
            'dcterms:title' not in attribs):
            attribs["dcterms:title"] = soup.find("title").string,

    def sanitize_metadata(self, attribs, basefile):
        """Given a dict with unprocessed metadata, run various sanitizing
        checks on the content and return a sane version.

        """
        if 'dcterms:identifier' in attribs:
            attribs['dcterms:identifier'] = self.sanitize_identifier(
                attribs['dcterms:identifier'])
        return attribs

    def sanitize_identifier(self, identifier):
        """Given the unprocessed dcterms:identifier for a document, return a
        sane version of the same.

        """
        # docrepos with unclean data might override this
        return identifier

    def polish_metadata(self, attribs, infer_nodes=True):
        """Given a sanitized flat dict of metadata for a document, return a
        rdflib.Resource version of the same. 

        """ 
        # even though our attributes are sanitized, plain-str objects
        # might need conversion (language-tagged literals, typed
        # literals, lookups from a label to a URIRef...)
        for k in attribs:
            islist = isinstance(attribs[k], (list, tuple))
            if islist:
                values = attribs[k]
            else:
                values = [attribs[k]]
            if not type(values[0]) == str:
                continue
            result = []
            for value in values:
                if k in ("dcterms:title", "dcterms:abstract"):
                    result.append(Literal(value, lang=self.lang))
                elif k in ("dcterms:issued", "rpubl:avgorandedatum",
                           "rpubl:utfardandedatum", "rpubl:ikrafttradandedatum"):
                    if re.match("\d{4}-\d{2}-\d{2}", value):
                        # iso8859-1 date (no time portion)
                        dt = datetime.strptime(value, "%Y-%m-%d")
                        result.append(Literal(date(dt.year, dt.month, dt.day)))
                    else:
                        try:
                            # assume something that parse_swedish_date handles
                            dt = self.parse_swedish_date(value)
                            result.append(Literal(dt))
                        except ValueError:
                            # parse_swedish_date failed, pass as-is
                            result.append(Literal(value))
                elif k in ("rpubl:forarbete", "rpubl:genomforDirektiv",
                           "rpubl:ersatter", "rpubl:upphaver", "rpubl:inforsI"):
                    result.append(URIRef(value))
                elif k in ("dcterms:creator", "dcterms:publisher",
                           "rpubl:beslutadAv", "rpubl:departement"):
                    result.append(self.lookup_resource(value))
                elif k in ("rpubl:forfattningssamling"):
                    result.append(self.lookup_resource(value, SKOS.altLabel))
                else:
                    # the default: just create a plain string literal
                    result.append(Literal(value))
            if islist:
                attribs[k] = result
            else:
                assert len(result) == 1, "attribs[%s] returned %s results" % (k, len(result))
                attribs[k] = result[0]

        resource = self.attributes_to_resource(attribs, infer_nodes=infer_nodes)
        uri = URIRef(self.minter.space.coin_uri(resource))
        # now that we know the document URI (didn't we already know it
        # from canonical_uri?), we should somehow replace
        # resource.identifier (a BNODE) with uri (a URIRef) in the
        # whole graph.
        for (p, o) in list(resource.graph.predicate_objects(
                resource.identifier)):
            resource.graph.remove((resource.identifier, p, o))
            resource.graph.add((uri, p, o))
        return resource.graph.resource(uri)

    def visitor_functions(self, basefile):
        """Returns a list of (callables, initialstate) tuples that can operate
        on a single document node and a (function-dependent) state
        object. These functions are automatically run on each document
        node, and can be used eg. to find references, tidy up things,
        and so on.

        """
        return []

    def parse_body(self, fp, basefile):
        """Given a open file containing raw document content (or intermediate
        content), return a ferenda.elements.Body object containing a structured
        version of the document text.
        """
        rawbody = self.extract_body(fp, basefile)
        sanitized = self.sanitize_body(rawbody)
        parser = self.get_parser(basefile, sanitized)
        tokenstream = self.tokenize(sanitized)
        # for PDFs, pdfreader.textboxes(gluefunc) is a tokenizer
        body = parser(tokenstream)
        for func, initialstate in self.visitor_functions(basefile):
            # could be functions for assigning URIs to particular
            # nodes, extracting keywords from text etc. Note: finding
            # references in text with LegalRef is done afterwards
            self.visit_node(body, func, initialstate)
        if self.config.parserefs and self.parse_types:
            # now find references using LegalRef
            parser = SwedishCitationParser(LegalRef(*self.parse_types),
                                           self.minter,
                                           self.commondata,
                                           allow_relative=self.parse_allow_relative)
            body = parser.parse_recursive(body)
        return body

    def extract_body(self, fp, basefile):
        """Given a open file containing raw document content (or intermediate
        content), return some sort of object representing the same
        content that :py:method:`tokenize` can work with.
        
        The default implementation assumes that the open file contains
        HTML/XML, creates a BeautifulSoup instance from it, and
        returns the body of that instance.
        
        Docrepos using different file formats, or having documents
        that are split up in multiple files, should override this to
        load those in some suitable way.  This will often be similar
        to the processing that extract_head does (but not always,
        eg. if the metadata is located in a HTML file but the main
        document content is in a PDF file).

        """
        # FIXME: This re-parses the same data as extract_head
        # does. This will be common. Maybe fix a superclass level
        # caching system? (ie read from self._rawbody, which
        # extract_head has previously set).
        parser = 'lxml'
        soup = bs4.BeautifulSoup(fp.read(), parser)
        return soup.body

    def sanitize_body(self, rawbody):
        """Given an object representing the document content, return the same or a similar 
        object, with some basic sanitation performed. 
        
        The default implementation returns its input unchanged.
        """
        return rawbody

    def get_pdf_analyzer(self, pdf):
        return PDFAnalyzer(pdf)

    def get_parser(self, basefile, sanitized):
        """should return a function that gets any iterable (the output
        from tokenize) and returns a ferenda.elements.Body object.
        
        The default implementation calls :py:func:`offtryck_parser` to
        create a function/closure which is returned IF the sanitized 
        body data is a PDFReader object. Otherwise, returns a function that
        justs packs every item in a recieved iterable into a Body object.
        
        If your docrepo requires a FSMParser-created parser, you should 
        instantiate and return it here.
        """
        if isinstance(sanitized, PDFReader):
            # If our sanitized body is a PDFReader, it's most likely
            # something that can be handled by the offtryck_parser.
            analyzer = self.get_pdf_analyzer(sanitized)
            metrics_path = self.store.path(basefile, 'intermediate',
                                           '.metrics.json')

            if os.environ.get("FERENDA_DEBUGANALYSIS"):
                plot_path = self.store.path(basefile, 'intermediate',
                                            '.plot.png')
            else:
                plot_path = None
            self.log.debug("%s: Calculating PDF metrics" % basefile)
            metrics = analyzer.metrics(metrics_path, plot_path)
            if os.environ.get("FERENDA_DEBUGANALYSIS"):
                pdfdebug_path = self.store.path(basefile, 'intermediate',
                                                '.debug.pdf')

                self.log.debug("Creating debug version of PDF")
                analyzer.drawboxes(pdfdebug_path, offtryck_gluefunc,
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
                                     debug=os.environ.get('FERENDA_FSMDEBUG', 0)            )
            parser.debug = os.environ.get('FERENDA_FSMDEBUG', False)
            return parser.parse
        else:
            def default_parser(iterable):
                return Body(list(iterable))
            return default_parser
    
    def tokenize(self, body):
        """Given a document format-specific object (like a PDFReader or a BeautifulSoup object),
        return a list or other iterable of suitable "chunks" for your parser function. 
        
        For PDF Readers, you might want to use :py:meth:`~ferenda.PDFReader.textboxes`
        with a suitable glue function to create the iterable.
        
        """
        # this method might recieve a arbitrary object (the superclass
        # impl returns a BeautifulSoup node) but must return an iterable
        if isinstance(body, PDFReader):
            return body.textboxes(offtryck_gluefunc, pageobjects=True)
        else:
            # just assume that this is iterable
            return body

    # see SFS.visit_node
    def visit_node(self, node, clbl, state, debug=False):
        """Visit each part of the document recursively (depth-first) and call
        a user-supplied function for each part.

        :param node: The document part
        :param clbl: A function that is called with node and state as
                     argument. It should return True if sub-nodes
                     should be visited, False otherwise.
        :param state: A mutable or immutable object (helpful!)

        """
        if debug:
            print("About to visit %s with %s" %
                  (node.__class__.__name__, clbl.__name__))
        newstate = clbl(node, state)
        if debug:
            print("After visiting %s: %s" % (node.__class__.__name__, newstate))
        if newstate is not None and isinstance(node, CompoundElement):
            for subnode in node:
                if debug:
                    print("about to visit subnode %s with %s" %
                          (subnode.__class__.__name__, newstate))
                self.visit_node(subnode, clbl, newstate, debug)

    def infer_metadata(self, resource, basefile=None):
        """Try to infer any missing metadata from what we already have.

        :param d: A configured Describer instance
        :param basefile: The basefile for the doc we want to infer from 
        """
        # Right now, this only tries to infer a dcterms:identifier if
        # not already present
        sup = super(SwedishLegalSource, self)
        if hasattr(sup, 'infer_metadata'):
            sup.infer_metadata(resource, basefile)
        d = Describer(resource.graph, resource.identifier)
        if not resource.value(DCTERMS.identifier):
            identifier = self.infer_identifier(basefile)
            # self.log.warning(
            #     "%s: No dcterms:identifier, assuming %s" % (basefile,
            #                                                 identifier))
            d.value(DCTERMS.identifier, identifier)

    def infer_identifier(self, basefile):
        """Given a basefile of a document, returns a string that is a usable
        dcterms:identifier for that document.
        
        This is similar to metadata_from_basefile, but should return a
        single string that can be used as a human-readable label or
        identifier for the document.

        """
        # FIXME: This logic should really be split up and put into
        # different subclasses override of infer_identifier. Also note
        # that many docrepos get dcterms:identifier from the document
        # itself.
        
        # Create one from basefile. First guess prefix
        if self.rdf_type == RPUBL.Kommittedirektiv:
            prefix = "Dir. "
        elif self.rdf_type == RPUBL.Utredningsbetankande:
            if self.alias.startswith("sou"):  # FIXME: only ever used by soukb
                prefix = "SOU "
            else:
                prefix = "Ds "
        elif self.rdf_type == RPUBL.Proposition:
            prefix = "Prop. "
        elif self.rdf_type == RPUBL.Forordningsmotiv:
            prefix = "Fm "
        else:
            raise ValueError(
                "Cannot create dcterms:identifier for rdf_type %r" %
                self.rdf_type)
        return "%s%s" % (prefix, basefile)


    def postprocess_doc(self, doc):
        """Do any last-minute postprocessing (mainly used to add extra
        metadata from doc.body to doc.head)"""
        pass


    def frontpage_content(self, primary=False):
        if not self.config.tabs:
            self.log.info("%s: Not doing frontpage content (config has tabs=False)" % self.alias)
            return
        x = self.tabs()[0]
        label = x[0]
        uri = x[1]
        body = self.frontpage_content_body()
        return ("<h2><a href='%(uri)s'>%(label)s</a></h2>"
                "<p>%(body)s</p>" % locals())

    def frontpage_content_body(self):
        # we could either count the number of items
        # self.store.list_basefiles_for("_postgenerate") returns or
        # count the number of unique docs in faceted_data. The latter
        # is prob more correct.
        return "%s dokument" % len(set([row['uri'] for row in self.faceted_data()]))


    ################################################################
    # General small utility functions
    # (these could be module functions or staticmethods instead)

    def parse_iso_date(self, datestr):
        # only handles YYYY-MM-DD now. Look into dateutil or isodate
        # for more complete support of all ISO 8601 variants
        datestr = datestr.replace(" ", "")  # Data cleaning occasionally
        # needed. Maybe this isn't
        # the right place?
        return datetime.strptime(datestr, "%Y-%m-%d").date()

    def parse_swedish_date(self, datestr):
        """Parses a number of common forms of expressing swedish dates with
        varying precision.

        >>> parse_swedish_date("3 februari 2010")
        datetime.date(2010, 2, 3)
        >>> parse_swedish_date("vid utgången av december 1999")
        datetime.date(1999, 12, 31)
        >>> parse_swedish_date("november 1999")
        ferenda.util.gYearMonth(1999, 11)
        >>> parse_swedish_date("1998")
        ferenda.util.gYear(1999)

        """
        day = month = year = None
        # assume strings on the form "3 februari 2010"
        # strings on the form "vid utg\xe5ngen av december 1999"
        if datestr.startswith("vid utg\xe5ngen av"):
            import calendar
            (x, y, z, month, year) = datestr.split()
            month = self.swedish_months[month]
            year = int(year)
            day = calendar.monthrange(year, month)[1]
        else:
            # assume strings on the form "3 februari 2010", "8 dec. 1997"
            components = datestr.split()
            year = int(components[-1])
            if len(components) >= 2:
                if components[-2].endswith("."):
                    components[-2] = components[-2][:-1]
                if components[-2] not in self.swedish_months:
                    raise ValueError(datestr)
                month = self.swedish_months[components[-2]]
            if len(components) >= 3:
                day = int(components[-3])

        # return the best we can
        if day:
            return date(year, month, day)
        if month:
            return util.gYearMonth(year, month)
        else:
            return util.gYear(year)


def offtryck_parser(basefile="0", metrics=None, preset=None,
                    identifier=None, debug=False):
    # First: merge the metrics we're provided with with a set of
    # defaults (for fallback), and wrap them in a LayeredConfig
    # structure
    if not metrics:
        metrics = {}
    defaultmetrics = {'header': 0,  # fix these
                      'footer': 1000,  # -""-
                      'odd_leftmargin': 172,
                      'odd_parindent': 187,
                      'odd_rightmargin': 619,
                      'even_leftmargin': 278,
                      'even_parindent': 293,
                      'even_rightmargin': 725,
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
    state = LayeredConfig(Defaults({'pageno': 0,
                                    'appendixno': None,
                                    'preset': preset}))
    def is_pagebreak(parser):
        return isinstance(parser.reader.peek(), Page)

    # page numbers, headings.
    def is_nonessential(parser):
        chunk = parser.reader.peek()
        strchunk = str(chunk).strip()
        # everything above or below these margins should be
        # pagenumbers -- always nonessential
        if chunk.top > metrics.bottommargin or chunk.bottom < metrics.topmargin:
            return True

        # pagenumbers can be in the left/right margin as well
        if ((chunk.right < metrics_leftmargin() or
             chunk.left > metrics_rightmargin()) and
                strchunk.isdigit()):
            return True

        # Propositioner has the identifier in the left or right
        # margin, set in the default style (or smaller)
        if (int(chunk.font.size) <= metrics.default.size and
            (chunk.right < metrics_leftmargin() or
             chunk.left > metrics_rightmargin()) and
                strchunk.startswith(parser.current_identifier)):
            # print("%s on p %s is deemed nonessential" % (str(chunk), state.pageno))
            return True

        # Direktiv first page has a similar identifier, but it starts
        # slightly before the right margin (hence +10), and is set in larger type.
        if (chunk.left + 10 < metrics_rightmargin() and
                strchunk == parser.current_identifier):
            return True

    def is_coverpage(parser):
        # first 2 pages of a SOU are coverpages
        return isinstance(
            parser.reader.peek(), Page) and state.preset == "sou" and state.pageno < 2

    def is_preamblesection(parser):
        chunk = parser.reader.peek()
        if isinstance(chunk, Page):
            return False
        txt = str(chunk).strip()
        fontsize = int(chunk.font.size)
        if not metrics.h2.size <= fontsize <= metrics.h1.size:
            return False

        for validheading in ('Propositionens huvudsakliga innehåll',
                             'Innehållsförteckning',
                             'Till statsrådet',
                             'Innehåll',
                             'Sammanfattning'):
            if txt.startswith(validheading):
                return True

    def is_section(parser):
        (ordinal, headingtype, title) = analyze_sectionstart(parser)
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
        if state.preset == 'proposition':
            return False
        chunk = parser.reader.peek()
        return (chunk.font.size == metrics.h1.size and
                chunk.font.family == metrics.h1.family)

    def is_unorderedsubsection(parser):
        # Subsections in "Författningskommentar" sections are
        # not always numbered. As a backup, check font size and family as well
        chunk = parser.reader.peek()
        return (chunk.font.size == metrics.h2.size and
                chunk.font.family == metrics.h2.family)

    def is_subsubsection(parser):
        (ordinal, headingtype, title) = analyze_sectionstart(parser)
        if ordinal:
            return headingtype == "h3" and ordinal.count(".") == 2

    def is_appendix(parser):
        chunk = parser.reader.peek()
        txt = str(chunk).strip()

        if (chunk.font.size == metrics.h1.size and txt.startswith("Bilaga ")):
            return True
        elif (int(chunk.font.size) == metrics.default.size and
              (chunk.left < metrics_leftmargin() or
               chunk.left > metrics_rightmargin())):
            m = re.search("Bilaga (\d)", str(chunk))
            if m:
                ordinal = int(m.group(1))
                if ordinal != state.appendixno:
                    return True

    def is_paragraph(parser):
        return True

    def make_body(parser):
        return p.make_children(Body())
    setattr(make_body, 'newstate', 'body')

    def make_paragraph(parser):
        # if "Regeringen beslutade den 8 april 2010 att" in str(parser.reader.peek()):
        #     raise ValueError("OK DONE")
        return parser.reader.next()

    def make_coverpage(parser):
        state.pageno += 1
        parser.reader.next()  # throwaway the Page object itself
        c = Coverpage()
        return parser.make_children(c)
    setattr(make_coverpage, 'newstate', 'coverpage')

    def make_preamblesection(parser):
        s = PreambleSection(title=str(parser.reader.next()).strip())
        if s.title == "Innehållsförteckning":
            parser.make_children(s)  # throw away
            return None
        else:
            return parser.make_children(s)
    setattr(make_preamblesection, 'newstate', 'preamblesection')

    def make_unorderedsection(parser):
        s = UnorderedSection(title=str(parser.reader.next()).strip())
        return parser.make_children(s)
    setattr(make_unorderedsection, 'newstate', 'unorderedsection')

    def make_unorderedsubsection(parser):
        s = UnorderedSection(title=str(parser.reader.next()).strip())
        return parser.make_children(s)
    setattr(make_unorderedsubsection, 'newstate', 'unorderedsubsection')

    def make_appendix(parser):
        # now, an appendix can begin with either the actual
        # headline-like title, or by the sidenote in the
        # margin. Find out which it is, and plan accordingly.
        done = False
        while not done:
            chunk = parser.reader.next()
            if isinstance(chunk, Page):
                continue
            m = re.search("Bilaga (\d)", str(chunk))
            if m:
                state.appendixno = int(m.group(1))
            if int(chunk.font.size) >= metrics.h2.size:
                done = True
        s = Appendix(title=str(chunk).strip(),
                     ordinal=str(state.appendixno),
                     uri=None)
        return parser.make_children(s)
    setattr(make_appendix, 'newstate', 'appendix')

    # this is used for subsections and subsubsections as well --
    # probably wont work due to the newstate property
    def make_section(parser):
        ordinal, headingtype, title = analyze_sectionstart(parser, parser.reader.next())
        if ordinal:
            identifier = "Prop. %s, avsnitt %s" % (basefile, ordinal)
            s = Section(ordinal=ordinal, title=title)
        else:
            s = Section(title=str(title))
        return parser.make_children(s)
    setattr(make_section, 'newstate', 'section')

    def skip_nonessential(parser):
        parser.reader.next()
        return None

    def skip_pagebreak(parser):
        # increment pageno
        state.pageno += 1
        parser.reader.next()
        sb = Sidbrytning()
        sb.ordinal = state.pageno
        return sb

    re_sectionstart = re.compile("^(\d[\.\d]*) +(.*[^\.])$").match

    def analyze_sectionstart(parser, textbox=None):
        """returns (ordinal, headingtype, text) if it looks like a section
        heading, (None, None, textbox) otherwise.

        """

        if not textbox:
            textbox = parser.reader.peek()
        # the font size and family should be defined
        found = False
        for h in ('h1', 'h2', 'h3'):
            h_metrics = getattr(metrics, h)
            if h_metrics.size == textbox.font.size and h_metrics.family == textbox.font.family:
                found = h
        if not found:
            return (None, None, textbox)
        txt = str(textbox)
        m = re_sectionstart(txt)
        if m:
            ordinal = m.group(1).rstrip(".")
            title = m.group(2)
            return (ordinal, found, title.strip())
        else:
            return (None, found, textbox)

    def metrics_leftmargin():
        if state.pageno % 2 == 0:  # even page
            return metrics.even_leftmargin
        else:
            return metrics.odd_leftmargin

    def metrics_rightmargin():
        if state.pageno % 2 == 0:  # even page
            return metrics.even_rightmargin
        else:
            return metrics.odd_rightmargin

    p = FSMParser()

    recognizers = [is_pagebreak,
                   is_appendix,
                   is_nonessential,
                   is_section,
                   is_subsection,
                   is_subsubsection,
                   is_preamblesection,
                   is_unorderedsection,
                   is_unorderedsubsection,
                   is_paragraph]
    if preset == "sou":
        recognizers.insert(0, is_coverpage)
    p.set_recognizers(*recognizers)

    commonstates = ("body", "preamblesection", "section", "subsection",
                    "unorderedsection", "unorderedsubsection", "subsubsection",
                    "appendix")

    p.set_transitions({(commonstates, is_nonessential): (skip_nonessential, None),
                       (commonstates, is_pagebreak): (skip_pagebreak, None),
                       (commonstates, is_paragraph): (make_paragraph, None),
                       ("body", is_coverpage): (make_coverpage, "coverpage"),
                       ("body", is_preamblesection): (make_preamblesection, "preamblesection"),
                       ("coverpage", is_coverpage): (False, None),
                       ("coverpage", is_preamblesection): (False, None),
                       ("coverpage", is_paragraph): (make_paragraph, None),
                       ("coverpage", is_pagebreak): (False, None),
                       ("preamblesection", is_preamblesection): (False, None),
                       ("preamblesection", is_section): (False, None),
                       ("body", is_section): (make_section, "section"),
                       ("body", is_unorderedsection): (make_unorderedsection, "unorderedsection"),
                       ("section", is_section): (False, None),
                       ("section", is_subsection): (make_section, "subsection"),
                       ("section", is_unorderedsection): (make_unorderedsection, "unorderedsection"),
                       ("section", is_unorderedsubsection): (make_unorderedsection, "unorderedsubsection"),
                       ("unorderedsection", is_section): (False, None),
                       ("unorderedsection", is_appendix): (False, None),
                       ("unorderedsection", is_preamblesection): (False, None),
                       ("unorderedsection", is_unorderedsection): (False, None),
                       ("unorderedsection", is_unorderedsubsection): (make_unorderedsubsection, "unorderedsubsection"),
                       ("unorderedsubsection", is_section): (False, None),
                       ("unorderedsubsection", is_appendix): (False, None),
                       ("unorderedsubsection", is_preamblesection): (False, None),
                       ("unorderedsubsection", is_unorderedsection): (False, None),
                       ("unorderedsubsection", is_unorderedsubsection): (False, None),
                       ("subsection", is_subsection): (False, None),
                       ("subsection", is_section): (False, None),
                       ("subsection", is_subsubsection): (make_section, "subsubsection"),
                       ("subsubsection", is_subsubsection): (False, None),
                       ("subsubsection", is_subsection): (False, None),
                       ("subsubsection", is_section): (False, None),
                       ("body", is_appendix): (make_appendix, "appendix"),
                       (("appendix", "subsubsection", "subsection", "section"), is_appendix):
                       (False, None)
                       })

    p.initial_state = "body"
    p.initial_constructor = make_body
    p.current_identifier = identifier
    p.debug = bool(debug)
    return p


def offtryck_gluefunc(textbox, nextbox, prevbox):
    linespacing = nextbox.font.size / 2
    parindent = nextbox.font.size
    # FIXME: if one textbox has family "TimesNewRomanPSMT@12" and
    # another "TimesNewRomanPS-BoldMT@12", they should be considered
    # the same family (and pdfreader/pdftohtml will wrap the latters'
    # text in a <b> element). Maybe achiveable through
    # FontmappingPDFReader?
    if (textbox.font.size == nextbox.font.size and
        textbox.font.family == nextbox.font.family and
        textbox.top + textbox.height + linespacing > nextbox.top and
        prevbox.left < nextbox.right and
        ((prevbox.top + prevbox.height == nextbox.top + nextbox.height) or  # compare baseline, not topline
         (prevbox.left == nextbox.left) or
         (parindent * 2 >= (prevbox.left - nextbox.left) >= parindent)
         )):
        return True


# (ab)use the CitationClass, with it's useful parse_recursive method,
# to use a legalref based parser instead of a set of pyparsing
# grammars.
class SwedishCitationParser(CitationParser):

    def __init__(self, legalrefparser, minter, commondata, allow_relative=False):
        assert isinstance(minter, URIMinter)
        assert isinstance(commondata, Graph)
        self._legalrefparser = legalrefparser
        self._minter = minter
        self._commondata = commondata
        self._currenturl = None
        self._allow_relative = allow_relative
        self.log = logging.getLogger("scp")

    def parse_recursive(self, part, predicate="dcterms:references"):
        if hasattr(part, 'about'):
            self._currenturl = part.about
        elif hasattr(part, 'uri') and not isinstance(part, (Link, A)):
            self._currenturl = part.uri
        if isinstance(part, (Link, A, H1, H2, H3)):
            # don't process text that's already a link (or a heading)
            if isinstance(part, str):  # caller expects a list
                return [part]
            else:
                return part
        else:
            return super(SwedishCitationParser, self).parse_recursive(part, predicate)

    def parse_string(self, string, predicate="dcterms:references"):
        from ferenda.sources.legal.se.sfs import UpphavtKapitel, UpphavdParagraf
        if isinstance(string, (UpphavtKapitel, UpphavdParagraf)):
            return [string]
        # basic normalization without stripping
        string = string.replace("\r\n", " ").replace("\n", " ").replace("\x00","")

        # transform self._currenturl => attributes.
        # FIXME: we should maintain a self._current_baseuri_attributes
        # instead of this fragile, URI-interpreting, hack.
        if self._currenturl:
            re_urisegments = re.compile(r'([\w]+://[^/]+/[^\d]*)(\d+:(bih\.[_ ]|N|)?\d+([_ ]s\.\d+|))#?(K([a-z0-9]+)|)(P([a-z0-9]+)|)(S(\d+)|)(N(\d+)|)')
            m = re_urisegments.match(self._currenturl)
            if m:
                attributes = {'law':m.group(2),
                              'chapter':m.group(6),
                              'section':m.group(8),
                              'piece':m.group(10),
                              'item':m.group(12)}
            else:
                attributes = {}
        else:
            attributes = {}
        for k in list(attributes):
            if attributes[k] is None:
                del attributes[k]
        try:
            return self._legalrefparser.parse(string,
                                              minter=self._minter,
                                              metadata_graph=self._commondata,
                                              baseuri_attributes=attributes,
                                              predicate=predicate,
                                              allow_relative=self._allow_relative)
        except RefParseError as e:
            self.log.error(e)
            return [string]

