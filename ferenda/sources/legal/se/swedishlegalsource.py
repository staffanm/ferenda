# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# Intermediate base class containing some functionality useful
# for handling data sources of swedish law, including minting URIs etc..

from bz2 import BZ2File
from datetime import datetime, date
from urllib.parse import quote, unquote
from wsgiref.util import request_uri
import logging
import operator
import os
import sys
import re
import codecs

from layeredconfig import LayeredConfig, Defaults
from rdflib import URIRef, RDF, Namespace, Literal, Graph, BNode
from rdflib.resource import Resource
from rdflib.namespace import DCTERMS, SKOS, FOAF, RDFS
BIBO = Namespace("http://purl.org/ontology/bibo/")
OLO = Namespace("http://purl.org/ontology/olo/core#")
from six import text_type as str
import bs4
from cached_property import cached_property

from ferenda import (DocumentRepository, DocumentStore, FSMParser,
                     CitationParser, Describer, Facet, RequestHandler)
from ferenda import util, fulltextindex
from ferenda.sources.legal.se.legalref import Link, LegalRef, RefParseError
from ferenda.elements.html import A, H1, H2, H3, P, Strong, Pre
from ferenda.elements import serialize, Section, Body, CompoundElement, UnicodeElement, Preformatted
from ferenda.pdfreader import Page, BaseTextDecoder, Textelement
from ferenda.pdfreader import PDFReader
from ferenda.pdfanalyze import PDFAnalyzer
from ferenda.decorators import action, managedparsing, newstate
from ferenda.thirdparty.coin import URIMinter
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


# used instead of False when we need to provide more information (yet
# still evaluate to False in a bool context)
class SupportsResult(int):

    def __new__(cls, *args, **kwargs):
        obj = int.__new__(cls, *args)
        object.__setattr__(obj, 'reason', kwargs['reason'])
        return obj

    def __bool__(self):
        return False


class SwedishLegalHandler(RequestHandler):
    def supports(self, environ):
        if environ['PATH_INFO'].startswith("/dataset/"):
            return super(SwedishLegalHandler, self).supports(environ)
        
        res = environ['PATH_INFO'].startswith("/" + self.repo.urispace_segment + "/")
        if not res:
            res =  SupportsResult(reason="'%s' didn't start with '/%s/'" % (environ['PATH_INFO'], 
                                                                           self.repo.urispace_segment))
        return res
        

class SwedishLegalSource(DocumentRepository):
    download_archive = False
    documentstore_class = SwedishLegalStore
    requesthandler_class = SwedishLegalHandler
    namespaces = ['rdf', 'rdfs', 'xsd', 'dcterms', 'skos', 'foaf',
                  'xhv', 'xsi', 'owl', 'prov', 'bibo', 'olo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]

    alias = "swedishlegalsource"

    lang = "sv"

    rdf_type = RPUBL.Rattsinformationsdokument  # subclasses override this

    parse_types = LegalRef.RATTSFALL, LegalRef.LAGRUM, LegalRef.KORTLAGRUM, LegalRef.FORARBETEN
    parse_allow_relative = False
    sparql_annotations = "sparql/describe-base.rq"
    
    # This is according to the RPUBL vocabulary: All
    # rpubl:Rattsinformationsdokument should have dcterms:title,
    # dcterms:issued (must be a xsd:date), dcterms:publisher and
    # dcterms:identifier
    required_predicates = [RDF.type, DCTERMS.title, DCTERMS.issued,
                           DCTERMS.identifier, PROV.wasGeneratedBy]
    if sys.platform == "darwin":
        collate_locale = "sv_SE.ISO8859-15"  # See
                                             # http://bugs.python.org/issue23195#msg233690
                                             # why we can't let it be
                                             # eg. sv_SE.UTF-8
    else:
        collate_locale = "sv_SE.UTF-8"

    
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
        with codecs.open(spacefile, encoding="utf-8") as space:
            with codecs.open(slugsfile, encoding="utf-8") as slugs:
                cfg = Graph().parse(space,
                                    format="turtle").parse(slugs,
                                                           format="turtle")
        COIN = Namespace("http://purl.org/court/def/2009/coin#")
        # select correct URI for the URISpace definition by
        # finding a single coin:URISpace object
        spaceuri = cfg.value(predicate=RDF.type, object=COIN.URISpace)
        return URIMinter(cfg, spaceuri)

    @cached_property
    def refparser(self):
        cd = self.commondata
        if self.alias != "sfs" and self.resourceloader.exists("extra/sfs.ttl"):
            with self.resourceloader.open("extra/sfs.ttl") as fp:
                cd.parse(data=fp.read(), format="turtle")
        return SwedishCitationParser(LegalRef(*self.parse_types),
                                     self.minter,
                                     cd,
                                     allow_relative=self.parse_allow_relative)
    
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
        opts['cssfiles'] = ['css/swedishlegalsource.css']
        return opts

    def download_is_different(self, existing, new):
        # almost all resources handled by all repos deriving from this
        # are immutable, ie they should never change. If some repo
        # needs to handle changed resources (like SFS) they'll have to
        # override this and do a proper semantic difference check.
        if self.config.refresh:
            return True
        else:
            return False  # or maybe just return self.config.refresh...

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
        if basefile != computed_basefile:
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
        """Given a basefile (typically during the download stage), make sure
        it's consistent with whatever rules the repo has for basefile
        naming, and sanitize it if it's not proper but still possible
        to guess what it should be.
        
        Sanitazion rules may include things like converting
        two-digit-years to four digits, removing or adding leading
        zeroes, case folding etc.
        
        Intended to be overridden by every docrepo that has rules for
        basefiles. The default implementation returns the basefile
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
        # Does a very simple transform. Examples:
        #
        # "https://lagen.nu/prop/1999/2000:35" => "1999/2000:35"
        # "https://lagen.nu/rf/hfd/2013/not/12" => "hfd/2013/not/12"
        # "https://lagen.nu/sosfs/2015:10" => "2015:10"
        # "https://lagen.nu/sfs/2013:1127/konsolidering/2014:117" => "2013:1127/konsolidering/2014:117"
        # "https://lagen.nu/sfs/1736:0123_1" => "1736:0123 1"
        # 
        # Subclasses with more specific rules should override, call
        # this through super(), and then sanitize basefile afterwards.
        base = self.urispace_base
        spacereplacement = str(self.minter.space.slugTransform.spaceRepl)
        # FIXME: This is super hacky.
        if base == "http://rinfo.lagrummet.se":
            base += "/publ"
        if 'develurl' in self.config:
            uri = uri.replace(self.config.develurl, self.config.url)
        if uri.startswith(base) and uri[len(base)+1:].startswith(self.urispace_segment):
            offset = 2 if self.urispace_segment else 1
            basefile = uri[len(base) + len(self.urispace_segment) + offset:]
            if spacereplacement:
                basefile = basefile.replace(spacereplacement, " ")
            if "#" in basefile:
                basefile = basefile.split("#", 1)[0]
            return basefile

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
        # reset some global state
        UnorderedSection.counter = 0
        PreambleSection.counter = 0
        self.refparser._legalrefparser.namedlaws = {}
        fp = self.parse_open(doc.basefile)
        resource = self.parse_metadata(fp, doc.basefile)
        doc.meta = resource.graph
        doc.uri = str(resource.identifier)
        if resource.value(DCTERMS.title):
            doc.lang = resource.value(DCTERMS.title).language
        doc.body = self.parse_body(fp, doc.basefile)
        if not fp.closed:
            fp.close()
        self.postprocess_doc(doc)
        self.parse_entry_update(doc)
        # print(doc.meta.serialize(format="turtle").decode("utf-8"))
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
                           "rpubl:utfardandedatum",
                           "rpubl:ikrafttradandedatum",
                           "rpubl:beslutsdatum"):
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
        rawbody = self.extract_body(fp, basefile)
        sanitized = self.sanitize_body(rawbody)
        parser = self.get_parser(basefile, sanitized)
        tokenstream = self.tokenize(sanitized)
        body = parser(tokenstream)
        for func, initialstate in self.visitor_functions(basefile):
            # could be functions for assigning URIs to particular
            # nodes, extracting keywords from text etc. Note: finding
            # references in text with LegalRef is done afterwards
            self.visit_node(body, func, initialstate)
        self._serialize_unparsed(body, basefile)
        if self.config.parserefs and self.parse_types:
            body = self.refparser.parse_recursive(body)
        return body

    def _serialize_unparsed(self, body, basefile):
        # FIXME: special hack depending on undocument config
        # variable. This is needed for parse-bench.py and its
        # RepoTest.createtest() method.
        if 'serializeunparsed' in self.config and self.config.serializeunparsed:
            serialized_path = self.store.serialized_path(basefile) + ".unparsed"
            serialized_path = serialized_path.replace(self.store.datadir + "/serialized", self.config.serializeunparsed + "/serialized/" + self.alias)
            with self.store._open(serialized_path, "wb") as fp:
                r = serialize(body, format="json")
                fp.write(r.encode('utf-8'))

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
        """Given an object representing the document content, return the same
        or a similar object, with some basic sanitation performed.
        
        The default implementation returns its input unchanged.

        """
        return rawbody

    def get_parser(self, basefile, sanitized, initialstate=None):
        """should return a function that gets any iterable (the output
        from tokenize) and returns a ferenda.elements.Body object.
        
        The default implementation returns a function that justs packs
        every item in a recieved iterable into a Body object.
        
        If your docrepo requires a FSMParser-created parser, you should
        instantiate and return it here.

        """
        def default_parser(iterable):
            if isinstance(iterable, PDFReader):
                return iterable
            else:
                return Body(list(iterable))
        return default_parser
    
    def get_pdf_analyzer(self, sanitized):
        return PDFAnalyzer(sanitized)
    

    def tokenize(self, body):
        """Given a document format-specific object (like a PDFReader or a BeautifulSoup object),
        return a list or other iterable of suitable "chunks" for your parser function. 
        
        For PDF Readers, you might want to use :py:meth:`~ferenda.PDFReader.textboxes`
        with a suitable glue function to create the iterable.
        
        """
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
        # Right now, this tries to infer a dcterms:identifier if not
        # already present, and adds prov:alternateOf (the original
        # main URL from where the data was fetched) and
        # prov:wasDerivedFrom (URIs representing the actual
        # PDF/Word/etc file(s) that is the basis for the parsed data).
        sup = super(SwedishLegalSource, self)
        if hasattr(sup, 'infer_metadata'):
            sup.infer_metadata(resource, basefile)
        d = Describer(resource.graph, resource.identifier)
        identifier = resource.value(DCTERMS.identifier)
        if not identifier:
            if identifier is not None:
                # there is a dcterms:identifier triple, but the object
                # is falsy (proably an emptry string). remove that.
                resource.graph.remove((resource.identifier, DCTERMS.identifier, identifier))
            identifier = self.infer_identifier(basefile)
            # self.log.warning(
            #     "%s: No dcterms:identifier, assuming %s" % (basefile,
            #                                                 identifier))
            
            d.value(DCTERMS.identifier, identifier)

        if not resource.value(PROV.alternateOf):
            source_url = self.source_url(basefile)
            if source_url:
                with d.rel(PROV.alternateOf, source_url):
                    d.value(RDFS.label, Literal("Källa", lang="sv"))

        if not resource.value(PROV.wasDerivedFrom):
            sourcefiles = self.sourcefiles(basefile, resource)
            if len(sourcefiles) == 1:
                sourcefile, label = sourcefiles[0]
                if self.store.storage_policy == "dir":
                    if os.sep in sourcefile:
                        sourcefile = sourcefile.rsplit(os.sep, 1)[1]
                    sourcefileuri = URIRef("%s?attachment=%s&repo=%s&dir=%s" %
                                           (resource.identifier,
                                            sourcefile,
                                            self.alias, "downloaded"))
                else:
                    sourcefileuri = URIRef("%s?repo=%s&dir=%s" %
                                           (resource.identifier,
                                            self.alias, "downloaded"))
                    
                with d.rel(PROV.wasDerivedFrom, sourcefileuri):
                    d.value(RDFS.label, Literal(label, lang="sv"))
            elif len(sourcefiles) > 1:
                # The commented-out code shows how to create a ordered
                # list using the native rdf:List concept (ie BNodes
                # with rdf:first/rdf:next). Serialization into RDFa
                # works, but this became unwieldy to query using
                # SPARQL. Instead we create a index triple for each
                # member in the list using the olo:index property (but
                # we don't bother with the rest of the olo vocab).
                #
                # derivedfrom = BNode()
                # c = Collection(resource.graph, derivedfrom)
                # for sourcefile, label in sourcefiles:
                #     if os.sep in sourcefile:
                #         sourcefile = sourcefile.rsplit(os.sep, 1)[1]
                #     sourcefileur = URIRef("%s?attachment=%s&repo=%s&dir=%s" %
                #                            (resource.identifier, sourcefile,
                #                             self.alias, "downloaded"))
                #     c.append(sourcefileuri)
                #     resource.graph.add((sourcefileuri, RDFS.label,
                #                         Literal(label, lang="sv")))
                # d.rel(PROV.wasDerivedFrom, derivedfrom)
                for index, tupl in enumerate(sourcefiles):
                    (sourcefile, label) = tupl
                    if os.sep in sourcefile:
                        sourcefile = sourcefile.rsplit(os.sep, 1)[1]
                    sourcefileuri = URIRef("%s?attachment=%s&repo=%s&dir=%s" %
                                           (resource.identifier,
                                            sourcefile,
                                            self.alias, "downloaded"))
                    with d.rel(PROV.wasDerivedFrom, sourcefileuri):
                        d.value(RDFS.label, Literal(label, lang="sv"))
                        d.value(OLO['index'], Literal(index))
            else:
                self.log.warning("%s: infer_metadata: No sourcefiles" %
                                 basefile)
            
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
            
            raise ValueError("Cannot create dcterms:identifier for rdf_type %s" % repr(self.rdf_type))
        return "%s%s" % (prefix, basefile)

    def postprocess_doc(self, doc):
        """Do any last-minute postprocessing (mainly used to add extra
        metadata from doc.body to doc.head)"""
        pass

    def get_url_transform_func(self, repos=None, basedir=None, develurl=None):
        f = super(SwedishLegalSource, self).get_url_transform_func(repos, basedir, develurl)
        if develurl:
            return f
        # since all Swedish legal source repos share the method of
        # generating URIs (through the self.minter property), we can
        # just share the initialized minter object.
        minter = self.minter
        for repo in repos:
            # NB: this doesn't check for the existance of a previous
            # minter object, since I can't find a way to do that with
            # a property using the @cached_property
            # decorator. Hopefully not an issue.
            repo.minter = minter
        return f

    def sourcefiles(self, basefile, resource=None):
        if resource.value(DCTERMS.identifier):
            identifier = str(resource.value(DCTERMS.identifier))
        else:
            identifier = self.infer_identifier(basefile)
        return [(self.store.downloaded_path(basefile),
                 identifier)]

    def source_url(self, basefile):
        url = self.remote_url(basefile)
        if url:
            return quote(url, safe="/:?$=&%")
        # else return None

    def relate(self, basefile, otherrepos=[]):
        for repo in otherrepos:
            # make sure all repos have a (copy of a) minter object for
            # performance reasons (compare self.get_url_transform_func)
            repo.minter = self.minter
        return super(SwedishLegalSource, self).relate(basefile, otherrepos)

    standardfacets = [Facet(RDFS.label,
                            use_for_toc=False,
                            use_for_feed=False,
                            toplevel_only=False,
                            dimension_label="label",
                            dimension_type="value",
                            multiple_values=False,
                            indexingtype=fulltextindex.Label(boost=16)),
                      Facet(DCTERMS.creator,
                            use_for_toc=False,
                            use_for_feed=False,
                            toplevel_only=False,
                            dimension_label="creator",
                            dimension_type="ref",
                            multiple_values=False,
                            indexingtype=fulltextindex.URI()),
                      Facet(DCTERMS.issued,
                            use_for_toc=False,
                            use_for_feed=False,
                            toplevel_only=False,
                            dimension_label="issued",
                            dimension_type="year",
                            multiple_values=False)]


    _relate_fulltext_value_cache = {}
    _default_creator = "Regeringen"

    def _relate_fulltext_value_rootlabel(self, desc):
        if desc.getvalues(DCTERMS.title):
            title = desc.getvalue(DCTERMS.identifier)
        else:
            self.log.warning("Missing dcterms:title")
            title = "(Titel saknas)"
        return "%s: %s" % (desc.getvalue(DCTERMS.identifier),
                           title)
    
    def _relate_fulltext_value(self, facet, resource, desc):
        if facet.dimension_label in ("label", "creator", "issued"):
            # "creator" and "issued" should be identical for the root
            # resource and all contained subresources. "label" can
            # change slighly.
            resourceuri = resource.get("about")
            rooturi = resourceuri.split("#")[0]
            if "#" not in resourceuri and rooturi not in self._relate_fulltext_value_cache:
                l = self._relate_fulltext_value_rootlabel(desc)
                if desc.getrels(RPUBL.departement):
                    c = desc.getrel(RPUBL.departement)
                else:
                    c = self.lookup_resource(self._default_creator)
                if desc.getvalues(DCTERMS.issued):
                    i = desc.getvalue(DCTERMS.issued)
                elif desc.getvalues(RPUBL.arsutgava):
                    # we have no knowledge of the exact date this was
                    # issued. It should be in the doc itself, but for
                    # now we fake one -- NB it'll be a year off 50% of
                    # the time.
                    y = int(desc.getvalue(RPUBL.arsutgava).split("/")[0])
                    i = date(y, 12, 31)
                else:
                    # we have no indication whatsoever of the issued
                    # date. Maybe it's today?
                    i = date.today()
                self._relate_fulltext_value_cache[rooturi] = {
                    "creator": c,
                    "issued": i,
                    "label": l
                }
            v = self._relate_fulltext_value_cache[rooturi][facet.dimension_label]
            if facet.dimension_label == "label" and "#" in resourceuri:
                if desc.getvalues(DCTERMS.title):
                    if desc.getvalues(BIBO.chapter):
                        v = "%s %s" % (desc.getvalue(BIBO.chapter),
                                       desc.getvalue(DCTERMS.title))
                    else:
                        v = "%s" % (desc.getvalue(DCTERMS.title))
                else:
                    # we don't have any title/label for whatever
                    # reason. Uniquify this by using the URI fragment
                    v = "%s, %s" % (v, resourceuri.split("#", 1)[1])
                
                # the below logic is useful for when labels must be
                # "standalone". with nested / inner hits, labels are
                # presented within the context of the parent document,
                # ie. it's preferable to use "15.2 Konsekvenser"
                # rather than "SOU 1997:39: Integritet ´ Offentlighet
                # ´ Informationsteknik, avsnitt 15.2 'Konsekvenser'"
#                if desc.getvalues(DCTERMS.title):
#                    if desc.getvalues(BIBO.chapter):
#                        v = "%s, avsnitt %s '%s'" % (v,
#                                                     desc.getvalue(BIBO.chapter),
#                                                     desc.getvalue(DCTERMS.title))
#                    else:
#                        v = "%s, '%s'" % (v, desc.getvalue(DCTERMS.title))
#                else:
#                    # we don't have any title for whatever
#                    # reason. Uniquify this rdfs:label by using the
#                    # URI fragment
#                    v = "%s, %s" % (v, resourceuri.split("#", 1)[1])
            return facet.dimension_label, v
        else:
            return super(SwedishLegalSource, self)._relate_fulltext_value(facet, resource, desc)

    def facets(self):
        return super(SwedishLegalSource, self).facets() + self.standardfacets
        
    def toc_item(self, binding, row):
        # the default toc listing uses <b>identifier</b>: title, with
        # only identifier being a link. Should work for most doctypes.
        return [Strong([Link(self.toc_item_identifier(row), uri=row['uri'])]),
                ": ", self.toc_item_title(row)]

    def toc_item_identifier(self, row):
        return row.get('dcterms_identifier', '(ID saknas)')

    def toc_item_title(self, row):
        return row.get('dcterms_title', '(Titel saknas)')

        
    def frontpage_content(self, primary=False):
        if not self.config.tabs:
            self.log.debug("%s: Not doing frontpage content (config has tabs=False)" % self.alias)
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
            # first normalize misformtting like "7juni 2007"
            datestr = re.sub("([a-z])(\d)", "\\1 \\2", datestr)
            datestr = re.sub("(\d)([a-z])", "\\1 \\2", datestr)
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

    def temp_sfs_uri(self, lawname):
        # Propositions and other preparatory works may suggest new
        # laws. At that point in time, no SFS number for the proposed
        # law exists, which makes it hard to mint an URI for the
        # proposed law (or a section within it) which we need when eg
        # creating detailed metadata about the commentary on the
        # section. Later, we may need to recreate that URI based on
        # information available at a later point in time, when an
        # official SFS number exists, eg when collecting annotations
        # on a law, when we want to get commentary from said
        # preparatory works.
        # 
        # This function creates a unique URI, based on a SFS number
        # derived from the name of the law
        slug = re.sub('\W+', '', lawname).lower()
        slug = re.sub('\d+', '', slug)
        slug = slug.replace("å", "aa").replace("ä", "ae").replace("ö", "oe").replace("é", "e")
        numslug = util.base27encode(slug)
        assert util.base27decode(numslug) == slug, "%s roundtripped as %s" % (slug, util.base26decode(numslug))
        resource = self.polish_metadata(
            {"rdf:type": RPUBL.KonsolideradGrundforfattning,
             "rpubl:arsutgava": "0000",
             "rpubl:lopnummer": str(numslug),
             "rpubl:forfattningssamling": URIRef(self.lookup_resource("SFS", SKOS.altLabel))})
        return str(resource.identifier)

    # hook for RepoTester to call
    def tearDown(self, testcase):
        self.refparser._legalrefparser.namedlaws = {}
        self.refparser._legalrefparser.currentlynamedlaws = {}



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
        self._currentattribs = None
        self._allow_relative = allow_relative
        self.log = logging.getLogger("scp")

    def parse_recursive(self, part, predicate="dcterms:references"):
        if hasattr(part, 'about'):
            self._currenturl = part.about
        elif hasattr(part, 'uri') and not isinstance(part, (Link, A)):
            self._currenturl = part.uri
        if isinstance(part, (Link, A, H1, H2, H3, DokumentRubrik, Preformatted, Pre)):
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
        # basic normalization without stripping (NOTE: this messes up
        # Preformatted sections, so parse_recursive avoids calling
        # this for those). FIXME: We should remove this normalization,
        # it's not parse_string's place to do this. Unfortunately
        # other parts rely on this normalization for the time being
        # (parts of the test suite fails without). We should fix that.
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
        elif self._currentattribs:
            attributes = dict(self._currentattribs)
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
