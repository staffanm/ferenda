# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import os

from bs4 import BeautifulSoup
from docutils.core import publish_string
from rdflib import URIRef, Graph, Literal, Namespace
from rdflib.namespace import DCTERMS, RDF
OLO = Namespace("http://purl.org/ontology/olo/core#")
PROV = Namespace("http://www.w3.org/ns/prov#")

from ferenda import DocumentRepository
from ferenda import DocumentStore
from ferenda import util
from ferenda.decorators import managedparsing
from ferenda import elements
from ferenda.elements.html import elements_from_soup

class StaticStore(DocumentStore):

    """Customized DocumentStore that looks for all "downloaded" resources
    from the specified ``staticdir``. If ``staticdir`` isn't provided
    or doesn't exist, falls back to a collection of package resources
    (under ferenda/res/static-content). Parsed, generated etc files
    are handled like normal, ie stored under
    ``[datadir]/static/{parsed,distilled,generated,...}/``
    """

    def downloaded_path(self, basefile, version=None, attachment=None):
        segments = [self.staticdir,
                    self.basefile_to_pathfrag(basefile) + self.downloaded_suffix]
        return "/".join(segments).replace("/", os.sep)

    def list_basefiles_for(self, action, basedir=None):
        if action == "parse":
            for x in util.list_dirs(self.staticdir, self.downloaded_suffix):
                pathfrag = x[len(self.staticdir) + 1:-len(self.downloaded_suffix)]
                yield self.pathfrag_to_basefile(pathfrag)
        else:
            for x in super(StaticStore, self).list_basefiles_for(action, basedir):
                yield x


class Static(DocumentRepository):

    """Generates documents from your own ``.rst`` files

    The primary purpose of this docrepo is to provide a small set of
    static pages for a complete ferenda-based web site, like "About
    us", "Contact information", "Terms of service" or whatever else
    you need. The ``download`` step of this docrepo does not do
    anything, and it's ``parse`` step reads ReStructuredText
    (``.rst``) files from a local directory and converts them into
    XHTML+RDFa. From that point on, it works just like any other
    docrepo.

    After enabling this, you should set the configuration parameter
    ``staticdir`` to the path of a directory where you keep your
    ``.rst`` files::

        [static]
        class = ferenda.sources.general.Static
        staticdir = /var/www/mysite/static/rst

    .. note::

       If this configuration parameter is not set, this docrepo will
       use a small set of generic static pages, stored under
       ``ferenda/res/static-pages`` in the distribution. To get
       started, you can just copy this directory and set ``staticdir``
       to point at your copy.

    Every file present in ``staticdir`` results in a link in the site
    footer. The link text will be the title of the document, i.e. the
    first header in the ``.rst`` file.

    """
    alias = "static"
    downloaded_suffix = ".rst"
    documentstore_class = StaticStore
    sparql_annotations = None
    # urls become on the form "http://localhost:8000/static/about"

    def __init__(self, config=None, **kwargs):
        super(Static, self).__init__(config, **kwargs)
        if 'staticdir' in self.config:
            staticdir = self.config.staticdir
            assert os.path.exists(staticdir), "%s does not exist" % staticdir
        else:
            p = self.resourceloader.filename('static-content/README')
            staticdir = os.path.dirname(p)
        self.store.staticdir = staticdir
        

    def download(self):
        pass

    @managedparsing
    def parse(self, doc):
        source = util.readfile(self.store.downloaded_path(doc.basefile))
        html = publish_string(source, writer_name="html")
        soup = BeautifulSoup(html, "lxml")
        docinfo = soup.find("table", "docinfo")
        docuri = URIRef(doc.uri)
        if docinfo:
            # this is where our custom metadata goes
            for row in docinfo.find_all("tr", "field"):
                key, val = row.th.text.strip(), row.td.text.strip()
            if key == 'footer-order:':
                doc.meta.add((docuri, OLO['index'], Literal(int(val))))
            else:
                self.log.warning("%s: Unknown metadata directive %s (%s)" %
                                 (doc.basefile, key, val))
        doc.body = elements_from_soup(soup.body)
        doc.meta.add((docuri, DCTERMS.title,
                      Literal(soup.title.text, doc.lang)))
        doc.meta.add((docuri, PROV.wasGeneratedBy, Literal(self.qualified_class_name())))
        doc.meta.add((docuri, RDF.type, self.rdf_type))
        self.parse_entry_update(doc)
        return True


    def toc(self, otherrepos=[]):
        pass

    def news(self, otherrepos=[]):
        pass

    def frontpage_content(self, primary=False):
        pass
    
    def tabs(self):
        if os.path.exists(self.store.parsed_path("about")):
            return [("About", self.canonical_uri("about"))]
        else:
            return[]

    def footer(self):
        res = {}
        for basefile in self.store.list_basefiles_for("generate"):
            uri = self.canonical_uri(basefile)
            g = Graph()
            g.parse(self.store.distilled_path(basefile))
            # only return those files that have olo:index metadata, in
            # that order
            if g.value(URIRef(uri), OLO['index']):
                title = g.value(URIRef(uri), self.ns['dcterms'].title).toPython()
                if not title:
                    title = basefile
                res[int(g.value(URIRef(uri), OLO['index']))] = (title, uri)
        return [res[x] for x in sorted(res)]
