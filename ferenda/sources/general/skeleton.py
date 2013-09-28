# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import sys
import re
from six.moves.urllib_parse import urljoin

from rdflib import Graph
from rdflib import URIRef
from rdflib import Literal
from rdflib import RDF
from rdflib import RDFS
from lxml import etree
import requests

from ferenda import DocumentRepository, TripleStore


class Skeleton(DocumentRepository):

    """Utility docrepo to fetch all RDF data from a triplestore (either
       our triple store, or a remote one, fetched through the combined
       ferenda atom feed), find out those resources that are referred
       to but not present in the data (usually older documents that
       are not available in electronic form), and create "skeleton
       entries" for those resources.

    """

    alias = "closet"
    start_url = "http://rinfo.demo.lagrummet.se/feed/current"
    downloaded_suffix = ".nt"

    def download(self):
        graph = self.download_from_triplestore()
        # or, alternatively
        graph = self.download_from_atom()

    def download_from_triplestore(self):
        sq = "SELECT ?something ?references ?uri where ?something ?references ?uri AND NOT ?uri ?references ?anything"
        store = TripleStore(self.config.storetype,
                            self.config.storelocation,
                            self.config.storerepository)
        with self.store.open_downloaded("biggraph") as fp:
            for row in store.select(sq):
                fp.write("<%(something)s> <%(references)s> <%(uri)s> .\n")

    def download_from_atom(self):
        refresh = self.config.force
        feed_url = self.start_url
        ns = 'http://www.w3.org/2005/Atom'
        done = False
        biggraph = Graph()
        biggraph.bind("dct", self.ns['dct'])
        biggraph.bind("rpubl", self.ns['rpubl'])

        while not done:
            self.log.info("Feed: %s" % feed_url)
            tree = etree.parse(requests.get(feed_url).text)
            for entry in tree.findall('{%s}entry' % (ns)):
                try:
                    self.log.info("  Examining entry")
                    rdf_url = None
                    for node in entry:
                        if (node.tag == "{%s}link" % ns and
                                node.get('type') == 'application/rdf+xml'):
                            rdf_url = urljoin(feed_url, node.get("href"))
                        elif (node.tag == "{%s}content" % ns and
                              node.get('type') == 'application/rdf+xml'):
                            rdf_url = urljoin(feed_url, node.get("src"))

                    if rdf_url:
                        self.log.info("    RDF: %s" % rdf_url)
                        g = Graph()
                        g.parse(requests.get(rdf_url).text)
                        for triple in g:
                            s, p, o = triple
                            if (not isinstance(o, URIRef) or
                                    not str(o).startswith(self.config.url)):
                                g.remove(triple)

                        self.log.debug("     Adding %s triples" % len(g))
                        biggraph += g
                except KeyboardInterrupt:
                    raise
                except:
                    e = sys.exc_info()[1]
                    self.log.error("ERROR: %s" % e)

            done = True
            for link in list(tree.findall('{%s}link' % (ns))):
                self.log.info("  Examining link")
                if link.get('rel') == 'prev-archive':
                    feed_url = urljoin(feed_url, link.get("href"))
                    done = False
                    # done = True

        self.log.info("Done downloading")
        with self.store.open_downloaded("biggraph", "wb") as fp:
            fp.write(biggraph.serialize(format="nt"))

    def parse(self, basefile):
        # Find out possible skeleton entries by loading the entire
        # graph of resource references, and find resources that only
        # exist as objects.
        #
        # Note: if we used download_from_triplestore we know that this list
        #       is clean -- we could just iterate the graph w/o filtering
        g = Graph()
        self.log.info("Parsing %s" % basefile)
        g.parse(self.store.downloaded_path(basefile), format="nt")
        self.log.info("Compiling object set")
        # create a uri -> True dict mapping -- maybe?
        objects = dict(zip([str(o).split("#")[0] for (s, p, o) in g], True))
        self.log.info("Compiling subject set")
        subjects = dict(zip([str(s).split("#")[0] for (s, p, o) in g], True))
        self.log.info("%s objects, %s subjects. Iterating through existing objects" %
                      (len(objects), len(subjects)))

        for o in objects:
            if not o.startswith(self.config.url):
                continue
            if '9999:999' in o:
                continue
            if o in subjects:
                continue
            for repo in otherrepos:
                skelbase = repo.basefile_from_uri(repo)
                if skelbase:
                    skel = repo.triples_from_uri(o)  # need to impl
                    with self.store.open_distilled(skelbase, "wb") as fp:
                        fp.write(skel.serialize(format="pretty-xml"))

                    self.log.info("Created skel for %s" % o)

    # FIXME: Move this to SwedishLegalSource -- also unify
    # triples_from_uri with SwedishLegalSource.infer_triples(basefile)
    RATTSFALL = 1
    KONSOLIDERAD = 2
    FORESKRIFT = 3
    PROPOSITION = 4
    UTREDNING = 5

    def triples_from_uri(self, uri):

        types = {self.RATTSFALL: self.ns['rpubl']["Rattsfallsreferat"],
                 self.KONSOLIDERAD: self.ns['rpubl']["KonsolideradGrundforfattning"],
                 self.FORESKRIFT: self.ns['rpubl']["Myndighetsforeskrift"],
                 self.PROPOSITION: self.ns['rpubl']["Proposition"],
                 self.UTREDNING: self.ns['rpubl']["Utredningsbetankande"],
                 }

        # Maps keys used by the internal dictionaries that LegalRef
        # constructs, which in turn are modelled after production rule names
        # in the EBNF grammar.
        predicate = {"type": RDF.type,
                     "rf": self.ns['rpubl']["rattsfallspublikation"],
                     "fs": self.ns['rpubl']["forfattningssamling"],
                     "artal": self.ns['rpubl']["artal"],
                     "lopnummer": self.ns['rpubl']["lopnummer"],
                     "sidnummer": self.ns['rpubl']["sidnummer"],
                     "arsutgava": self.ns['rpubl']["arsutgava"],
                     "kapitel": self.ns['rpubl']["kapitel"],
                     "paragraf": self.ns['rpubl']["paragraf"],
                     "identifier": self.ns['dct']["identifier"],
                     }

        patterns = {self.RATTSFALL:
                    re.compile(
                        "http://rinfo.lagrummet.se/publ/rf/(?P<rf>\w+)/(?P<arsutgava>\d+)(/|)(?P<sep>[s:])(_(?P<sidnummer>\d+)|(?P<lopnummer>\d+))").match,
                    self.KONSOLIDERAD:
                    # NB: These shouldn't have any
                    # rpubl:forfattningssamling triples.
                    re.compile(
                        "http://rinfo.lagrummet.se/publ/sfs/(?P<arsutgava>\d{4}):(?P<lopnummer>\w+)#?(k_(?P<kapitel>[0-9a-z]+))?(p_(?P<paragraf>[0-9a-z]+))?").match,
                    self.FORESKRIFT:
                    re.compile(
                        "http://rinfo.lagrummet.se/publ/(?P<fs>[\w-]+fs)/(?P<arsutgava>\d{4}):(?P<lopnummer>\w+)").match,
                    self.UTREDNING:
                    re.compile(
                        "http://rinfo.lagrummet.se/publ/(?P<utr>(sou|ds))/(?P<arsutgava>\d{4}(/\d{2}|)):(?P<lopnummer>\w+)").match,
                    self.PROPOSITION:
                    re.compile(
                        "http://rinfo.lagrummet.se/publ/(?P<prop>prop)/(?P<arsutgava>\d{4}(/\d{2}|)):(?P<lopnummer>\w+)").match
                    }

        identifier = {self.RATTSFALL: "%(rf)s %(arsutgava)s%(sep)s%(lopnummer)s",
                      self.KONSOLIDERAD: "SFS %(arsutgava)s:%(lopnummer)s",
                      self.FORESKRIFT: "%(fs)s %(arsutgava)s:%(lopnummer)s",
                      self.PROPOSITION: "Prop. %(arsutgava)s:%(lopnummer)s",
                      self.UTREDNING: "%(utr)s. %(arsutgava)s:%(lopnummer)s"
                      }

        dictionary = None
        for (pid, pattern) in list(patterns.items()):
            m = pattern(uri)
            if m:
                dictionary = m.groupdict()
                dictionary["type"] = pid
                break

        if not dictionary:
            raise ValueError("Can't parse URI %s" % uri)

        graph = Graph()
        for key, value in list(self.ns.items()):
            graph.bind(key, value)
        subj = URIRef(uri)
        for key in dictionary:
            if dictionary[key] is None:
                continue
            if key.startswith("_"):
                continue
            if key == "type":
                graph.add((subj, RDF.type, URIRef(types[dictionary[key]])))
            elif key in ("fs", "rf", "utr"):
                uri = "http://rinfo.lagrummet.se/serie/%s/%s" % (
                    key, dictionary[key])
                graph.add((subj, predicate[key], URIRef(uri)))
            elif key in ("prop"):
                pass
                #uri = "http://rinfo.lagrummet.se/serie/%s" % key
                #graph.add((subj, predicate[key], URIRef(uri)))
            elif key in ("sep"):
                pass
            else:
                graph.add((subj, predicate[key], Literal(dictionary[key])))

        id_templ = identifier[dictionary["type"]]

        if 'sep' in dictionary and dictionary['sep'] == "s":
            # Extra handling of NJA URIs
            dictionary['sep'] = " s. "
            dictionary['lopnummer'] = dictionary['sidnummer']
        for key in ('fs', 'rf'):
            if key in dictionary:
                dictionary[key] = dictionary[key].upper()

        graph.add(
            (subj, predicate["identifier"], Literal(id_templ % dictionary)))

        graph.add(
            (subj, RDFS.comment, Literal("Detta dokument finns inte i elektronisk form i rättsinformationssystemet")))

        return graph
