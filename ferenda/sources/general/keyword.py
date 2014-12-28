# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# system libraries
import re
import os
from collections import defaultdict
from time import time

# 3rdparty libs
import pkg_resources
import requests
from lxml import etree
from lxml.builder import ElementMaker
from rdflib import Literal, Namespace

# my libs
from ferenda import util
from ferenda import DocumentRepository, TripleStore, DocumentStore, Describer
from ferenda.decorators import managedparsing
from ferenda.elements import Body



# FIXME: Use MW_NS = "{%s}" % xml.getroot().nsmap[None]
MW_NS = "{http://www.mediawiki.org/xml/export-0.3/}"


class KeywordStore(DocumentStore):
    def basefile_to_pathfrag(self, basefile):
        # Shard all files under initial letter, eg "Avtal" => "a/Avtal"
        first = basefile[0].lower()
        return "%s/%s" % (first, basefile)

    def pathfrag_to_basefile(self, pathfrag):
        first, basefile = pathfrag.split("/", 1)
        return basefile


class Keyword(DocumentRepository):

    """Implements support for 'keyword hubs', conceptual resources which
       themselves aren't related to any document, but to which other
       documents are related. As an example, if a docrepo has
       documents that each contains a set of keywords, and the docrepo
       parse implementation extracts these keywords as ``dcterms:subject``
       resources, this docrepo creates a document resource for each of
       those keywords. The main content for the keyword may come from
       the :class:`~ferenda.sources.general.MediaWiki` docrepo, and
       all other documents in any of the repos that refer to this
       concept resource are automatically listed.

    """  # FIXME be more comprehensible
    alias = "keyword"
    downloaded_suffix = ".txt"
    documentstore_class = KeywordStore
    xslt_template = "res/xsl/keyword.xsl"
    rdf_type = Namespace(util.ns['skos']).Concept
    namespaces = ['skos', 'prov', 'dcterms']

    def __init__(self, config=None, **kwargs):
        super(Keyword, self).__init__(config, **kwargs)
        # extra functions -- subclasses can add / remove from this
        self.termset_funcs = [self.download_termset_mediawiki,
                              self.download_termset_wikipedia]

    def get_default_options(self):
        opts = super(Keyword, self).get_default_options()
        # The API endpoint URLs change with MW language
        opts['mediawikiexport'] = 'http://localhost/wiki/Special:Export/%s(basefile)'
        opts['wikipediatitles'] = 'http://download.wikimedia.org/svwiki/latest/svwiki-latest-all-titles-in-ns0.gz'
        return opts

    def canonical_uri(self, basefile):
        # keywords often contain spaces -- convert to underscore to get nicer URIs
        return super(Keyword, self).canonical_uri(basefile.replace(" ",  "_"))

    def basefile_from_uri(self, uri):
        # do the inverse conversion from canonical_uri. NOTE: if your
        # Keyword-derived repo might handle keywords that contain "_",
        # you need to have some other basefile <-> uri strategy.
        ret = super(Keyword, self).basefile_from_uri(uri)
        if ret:
            ret = ret.replace("_", " ")
        return ret

    def download(self, basefile=None):
        # Get all "term sets" (used dcterms:subject Objects, wiki pages
        # describing legal concepts, swedish wikipedia pages...)
        terms = defaultdict(dict)

        # 1) Query the triplestore for all dcterms:subject triples (is this
        # semantically sensible for a "download" action -- the content
        # isn't really external?) -- term set "subjects" (these come
        # from both court cases and legal definitions in law text)
        sq = """
        PREFIX dcterms:<http://purl.org/dc/terms/>
        PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>

        SELECT DISTINCT ?subject ?label
        WHERE { {?uri dcterms:subject ?subject . } 
                OPTIONAL {?subject rdfs:label ?label . } }
        """
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        results = store.select(sq, "python")
        for row in results:
            if 'label' in row:
                label = row['label']
            else:
                label = self.basefile_from_uri(row['subject'])
            # sanity checking -- not everything can be a legit
            # keyword. Must be under 100 chars and not start with . or /
            if len(label) < 100 and not label[0] in (".", "/", ":"):
                terms[label]['subjects'] = True

        self.log.debug("Retrieved %s subject terms from triplestore" % len(terms))

        for termset_func in self.termset_funcs:
            termset_func(terms)

        for term in terms:
            if not term:
                continue
            oldterms = ""
            termpath = self.store.downloaded_path(term)
            if os.path.exists(termpath):
                oldterms = util.readfile(termpath)
            newterms = "\n".join(sorted(terms[term])) + "\n"
            if newterms != oldterms:
                util.ensure_dir(termpath)
                util.writefile(termpath, newterms)
                self.log.info("%s: in %s termsets" % (term, len(terms[term])))
            else:
                self.log.debug("%s: skipped" % term)

    def download_termset_mediawiki(self, terms):
        # 2) Download the wiki.lagen.nu dump from
        # http://wiki.lagen.nu/pages-articles.xml -- term set "mediawiki"
        xml = etree.parse(requests.get(self.config.mediawikidump).text)
        wikinamespaces = []

        # FIXME: Handle any MW_NS namespace (c.f. wiki.py)

        for ns_el in xml.findall("//" + MW_NS + "namespace"):
            wikinamespaces.append(ns_el.text)
        for page_el in xml.findall(MW_NS + "page"):
            title = page_el.find(MW_NS + "title").text
            if title == "Huvudsida":
                continue
            if ":" in title and title.split(":")[0] in wikinamespaces:
                continue  # only process pages in the main namespace
            if title.startswith("SFS/"):  # FIXME: should be handled in
                                         # subclass -- or
                                         # repo-specific pages should
                                         # be kept in subclasses
                continue  # only proces normal keywords
            terms[title]['mediawiki'] = True

        self.log.debug("Retrieved subject terms from wiki, now have %s terms" %
                       len(terms))

    def download_termset_wikipedia(self, terms):
        # 3) Download the Wikipedia dump from
        # http://download.wikimedia.org/svwiki/latest/svwiki-latest-all-titles-in-ns0.gz
        # -- term set "wikipedia"
        # FIXME: only download when needed
        resp = requests.get(self.config.wikipediatitles)
        wikipediaterms = resp.text.split("\n")
        for utf8_term in wikipediaterms:
            term = utf8_term.decode('utf-8').strip()
            if term in terms:
                terms[term]['wikipedia'] = True

        self.log.debug("Retrieved terms from wikipedia, now have %s terms" % len(terms))

    @managedparsing
    def parse(self, doc):
        # create a dummy txt
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['dcterms'].title, Literal(doc.basefile, lang=doc.lang))
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        doc.body = Body()  # can be empty, all content in doc.meta
        self.parse_entry_update(doc)
        return True

    re_tagstrip = re.compile(r'<[^>]*>')

    # FIXME: This is copied verbatim from sfs.py -- maybe it could go
    # into DocumentRepository or util? (or possibly triplestore?)
    def store_select(self, store, query_template, uri, context=None):
        if os.path.exists(query_template):
            fp = open(query_template, 'rb')
        elif pkg_resources.resource_exists('ferenda', query_template):
            fp = pkg_resources.resource_stream('ferenda', query_template)
        else:
            raise ValueError("query template %s not found" % query_template)
        params = {'uri': uri,
                  'context': context}
        sq = fp.read().decode('utf-8') % params
        fp.close()
        # FIXME: Only FusekiStore.select supports (or needs) uniongraph
        if context:
            uniongraph = False
        else:
            uniongraph = True
        return store.select(sq, "python", uniongraph=uniongraph)

    def time_store_select(self, store, query_template, basefile, context=None, label="things"):
        values = {'basefile': basefile,
                  'label': label,
                  'count': None}
        uri = self.canonical_uri(basefile)
        msg = ("%(basefile)s: selected %(count)s %(label)s "
               "(%(elapsed).3f sec)")
        with util.logtime(self.log.debug,
                          msg,
                          values):
            result = self.store_select(store,
                                       query_template,
                                       uri,
                                       context)
            values['count'] = len(result)
        return result
        
            

    # FIXME: translate this to be consistent with construct_annotations
    # (e.g. return a RDF graph through one or a few SPARQL queries),
    # not a XML monstrosity

    def prep_annotation_file(self, basefile):
        uri = self.canonical_uri(basefile)
        keyword = basefile
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)

        # Use SPARQL queries to create a rdf graph (to be used by the
        # xslt transform) containing the wiki authored
        # dcterms:description for this term. FIXME: This isn't a real
        # RDF graph yet.
        wikidesc = self.time_store_select(store,
                                          "res/sparql/keyword_subjects.rq",
                                          basefile,
                                          None,
                                          "descriptions")
        
        # compatibility hack to enable lxml to process qnames for namespaces 
        def ns(string):
            if ":" in string:
                prefix, tag = string.split(":", 1)
                return "{%s}%s" % (str(self.ns[prefix]), tag)

        # FIXME: xhv MUST be part of nsmap
        if 'xhtml' not in self.ns:
            self.ns['xhtml'] = "http://www.w3.org/1999/xhtml"

        root_node = etree.Element(ns("rdf:RDF"), nsmap=self.ns)

        main_node = etree.SubElement(root_node, ns("rdf:Description"))
        main_node.set(ns("rdf:about"), uri)

        for d in wikidesc:
            desc_node = etree.SubElement(main_node, ns("dcterms:description"))
            xhtmlstr = "<div xmlns='http://www.w3.org/1999/xhtml'>%s</div>" % (d['desc'])
            # xhtmlstr = xhtmlstr.replace(
            #    ' xmlns="http://www.w3.org/1999/xhtml"', '')
            desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

        # subclasses override this to add extra annotations from other
        # sources
        self.prep_annotation_file_termsets(basefile, main_node)
        
        treestring = etree.tostring(root_node,
                                    encoding="utf-8",
                                    pretty_print=True)
        with self.store.open_annotation(basefile, mode="wb") as fp:
            fp.write(treestring)
        return self.store.annotation_path(basefile)

    def prep_annotation_file_termsets(self, basefile, main_node):
        pass

        

