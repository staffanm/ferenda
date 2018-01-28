# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os
import sys
from urllib.parse import quote, unquote
from wsgiref.util import request_uri
from collections import OrderedDict

from lxml import etree
from rdflib.namespace import DCTERMS

from ferenda import util
from ferenda import TripleStore, Facet, RequestHandler
from ferenda.elements import Body, UnorderedList, ListItem, Link
from ferenda.elements.html import Div, H2
from ferenda.sources.general import keyword
from ferenda.sources.legal.se import SwedishLegalSource
from . import SameAs, SFS  # for the keyword_uri implementation

class LNKeywordHandler(RequestHandler):
    def supports(self, environ):
        if environ['PATH_INFO'].startswith("/dataset/"):
            return super(LNKeywordHandler, self).supports(environ)
        return environ['PATH_INFO'].startswith("/begrepp/")


class LNKeyword(keyword.Keyword, SameAs):
    """Manages descriptions of legal concepts (Lagen.nu-version of Keyword)
    """
    requesthandler_class = LNKeywordHandler
    namespaces = SwedishLegalSource.namespaces
    lang = "sv"
    if sys.platform == "darwin":
        collate_locale = "sv_SE.ISO8859-15"
    else:
        collate_locale = "sv_SE.UTF-8"

    def __init__(self, config=None, **kwargs):
        super(LNKeyword, self).__init__(config, **kwargs)
        # FIXME: Don't bother with the large wp download right now
        # (but reinstate later)
        # self.termset_funcs.remove(self.download_termset_wikipedia)
        if self.config._parent and hasattr(self.config._parent, "sfs"):
            self.sfsrepo = SFS(self.config._parent.sfs)
        else:
            self.sfsrepo = SFS()

    def sanitize_term(self, term):
        sanitized = super(LNKeyword, self).sanitize_term(term)
        if sanitized is not None:
            # handle word inflections etc ("personuppgifter" -> "personuppgift")
            return sanitized
        # else return None
            
    def canonical_uri(self, basefile):
        return self.keyword_uri(basefile)

    def basefile_from_uri(self, uri):
        prefix = "https://lagen.nu/begrepp/"
        if prefix in uri:
            return unquote(uri.replace(prefix, "").replace("_", " ").replace("//", "»"))
        else:
            return super(LNKeyword, self).basefile_from_uri(uri)
        
    def _download_termset_mediawiki_titles(self):
        for basefile in self.mediawikirepo.store.list_basefiles_for("parse"):
            if not basefile.startswith("SFS/"):
                yield basefile

    def prep_annotation_file_termsets(self, basefile, main_node):
        dvdataset = self.config.url + "dataset/dv"
        sfsdataset = self.config.url + "dataset/sfs"
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        legaldefs = self.time_store_select(store,
                                          "sparql/keyword_sfs.rq",
                                          basefile,
                                          sfsdataset,
                                          "legaldefs")
        rattsfall = self.time_store_select(store,
                                          "sparql/keyword_dv.rq",
                                          basefile,
                                          dvdataset,
                                          "legalcases")

        # compatibility hack to enable lxml to process qnames for
        # namespaces FIXME: this is copied from sfs.py -- but could
        # probably be removed once we rewrite this method to use real
        # RDFLib graphs
        def ns(string):
            if ":" in string:
                prefix, tag = string.split(":", 1)
                return "{%s}%s" % (str(self.ns[prefix]), tag)

        for r in rattsfall:
            subject_node = etree.SubElement(main_node, ns("dcterms:subject"))
            rattsfall_node = etree.SubElement(subject_node,
                                              ns("rdf:Description"))
            rattsfall_node.set(ns("rdf:about"), r['uri'])
            id_node = etree.SubElement(rattsfall_node,
                                       ns("dcterms:identifier"))
            id_node.text = r['id']
            desc_node = etree.SubElement(rattsfall_node,
                                         ns("dcterms:description"))
            desc_node.text = r['desc']

        for l in legaldefs:
            subject_node = etree.SubElement(main_node,
                                            ns("rinfoex:isDefinedBy"))
            legaldef_node = etree.SubElement(subject_node,
                                              ns("rdf:Description"))
            legaldef_node.set(ns("rdf:about"), l['uri'])
            id_node = etree.SubElement(legaldef_node, ns("rdfs:label"))
            # id_node.text = "%s %s" % (l['uri'].split("#")[1], l['label'])
            id_node.text = self.sfsrepo.display_title(l['uri'])

        if 'wikipedia\n' in util.readfile(self.store.downloaded_path(basefile)):
            subject_node = etree.SubElement(main_node,
                                            ns("rdfs:seeAlso"))
            link_node = etree.SubElement(subject_node,
                                         ns("rdf:Description"))
            link_node.set(ns("rdf:about"), 'http://sv.wikipedia.org/wiki/' + basefile.replace(" ","_"))
            label_node = etree.SubElement(link_node, ns("rdfs:label"))
            label_node.text = "Begreppet %s finns även beskrivet på svenska Wikipedia" % basefile


    def facets(self):
        def kwselector(row, binding, resource_graph):
            bucket = row[binding][0]
            if bucket.isalpha():
                return bucket.upper()
            else:
                return "#"

        return [Facet(DCTERMS.title,
                      label="Ordnade efter titel",
                      pagetitle='Begrepp som b\xf6rjar p\xe5 "%(selected)s"',
                      selector=kwselector)
        ]

    # override simply to be able to specify that title/A should be first, not title/#
    def toc_generate_first_page(self, pagecontent, pagesets, otherrepos=[]):
        """Generate the main page of TOC pages."""
        for firstpage in pagesets[0].pages:
            if firstpage.value == "a":
                break
        else:
            firstpage = pagesets[0].pages[0]
        documents = pagecontent[(firstpage.binding, firstpage.value)]
        return self.toc_generate_page(firstpage.binding, firstpage.value,
                                      documents, pagesets, "index", otherrepos)

    def toc_generate_page_body(self, documentlist, nav):
        # make a copy because toc_generate_page_body_thread will eat
        # it, and we need to reuse it
        documentlist = list(documentlist)
        # for item in documentlist:
        #     print(repr(str(item[0]))+",")
        rootul = self.toc_generate_page_body_thread(documentlist)
        assert len(documentlist) == 0, "toc_generate_page_body_thread left some items in the documentlist"
        uls = OrderedDict()
        # create one ul per two-char-prefix (eg "Ab", "Ac", "Ad", "Af" and so on)
        for li in rootul:
            strdoc = str(li)
            prefix = strdoc.replace(" ","").replace("-", "")[:2].capitalize() # maybe clean even more, eg remove space?
            # remove anything non-numerical
            if prefix not in uls:
                uls[prefix] = UnorderedList()
                currentul = uls[prefix]
            currentul.append(li)
        d = Div(**{'class': 'threecol'})
        for k, v in uls.items():
            if len(k) > 2:
                continue
            d.append(H2([k]))
            d.append(v)
        return Body([nav,d])
        
    def toc_generate_page_body_thread(self, documentlist, rootsegments=()):
        def remove_rootsegments(item, rootsegments):
            linktext = str(item[0])
            prefix = "»".join(rootsegments)
            assert linktext.startswith(prefix), "Tried to remove prefix %s from %s" % (prefix, linktext)
            prefixlen = len(prefix)
            if prefixlen:
                prefixlen += 1 # removes extra "»", but not if rootsegments is empty
            if linktext[prefixlen:]:
                return [Link(linktext[prefixlen:], uri=item[0].uri)]
            else:
                return []

        ul = UnorderedList()
        doc = documentlist.pop(0)
        try:
            while doc:
                strdoc = str(doc[0])
                segments = tuple(strdoc.split("»"))
                # check if we're the same depth or deeper
                if rootsegments == segments[:len(rootsegments)]:
                    if len(segments) > len(rootsegments) + 1:
                        # OK, this indicates a sublist to our current list
                        documentlist.insert(0,doc)
                        if (not ul or # we're in a brand new sub-ul
                            not isinstance(ul[-1][0], Link) or # previous li is a phantom
                            not str(ul[-1][0]).endswith(segments[len(rootsegments)])): # previous li is a different branch than our current
                            # create phantom entry
                            ul.append(ListItem([segments[len(rootsegments)]]))
                        ul[-1].append(
                            self.toc_generate_page_body_thread(documentlist, segments[:len(rootsegments)+1]))
                    else:
                        ul.append(ListItem(remove_rootsegments(doc, rootsegments)))
                # ok we're not, pop and return
                else:
                    documentlist.insert(0,doc)
                    return ul
                doc = documentlist.pop(0)
        except IndexError: # ok the list is done
            return ul
            
    news_feedsets_main_label = "Alla nya och ändrade begrepp"


    def tabs(self):
        return [("Begrepp", self.dataset_uri())]

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
        return "%s begrepp" % len(set([row['uri'] for row in self.faceted_data()]))
        

