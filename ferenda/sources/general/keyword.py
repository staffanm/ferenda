# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# system libraries
import re
from collections import defaultdict
from time import time

# 3rdparty libs
import requests
from lxml import etree

# my libs
from ferenda import util
from ferenda import DocumentRepository, TripleStore
from ferenda.decorators import managedparsing

MW_NS = "{http://www.mediawiki.org/xml/export-0.3/}"


class Keyword(DocumentRepository):

    """Implements support for 'keyword hubs', conceptual resources which
       themselves aren't related to any document, but to which other
       documents are related. As an example, if a docrepo has
       documents that each contains a set of keywords, and the docrepo
       parse implementation extracts these keywords as ``dct:subject``
       resources, this docrepo creates a document resource for each of
       those keywords. The main content for the keyword may come from
       the :class:`~ferenda.sources.general.MediaWiki` docrepo, and
       all other documents in any of the repos that refer to this
       concept resource are automatically listed.

    """  # FIXME be more comprehensible
    alias = "keyword"
    downloaded_suffix = ".txt"

    def __init__(self, **kwargs):
        super(Keyword, self).__init__(**kwargs)
        # extra functions -- subclasses can add / remove from this
        self.termset_funcs = [self.download_termset_mediawiki,
                              self.download_termset_wikipedia]

    def get_default_options(self):
        opts = super(Keyword, self).get_default_options()
        # The API endpoint URLs change with MW language
        opts['mediawikiexport'] = 'http://localhost/wiki/Special:Export/%s(basefile)'
        opts['wikipediatitles'] = 'http://download.wikimedia.org/svwiki/latest/svwiki-latest-all-titles-in-ns0.gz'
        return opts

    def download(self):
        # Get all "term sets" (used dct:subject Objects, wiki pages
        # describing legal concepts, swedish wikipedia pages...)
        terms = defaultdict(dict)

        # 1) Query the triplestore for all dct:subject triples (is this
        # semantically sensible for a "download" action -- the content
        # isn't really external?) -- term set "subjects" (these come
        # from both court cases and legal definitions in law text)
        sq = """
        PREFIX dct:<http://purl.org/dc/terms/>

        SELECT DISTINCT ?subject ?label { {?uri dct:subject ?subject } 
                                          OPTIONAL {?subject rdfs:label ?label} }
        """
        store = store = TripleStore(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        results = store.select(sq, "python")
        for row in results:
            if 'label' in row:
                label = row['label']
            else:
                label = self.basefile_from_uri(row['subject'])
            terms[subj]['subjects'] = True

        self.log.debug("Retrieved subject terms from triplestore" % len(terms))

        for termset_func in self.termset_funcs:
            termset_func(terms)

        for term in terms:
            if not term:
                continue
            with self.store.open_downloaded(term, "w") as fp:
                for termset in sorted(terms[term]):
                    f.write(termset + "\n")

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
        # for a base name (term), create a skeleton xht2 file
        # containing a element of some kind for each term set this
        # term occurs in.
        baseuri = self.canonical_uri(doc.basefile)
        with self.store.open_downloaded(doc.basefile) as fp:
            termsets = fp.readlines()

        # FIXME: translate this to ferenda.elements, set doc.body to it
        root = etree.Element("html")
        root.set("xml:base", baseuri)
        root.set("xmlns", 'http://www.w3.org/2002/06/xhtml2/')
        root.set("xmlns:dct", util.ns['dct'])
        head = etree.SubElement(root, "head")
        title = etree.SubElement(head, "title")
        title.text = doc.basefile
        body = etree.SubElement(root, "body")
        heading = etree.SubElement(body, "h")
        heading.set("property", "dct:title")
        heading.text = doc.basefile
        if 'wikipedia\n' in termsets:
            p = etree.SubElement(body, "p")
            p.attrib['class'] = 'wikibox'
            p.text = 'Begreppet '
            a = etree.SubElement(p, "a")
            a.attrib['href'] = 'http://sv.wikipedia.org/wiki/' + \
                doc.basefile.replace(" ", "_")
            a.text = doc.basefile
            a.tail = ' finns även beskrivet på '
            a = etree.SubElement(p, "a")
            a.attrib['href'] = 'http://sv.wikipedia.org/'
            a.text = 'svenska Wikipedia'

        return etree.tostring(root, encoding='utf-8')

    re_tagstrip = re.compile(r'<[^>]*>')

    # FIXME: translate this to be consistent with construct_annotations
    # (e.g. return a RDF graph through one or a few SPARQL queries),
    # not a XML monstrosity
    def construct_annotations(self, uri):
        start = time()
        keyword = basefile.split("/", 1)[1]
        # note: infile is e.g. parsed/K/Konsument.xht2, but outfile is generated/Konsument.html
        infile = util.relpath(self._xmlFileName(basefile))
        outfile = util.relpath(self._htmlFileName(keyword))

        # Use SPARQL queries to create a rdf graph (to be used by the
        # xslt transform) containing enough information about all
        # cases using this term, as well as the wiki authored
        # dct:description for this term.

        # For proper SPARQL escaping, we need to change å to \u00E5
        # etc (there probably is a neater way of doing this).
        esckeyword = ''
        for c in keyword:
            if ord(c) > 127:
                esckeyword += '\\u%04X' % ord(c)
            else:
                esckeyword += c

        escuri = keyword_to_uri(esckeyword)

        sq = """
PREFIX dct:<http://purl.org/dc/terms/>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
PREFIX rinfo:<http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#>

SELECT ?desc
WHERE { ?uri dct:description ?desc . ?uri rdfs:label "%s"@sv }
""" % esckeyword
        wikidesc = self._store_select(sq)
        log.debug('%s: Selected %s descriptions (%.3f sec)',
                  basefile, len(wikidesc), time() - start)

        sq = """
PREFIX dct:<http://purl.org/dc/terms/>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
PREFIX rinfo:<http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#>

SELECT DISTINCT ?uri ?label
WHERE {
    GRAPH <urn:x-local:sfs> {
       { ?uri dct:subject <%s> .
         ?baseuri dct:title ?label .
         ?uri dct:isPartOf ?x . ?x dct:isPartOf ?baseuri
       }
       UNION {
         ?uri dct:subject <%s> .
         ?baseuri dct:title ?label .
         ?uri dct:isPartOf ?x . ?x dct:isPartOf ?y . ?y dct:isPartOf ?baseuri
       }
       UNION {
         ?uri dct:subject <%s> .
         ?baseuri dct:title ?label .
         ?uri dct:isPartOf ?x . ?x dct:isPartOf ?y . ?x dct:isPartOf ?z . ?z dct:isPartOf ?baseuri
       }
       UNION {
         ?uri dct:subject <%s> .
         ?baseuri dct:title ?label .
         ?uri dct:isPartOf ?x . ?x dct:isPartOf ?y . ?x dct:isPartOf ?z . ?z dct:isPartOf ?w . ?w dct:isPartOf ?baseuri
       }
    }
}

""" % (escuri, escuri, escuri, escuri)
        # print sq
        legaldefinitioner = self._store_select(sq)
        log.debug('%s: Selected %d legal definitions (%.3f sec)',
                  basefile, len(legaldefinitioner), time() - start)

        sq = """
PREFIX dct:<http://purl.org/dc/terms/>
PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
PREFIX rinfo:<http://rinfo.lagrummet.se/taxo/2007/09/rinfo/pub#>
PREFIX rinfoex:<http://lagen.nu/terms#>

SELECT ?uri ?id ?desc
WHERE {
    {
        GRAPH <urn:x-local:dv> {
            {
                ?uri dct:description ?desc .
                ?uri dct:identifier ?id .
                ?uri dct:subject <%s>
            }
            UNION {
                ?uri dct:description ?desc .
                ?uri dct:identifier ?id .
                ?uri dct:subject "%s"@sv
            }
        }
    } UNION {
        GRAPH <urn:x-local:arn> {
                ?uri dct:description ?desc .
                ?uri rinfoex:arendenummer ?id .
                ?uri dct:subject "%s"@sv
        }
    }
}
""" % (escuri, esckeyword, esckeyword)

        # Maybe we should handle <urn:x-local:arn> triples here as well?

        rattsfall = self._store_select(sq)
        log.debug('%s: Selected %d legal cases (%.3f sec)',
                  basefile, len(rattsfall), time() - start)

        root_node = etree.Element("rdf:RDF")
        for prefix in util.ns:
            etree._namespace_map[util.ns[prefix]] = prefix
            root_node.set("xmlns:" + prefix, util.ns[prefix])

        main_node = etree.SubElement(root_node, "rdf:Description")
        main_node.set("rdf:about", keyword_to_uri(keyword))

        for d in wikidesc:
            desc_node = etree.SubElement(main_node, "dct:description")
            xhtmlstr = "<xht2:div xmlns:xht2='%s'>%s</xht2:div>" % (
                util.ns['xht2'], d['desc'])
            xhtmlstr = xhtmlstr.replace(
                ' xmlns="http://www.w3.org/2002/06/xhtml2/"', '')
            desc_node.append(etree.fromstring(xhtmlstr.encode('utf-8')))

        for r in rattsfall:
            subject_node = etree.SubElement(main_node, "dct:subject")
            rattsfall_node = etree.SubElement(subject_node, "rdf:Description")
            rattsfall_node.set("rdf:about", r['uri'])
            id_node = etree.SubElement(rattsfall_node, "dct:identifier")
            id_node.text = r['id']
            desc_node = etree.SubElement(rattsfall_node, "dct:description")
            desc_node.text = r['desc']

        for l in legaldefinitioner:
            subject_node = etree.SubElement(main_node, "rinfoex:isDefinedBy")
            rattsfall_node = etree.SubElement(subject_node, "rdf:Description")
            rattsfall_node.set("rdf:about", l['uri'])
            id_node = etree.SubElement(rattsfall_node, "rdfs:label")
            # id_node.text = "%s %s" % (l['uri'].split("#")[1], l['label'])
            id_node.text = self.sfsmgr.display_title(l['uri'])

        # FIXME: construct graph
        return graph
