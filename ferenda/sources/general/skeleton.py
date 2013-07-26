#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
#
# A utility class to fetch all RDF data from a remote RDL system, find
# out those resources that are referred to but not present in the data
# (usually older documents that are not available in electronic form),
# and create "skeleton entries" for those resources.
import sys
import re
import xml.etree.ElementTree as ET
from collections import Set

from rdflib import Graph, URIRef, Literal, BNode, RDF, RDFS

from ferenda import util
from ferenda import DocumentRepository


class Skeleton(DocumentRepository):
    module_dir = "closet"
    start_url = "http://rinfo.demo.lagrummet.se/feed/current"

    def downloaded_path(self, basefile):
        return self.generic_path(basefile, 'downloaded', '.nt')

    def download(self, usecache=False):
        refresh = self.get_moduleconfig('refresh', bool, False)
        feed_url = self.start_url
        ns = 'http://www.w3.org/2005/Atom'
        done = False
        biggraph = Graph()
        biggraph.bind("dct", self.ns['dct'])
        biggraph.bind("rpubl", self.ns['rpubl'])

        while not done:
            self.log.info("Feed: %s" % feed_url)
            tree = ET.parse(urlopen(feed_url))
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
                        g.parse(urlopen(rdf_url))
                        for triple in g:
                            s, p, o = triple
                            if not isinstance(o, URIRef):
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
        outfile = self.downloaded_path("biggraph")
        util.ensure_dir(outfile)
        fp = open(outfile, "w")
        fp.write(biggraph.serialize(format="nt"))
        fp.close()

    def parse(self, basefile):
        # Find out possible skeleton entries by loading the entire
        # graph of resource references, and find resources that only
        # exist as objects. Save URIs as data/closet/intermediate/biggraph.txt
        g = Graph()
        self.log.info("Parsing %s" % basefile)
        g.parse(self.downloaded_path(basefile), format="nt")
        self.log.info("Compiling object set")
        # FIXME: This syntax is not py26 compatible. Change into
        # something that is.
        # objects = {str(o).split("#")[0] for s, p, o in g}
        self.log.info("Compiling subject set")
        # subjects = {str(s).split("#")[0] for s, p, o in g}
        self.log.info("%s objects, %s subjects. Iterating through existing objects" % (len(objects), len(subjects)))
        skelfile = self.generic_path(basefile, 'intermediate', '.txt')
        util.ensure_dir(skelfile)
        with open(skelfile, "w") as fp:
            for o in objects:
                if not o.startswith("http://rinfo.lagrummet.se/publ/"):
                    continue
                if '9999:999' in o:
                    continue
                self.log.info("Examining object %s" % o)
                if not o in subjects:
                    self.log.info("...not found as a subject, creating skel")
                    fp.write(o + "\n")

        self.log.info("Created skel uri file")

    def generate(self, basefile):
        # Iterate through the list of URIs gathered by parse() and
        # create skeleton entries in RDF/XML for these.
        skelfile = self.generic_path(basefile, 'intermediate', '.txt')
        with open(skelfile) as fp:
            for uri in fp:
                uri = uri.strip()
                try:
                    skel = self.parse_uri(uri)
                except ValueError as e:
                    self.log.error("ERROR: %s" % e)
                    continue

                basefile = urlparse(uri).path[1:]
                outfile = self.distilled_path(basefile)
                util.ensure_dir(outfile)
                fp = open(outfile, "w")
                fp.write(skel.serialize(format="pretty-xml"))
                self.log.info("Serialized '%s'" % basefile)

    RATTSFALL = 1
    KONSOLIDERAD = 2
    FORESKRIFT = 3
    PROPOSITION = 4
    UTREDNING = 5

    def parse_uri(self, uri):

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
                            re.compile("http://rinfo.lagrummet.se/publ/rf/(?P<rf>\w+)/(?P<arsutgava>\d+)(/|)(?P<sep>[s:])(_(?P<sidnummer>\d+)|(?P<lopnummer>\d+))").match,
                    self.KONSOLIDERAD:
                    # NB: These shouldn't have any
                    # rpubl:forfattningssamling triples.
                    re.compile("http://rinfo.lagrummet.se/publ/sfs/(?P<arsutgava>\d{4}):(?P<lopnummer>\w+)#?(k_(?P<kapitel>[0-9a-z]+))?(p_(?P<paragraf>[0-9a-z]+))?").match,
                    self.FORESKRIFT:
                    re.compile("http://rinfo.lagrummet.se/publ/(?P<fs>[\w-]+fs)/(?P<arsutgava>\d{4}):(?P<lopnummer>\w+)").match,
                    self.UTREDNING:
                    re.compile("http://rinfo.lagrummet.se/publ/(?P<utr>(sou|ds))/(?P<arsutgava>\d{4}(/\d{2}|)):(?P<lopnummer>\w+)").match,
                    self.PROPOSITION:
                    re.compile("http://rinfo.lagrummet.se/publ/(?P<prop>prop)/(?P<arsutgava>\d{4}(/\d{2}|)):(?P<lopnummer>\w+)").match
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

        #self.log.info("templ %s, dict %r, id '%s'" %
        #              (id_templ,dictionary,id_templ%dictionary))
        graph.add(
            (subj, predicate["identifier"], Literal(id_templ % dictionary)))

        graph.add((subj, RDFS.comment, Literal("Detta dokument finns inte i elektronisk form i rättsinformationssystemet")))

        return graph
