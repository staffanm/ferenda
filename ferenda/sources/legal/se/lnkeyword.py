# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from lxml import etree

from ferenda import TripleStore
from ferenda.sources.general import Keyword
from ferenda.sources.legal.se import SwedishLegalSource, SFS


class LNKeyword(Keyword):
    """Manages descriptions of legal concepts (Lagen.nu-version of Keyword)
    """
    namespaces = SwedishLegalSource.namespaces
    lang = "sv"

    def __init__(self, config=None, **kwargs):
        super(Keyword, self).__init__(config, **kwargs)
        self.termset_funcs = []
        if self.config._parent and hasattr(self.config._parent, "sfs"):
            self.sfsrepo = SFS(self.config._parent.sfs)
        else:
            self.sfsrepo = SFS()

    def canonical_uri(self, basefile):
        # FIXME: make configurable like SFS.canonical_uri
        capitalized = basefile[0].upper() + basefile[1:]
        return 'https://lagen.nu/concept/%s' % capitalized.replace(' ', '_')

    def basefile_from_uri(self, uri):
        prefix = "https://lagen.nu/concept/"
        if prefix in uri:
            return uri.replace(prefix, "").replace("_", " ")
        else:
            return super(LNKeyword, self).basefile_from_uri(uri)
        
    def prep_annotation_file_termsets(self, basefile, main_node):
        dvdataset = self.config.url + "dataset/dv"
        sfsdataset = self.config.url + "dataset/sfs"
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        legaldefs = self.time_store_select(store,
                                          "res/sparql/keyword_sfs.rq",
                                          basefile,
                                          sfsdataset,
                                          "legaldefs")
        rattsfall = self.time_store_select(store,
                                          "res/sparql/keyword_dv.rq",
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
            rattsfall_node = etree.SubElement(subject_node,
                                              ns("rdf:Description"))
            rattsfall_node.set(ns("rdf:about"), l['uri'])
            id_node = etree.SubElement(rattsfall_node, ns("rdfs:label"))
            # id_node.text = "%s %s" % (l['uri'].split("#")[1], l['label'])
            id_node.text = self.sfsrepo.display_title(l['uri'])

    def tabs(self):
        return [("Begrepp", self.dataset_uri())]
