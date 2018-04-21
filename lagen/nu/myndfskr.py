# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from operator import attrgetter
from collections import Counter
import re
import os
from urllib.parse import unquote
from wsgiref.util import request_uri
from itertools import chain

from rdflib import RDF, URIRef
from rdflib.namespace import DCTERMS, SKOS
from ferenda.sources.legal.se import RPUBL

from ferenda.sources.legal.se import myndfskr
from ferenda import (CompositeRepository, CompositeStore, Facet, TocPageset,
                     TocPage, RequestHandler)
from ferenda import util, fulltextindex
from ferenda.elements import Link
from ferenda.sources.legal.se import (SwedishLegalSource, SwedishLegalStore)
from . import SameAs


# inherit list_basefiles_for from CompositeStore, basefile_to_pathfrag
# from SwedishLegalStore)
class MyndFskrStore(CompositeStore, SwedishLegalStore):
    pass

class MyndFskrHandler(RequestHandler):
    def supports(self, environ):
        # resources are at /dvfs/2013:1
        # datasets are at /dataset/myndfs?difs=2013
        segment = environ['PATH_INFO'].split("/")[1]
        if segment == "dataset":
            return super(MyndFskrHandler, self).supports(environ)
        fs = chain.from_iterable([self.repo.get_instance(cls).forfattningssamlingar() for cls in self.repo.subrepos])
        return segment in fs

    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        if basefile and suffix == "png":
            # fill params so that RequestHandler.get_pathfunc can do
            # its page-extracting thing
            # 
            # FIXME: Maybe the first segment isn't always equal to the
            # correct repo name?
            params["repo"] = environ["PATH_INFO"].split("/")[1]
            if "dir" not in params:
                params["dir"] = "downloaded"
                # ".../sid4.png" => "3" (because 0-based) 
                params["page"] = str(int(environ["PATH_INFO"].split("/sid")[1][:-4])-1)
            params["format"] = suffix
        return super(MyndFskrHandler, self).get_pathfunc(environ, basefile, params, contenttype, suffix)


class MyndFskr(CompositeRepository, SwedishLegalSource):
    alias = "myndfs"
    storage_policy = 'dir'
    xslt_template = "xsl/paged.xsl"
    extrabases = SameAs,
    loadpath = [os.path.dirname(__file__) + os.sep + "res"]
    subrepos = [
        myndfskr.AFS,
        myndfskr.BOLFS,
        myndfskr.DIFS,
        myndfskr.DVFS,
        myndfskr.EIFS,
        myndfskr.ELSAKFS,
        myndfskr.FFFS,
        myndfskr.FFS,
        myndfskr.KFMFS,
        myndfskr.KOVFS,
        myndfskr.KVFS,
        myndfskr.LIFS,
        myndfskr.LMFS,
        myndfskr.LVFS,
        myndfskr.MIGRFS,
        myndfskr.MPRTFS,
        myndfskr.MSBFS,
        myndfskr.MYHFS,
        myndfskr.NFS,
        myndfskr.RAFS,
        myndfskr.RGKFS,
        myndfskr.RIFS,
        myndfskr.SJVFS,
        myndfskr.SKVFS,
        myndfskr.SOSFS,
        # myndfskr.STAFS,    #            -""-
        myndfskr.STFS,
        myndfskr.SvKFS,
    ]
    rdf_type = (RPUBL.Myndighetsforeskrift, RPUBL.AllmannaRad)
    namespaces = ['rdf', 'rdfs', 'xsd', 'dcterms', 'skos', 'foaf',
                  'xhv', 'xsi', 'owl', 'prov', 'bibo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]
    sparql_annotations = None  # until we can speed things up
    documentstore_class = MyndFskrStore
    requesthandler_class = MyndFskrHandler
    urispace_segment = ""

    # mapping to get basefile_from_uri to work. This is a ugly hack,
    # the proper way would be to call the bsefile_from_uri method in
    # the appropriate subrepo, but that turned out to be hard.
    basefile_fs = {"elsaekfs": "elsakfs",
                   "saeifs": "säifs"}

    @classmethod
    def get_default_options(cls):
        opts = super(MyndFskr, cls).get_default_options()
        opts['pdfimages'] = True
        if 'cssfiles' not in opts:
            opts['cssfiles'] = []
        opts['cssfiles'].append('css/pdfview.css')
        if 'jsfiles' not in opts:
            opts['jsfiles'] = []
        opts['jsfiles'].append('js/pdfviewer.js')
        return opts

    def metadata_from_basefile(self, basefile):
        # FIXME: Copied from
        # ferenda.sources.legal.MyndFskrBase.metadata_from_basefile
        fs, realbasefile = basefile.split("/")
        fs = fs.upper()
        # fs = self._basefile_frag_to_altlabel(fs)
        fs = myndfskr.MyndFskrBase._basefile_frag_to_altlabel(self, fs)
        a = super(MyndFskr, self).metadata_from_basefile(basefile)
        a["rpubl:arsutgava"], a["rpubl:lopnummer"] = realbasefile.split(":", 1)
        a["rpubl:forfattningssamling"] = self.lookup_resource(fs, SKOS.altLabel)
        return a

    def basefile_from_uri(self, uri):
        # FIXME: Adapted from
        # ferenda.sources.legal.MyndFskrBase.metadata_from_basefile
        if '/sid' in uri and uri.endswith(".png"):
            uri = uri.split("/sid")[0]
        # FIXME: We should call the appropriate subrepos
        # basefile_from_uri, not the superclass
        basefile = super(MyndFskr, self).basefile_from_uri(uri)
        if basefile and basefile.count("/") == 1:
            basefile = basefile.replace("-", "")
            fs, realbasefile = basefile.split("/")
            if fs != "sfs" and fs.endswith("fs"):
                # adjust some tricky URIs to get the basefile fs from
                # them
                fs = self.basefile_fs.get(fs, fs)
                return fs + "/" + realbasefile

    # This custom implementation of download is able to select a
    # particular subrepo and call its download method. That way we
    # don't have to enable the subrepo specifically, eg:
    #
    # ./ferenda-build.py myndfs download bolfs:
    def download(self, basefile=None, reporter=None):
        if basefile:
            # expect a basefile on the form "subrepoalias:basefile" or
            # just "subrepoalias:"
            subrepoalias, basefile = basefile.split(":")
        else:
            subrepoalias = None
        if not basefile:
            basefile = None  # ie convert '' => None
        found = False
        for cls in self.subrepos:
            if (subrepoalias is None or
                cls.alias == subrepoalias):
                found = True
                inst = self.get_instance(cls)
                # the feature where subrepos has a slighly higher
                # loglevel to avoid creating almost-duplicate "OK" log
                # messages is not useful for downloading. So we work
                # around it here.
                subrepo_loglevel = inst.log.getEffectiveLevel()
                if subrepo_loglevel == self.log.getEffectiveLevel() + 1:
                    inst.log.setLevel(self.log.getEffectiveLevel())
                basefiles = []
                try:
                    ret = inst.download(basefile, reporter=basefiles.append)
                except Exception as e:
                    loc = util.location_exception(e)
                    self.log.error("download for %s failed: %s (%s)" % (cls.alias, e, loc))
                    ret = False
                finally:
                    inst.log.setLevel(subrepo_loglevel)
                    for b in basefiles:
                        util.link_or_copy(inst.store.documententry_path(b),
                                          self.store.documententry_path(b))
                    # msbfs/entries/.root.json -> myndfs/entries/msbfs.json
                    util.link_or_copy(inst.store.documententry_path(".root"),
                                      self.store.documententry_path(inst.alias))
        if not found:
            self.log.error("Couldn't find any subrepo with alias %s" % subrepoalias)
            

    # This custom implementation of parse is able to select a
    # particular subrepo and parse using that, eg::
    #
    # ./ferenda-build.py myndfs parse bolfs:bolfs/2012:1
    def parse(self, basefile):
        subrepoalias, subbasefile = basefile.split(":", 1)
        if re.match("[a-zåäö\-]+fs$", subrepoalias, re.IGNORECASE):
            for cls in self.subrepos:
                if cls.alias == subrepoalias:
                    inst = self.get_instance(cls)
                    ret = inst.parse(subbasefile)
                    if ret:
                        if ret is not True and ret != subbasefile:
                            # this is a signal that parse discovered
                            # that the basefile was wrong
                            subbasefile = ret
                        self.copy_parsed(subbasefile, inst)
                    break
        else:
             return super(MyndFskr, self).parse(basefile)   
    
    def facets(self):
        # maybe if each entry in the list could be a tuple or a single
        # element. If it's a tuple, then the first elements' selector
        # values (eg organizations) become top level facets, the
        # second elements' selector values become subsection
        # underneath, and possibly more levels.
        def altlabel(row, binding, resource_graph):
            uri = URIRef(row[binding])
            if resource_graph.value(uri, SKOS.altLabel):
                return str(resource_graph.value(uri, SKOS.altLabel))
            else:
                return row[binding]

        def mainfs(row, binding, resource_graph):
            uri = URIRef(row[binding])
            mainuri = resource_graph.value(uri, DCTERMS.isReplacedBy)
            if mainuri:
                uri = mainuri
            return util.uri_leaf(uri)
            
        return [Facet(RPUBL.forfattningssamling,
                      selector=altlabel,
                      identificator=mainfs,
                      use_for_toc=True),
                Facet(RPUBL.arsutgava,
                      indexingtype=fulltextindex.Label(),
                      selector_descending=True,
                      use_for_toc=True),
                Facet(RDF.type, use_for_toc=False, use_for_feed=False),
                Facet(DCTERMS.title, use_for_toc=False),
                Facet(DCTERMS.publisher, use_for_toc=False,
                      pagetitle="Författningar utgivna av %(selected)s"),
                Facet(DCTERMS.identifier)] + self.standardfacets

    def toc_pagesets(self, data, facets):
        # FIXME: Main structure of this (create a two-level hierarchy
        # based on two different facets) mirrors the dv.py
        # toc_pagesets and could possibly be abstracted.
        pagesetdict = {}
        labelsets = {} 
        selector_values = {}
        for row in data:
            pagesetid = facets[0].identificator(row,
                                                'rpubl_forfattningssamling',
                                                self.commondata)
            altlabel = facets[0].selector(row, 'rpubl_forfattningssamling', self.commondata)
            if "|" in altlabel:
                mainaltlabel, altaltlabel = altlabel.split
            else:
                mainaltlabel = altaltlabel = altlabel

            # this makes sure that each value in labelsets is a array
            # with the main preflabel and altlabel first (eg ["Statens
            # Jordbruksverks författningssamling, "SJVFS"]), and
            # alternate altlabels (eg DFS) later (in an arbitrary
            # order).
            if pagesetid not in labelsets:
                preflabel = self.commondata.value(URIRef(row['rpubl_forfattningssamling']),
                                                  SKOS.prefLabel)
                labelsets[pagesetid] = [preflabel, mainaltlabel]
            if altaltlabel not in labelsets[pagesetid]:
                labelsets[pagesetid].append(altaltlabel)
                
            selected = facets[1].selector(row, 'rpubl_arsutgava', self.commondata)
            selector_values[(pagesetid, selected)] = True
        for (pagesetid, value) in sorted(list(selector_values.keys()), reverse=True):
            if pagesetid not in pagesetdict:
                # generate eg "Skatteverkets författningssamling (SKVFS, RSFS)
                labels = labelsets[pagesetid]
                preflabel = labels.pop(0)
                pslabel = "%s (%s)" % (preflabel, ", ".join(labels))
                pagesetdict[pagesetid] = TocPageset(label=pslabel,
                                                    predicate=pagesetid,  # ??
                                                    pages=[])
            pageset = pagesetdict[pagesetid]
            pageset.pages.append(TocPage(linktext=value,
                                         title="%s från %s" % (pageset.label, value),
                                         binding=pagesetid,
                                         value=value))
        return sorted(pagesetdict.values(), key=attrgetter('label'))

    def toc_select_for_pages(self, data, pagesets, facets):
        def sortkey(doc):
            return util.split_numalpha(doc['dcterms_identifier'])
        # FIXME: Again, this mirrors the dv.py structure
        res = {}
        documents = {}
        for row in data:
            key = (facets[0].identificator(row, 'rpubl_forfattningssamling', self.commondata),
                   facets[1].selector(row, 'rpubl_arsutgava', self.commondata))
            if key not in documents:
                documents[key] = []
            documents[key].append(row)
        pagesetdict = {}
        for pageset in pagesets:
            pagesetdict[pageset.predicate] = pageset
        for (binding, value) in sorted(documents.keys()):
            pageset = pagesetdict[binding]
            s = sorted(documents[(binding, value)], key=sortkey)
            res[(binding, value)] = [self.toc_item(binding, row)
                                     for row in s]
        return res

    news_feedsets_main_label = "Samtliga föreskrifter"
    news_sortkey = "orig_created"
    def news_item(self, binding, entry):
        if entry["orig_created"] is None:
            self.log.warning("%s: No orig_created found" % entry["basefile"])
            entry["orig_created"] = entry["published"]
        entry['title'] = "%s: %s" % (entry['dcterms_identifier'], entry.get('dcterms_title', '(Titel saknas)'))
        # FIXME: Set entry|'published'] to rpubl_utkomFranTrycket when available
        entry['published'] = entry['orig_created']
        return entry


    def tabs(self):
        return [("Föreskrifter", self.dataset_uri())]

    def frontpage_content_body(self):
        c = Counter([row['rpubl_forfattningssamling'] for row in self.faceted_data()])
        return ("%s författningar från %s författningssamlingar" % (
            sum(c.values()), len(c)))


