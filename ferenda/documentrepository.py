# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
nativeint = int
from builtins import *
from future.utils import native_str

# stdlib
from collections import defaultdict, OrderedDict
from datetime import datetime
from io import BytesIO, StringIO
from itertools import chain
from operator import itemgetter
from tempfile import mkstemp
from wsgiref.handlers import format_date_time as format_http_date
from urllib.parse import quote, unquote, parse_qsl, urlparse
import builtins
import locale
import calendar
import codecs
import difflib
import filecmp
import functools
import inspect
import json
import logging
import logging.handlers
import os
import pickle
import re
import socket
import stat
import sys
import time
import unicodedata

# 3rd party
from layeredconfig import LayeredConfig, Defaults
from lxml import etree
from lxml.etree import Element
from lxml.builder import ElementMaker
from rdflib import Graph, Literal, Namespace, URIRef, BNode, RDF, RDFS
from rdflib.namespace import FOAF, OWL
from rdflib.collection import Collection
import bs4
import lxml.html
import requests
import requests.exceptions
from cached_property import cached_property

# mine
import ferenda
from ferenda import util, errors, decorators, fulltextindex

from ferenda import (Describer, TripleStore, FulltextIndex, Document,
                     DocumentEntry, TocPageset, TocPage,
                     DocumentStore, Transformer, Facet, Feed, Feedset,
                     ResourceLoader, RequestHandler)
from ferenda.elements import (Body, Link,
                              UnorderedList, ListItem, Paragraph)
from ferenda.elements.html import elements_from_soup
from ferenda.documentstore import RelateNeeded

import contextlib

# establish two central RDF Namespaces at the top level
DCTERMS = Namespace(util.ns['dcterms'])
PROV = Namespace(util.ns['prov'])


class RDFQuery(object):
    store = None
    
    @classmethod
    @contextlib.contextmanager
    def enable(cls, config):
        cls.store = TripleStore.connect(config.storetype,
                                        config.storelocation,
                                        config.storerepository)
        yield 
        cls.store.close()
        
    @classmethod
    def rdf_query(cls, dummy, query, *arg):
        res = json.loads(cls.store.select(query.replace("[", "<").replace("]", ">") % arg, format="json"))
        
        return [etree.Element("{http://lagen.nu/xslt}SparqlResult",
                              {key: value["value"]
                               for key, value in binding.items()})
                for binding in res["results"]["bindings"]]
        
ns = etree.FunctionNamespace("http://lagen.nu/xslt")
ns['sparql'] = RDFQuery.rdf_query


class DocumentRepository(object):

    """Base class for handling a repository of documents.

    Handles downloading, parsing and generation of HTML version of
    documents. Start building your application by subclassing this
    class, and then override methods in order to customize the
    downloading, parsing and generation behaviour.

    :param \*\*kwargs: Any named argument overrides any
                   similarly-named :ref:`configuration` file
                   parameter.

    Example:

    >>> class MyRepo(DocumentRepository):
    ...     alias="myrepo"
    ...
    >>> d = MyRepo(datadir="/tmp/ferenda")
    >>> d.store.downloaded_path("mybasefile").replace(os.sep,'/')
    '/tmp/ferenda/myrepo/downloaded/mybasefile.html'

    .. note::

       This class has a ridiculous amount of properties and methods
       that you can override to control most of Ferendas behaviour in
       all stages. For basic usage, you need only a fraction of
       them. Please don't be intimidated/horrified.

    """

#    There are seven main entry points into the module, with the
#    following principal call chains:
#
#    download
#        download_get_basefiles
#            download_single
#                downloaded_path
#                download_if_needed
#                remote_url
#                download_update_entry
#    parse
#        parsed_path
#        soup_from_basefile
#        parse_from_soup
#        render_xhtml
#
#    relate
#        relate_triples
#        relate_dependencies
#        relate_fulltext
#
#    generate
#        generated_file
#        prep_annotation_file
#            graph_to_annotation_file
#
#    toc
#        faceted_data
#            facet_select
#                facet_query
#                    dataset_uri
#                    facets
#        toc_pagesets
#            facets
#        toc_select_for_pages
#        toc_generate_pages
#
#    news
#        news_facet_entries
#        news_feedsets
#        news_select_for_feeds
#            news_item
#        news_generate_feeds
#            news_write_atom
#
#    frontpage_content

    #
    # general class properties
    # FIXME: Duplicated in documentstore -- how do we unify?
    alias = "base"
    """A short name for the class, used by the command line
    ``ferenda-build.py`` tool. Also determines where to store
    downloaded, parsed and generated files. When you subclass
    :py:class:`~ferenda.DocumentRepository` you *must* override
    this."""

    storage_policy = "file"
    """Some repositories have documents in several formats, documents
    split amongst several files or embedded resources. If
    ``storage_policy`` is set to ``dir``, then each document gets its own
    directory (the default filename being ``index`` +suffix),
    otherwise each doc gets stored as a file in a directory with other
    files.  Affects
    :py:meth:`ferenda.DocumentStore.path` (and therefore
    all other ``*_path`` methods)"""


    namespaces = [
        'rdf',
        'rdfs',
        'xsd',
        'xsi',
        'dcterms',
        'skos',
        'foaf',
        'xhv',
        'owl',
        'prov',
        'bibo']
    """The namespaces that are included in the XHTML and RDF files
    generated by :py:meth:`~ferenda.DocumentRepository.parse`. This
    can be a list of strings, in which case the strings are assumed to
    be well-known prefixes to established namespaces, or a list of
    *(prefix, namespace)* tuples. All well-known prefixes are available
    in :py:data:`ferenda.util.ns`.

    If you specify a namespace for a well-known ontology/vocabulary,
    that onlology will be available as a
    :py:class:`~rdflib.graph.Graph` from the
    :py:data:`~ferenda.DocumentRepository.ontologies` property.

    """

    collate_locale = None
    """The locale to be used for sorting (collating). This affects TOCs, see
    :ref:`toc-sorting`."""

    loadpath = None
    """If defined (by default it's ``None``), this should be a list of
    directories that takes precedence over the loadpath given by the
    current config."""

    lang = "en"
    """The language (expressed as a two-letter ISO 639-1 code) which the
    source documents are assumed to be written in (unless otherwise
    specified), and the language which output document should use."""


    #
    # download() related class properties

    start_url = "http://example.org/"
    """The main entry page for the remote web store of documents. May
    be a list of documents, a search form or whatever. If it's
    something more complicated than a simple list of documents, you
    need to override :py:meth:`~ferenda.DocumentRepository.download`
    in order to tell which documents are to be downloaded."""

    document_url_template = "http://example.org/docs/%(basefile)s.html"
    """A string template for creating URLs for individual documents on
    the remote web server. Directly used by
    :py:meth:`~ferenda.DocumentRepository.remote_url` and indirectly
    by :py:meth:`~ferenda.DocumentRepository.download_single`."""

    document_url_regex = "http://example.org/docs/(?P<basefile>\w+).html"
    """A regex that matches URLs for individual documents -- the
    reverse of what
    :py:data:`~ferenda.DocumentRepository.document_url_template` is
    used for. Used by
    :py:meth:`~ferenda.DocumentRepository.download()` to find suitable
    links if :py:data:`~ferenda.DocumentRepository.basefile_regex`
    doesn't match. Must define the named group ``basefile`` using the
    ``(?P<basefile>...)`` syntax"""

    # matches "ID: foo/123" or "ID: Bar:Baz/Quux" but not "ID: Foo bar"
    basefile_regex = "^ID: ?(?P<basefile>[\w\d\:\/]+)$"
    """A regex for matching document names in link text, as used by
    :py:meth:`~ferenda.DocumentRepository.download()`. Must define a
    named group ``basefile``, just like
    :py:data:`~ferenda.DocumentRepository.document_url_template`."""

    downloaded_suffix = ".html"
    """File suffix for the main document format. Determines the suffix
    of downloaded files."""

    download_archive = True
    """If ``True`` (the default), any attempt by download_single to
    download a basefile that already exists will cause the old version
    to be archived. See :ref:`keyconcept-archiving`.
    """

    download_archive_overwrite = False
    """If ``False`` (the default), any attempt to overwrite an archived
    version with a new archived version with the same version id will
    fail (this will happen when
    `~ferenda.DocumentRepository.downloaded_is_different()` signals
    that document versions are different, yet
    `~ferenda.DocumentRepository.get_archive_version()` returns the
    same version id for both."""

    download_archive_copy = False
    """If ``True``, any attempt to archive a version will copy the current
    version to its archived location. If ``False`` (the default), the
    current version will be moved instead, which means that for a brief
    time, there will exist no current version (in any of its forms, eg
    parsed or generated)."""

    download_iterlinks = True
    """If ``True`` (the default),
    :py:meth:`~ferenda.DocumentRepository.download_get_basefiles`
    will be called with an iterator that returns (element,
    attribute, link, pos) tuples (like ``lxml.etree.iterlinks()``
    does). Othervise, it will be called with the downloaded index page
    as a string."""


    download_accept_404 = False
    """If ``True`` (default: ``False``), any 404 HTTP error encountered
    during download will NOT raise and error. Instead, the download
    process will just move on to the next identified basefile."""

    download_accept_406 = False
    # same
    download_accept_400 = False
    
    download_reverseorder = False
    """It ``True`` (default: ``False``), download_get_basefiles will
    process recieved basefiles in reverse order."""

    download_record_last_download = True
    

    source_encoding = "utf-8"
    """The character set that the source documents use (if
    applicable)."""

    # parse() specific class properties
    rdf_type = Namespace(util.ns['foaf']).Document
    """The RDF type of the documents you are handling (expressed as a
    :py:class:`rdflib.term.URIRef` object).

    .. note::

       If your repo produces documents of several different types, you
       can define this as a list (or other iterable) of
       :py:class:`~rdflib.term.URIRef`
       objects. :py:meth:`~ferenda.DocumentRepository.faceted_data()`
       will only find documents that are any of the types.

    """

    required_predicates = [RDF.type]
    """A list of RDF predicates that should be present in the outdata. If
    any of these are missing from the result of
    :py:meth:`~ferenda.DocumentRepository.parse`, a warning is
    logged. You can add to this list as a form of simple validation of
    your parsed data.

    """

    max_resources = 1000
    """The maximum number of sub-resources (as defined by having a
    specific URI) that documents in this repo can have. This is
    checked in a validation step at the end of parse. If set to None,
    no validation of the number of resources is done."""

    # css selectors, handled by BeautifulSoup's select() method
    parse_content_selector = "body"
    """CSS selector used to select the main part of the document
    content by the default
    :py:meth:`~ferenda.DocumentRepository.parse` implementation."""

    parse_filter_selectors = ["script"]
    """CSS selectors used to filter/remove certain parts of the
    document content by the default
    :py:meth:`~ferenda.DocumentRepository.parse` implementation."""

    #
    # generate() specific class properties
    xslt_template = "xsl/generic.xsl"
    """A template used by
    :py:meth:`~ferenda.DocumentRepository.generate` to transform the
    XML file into browser-ready HTML. If your document type is
    complex, you might want to override this (and write your own XSLT
    transform). You should include ``base.xslt`` in that template,
    though."""

    sparql_annotations = "sparql/annotations.rq"
    """A template SPARQL CONSTRUCT query for document annotations."""

    sparql_expect_results = True
    """If ``True`` (the default) and the ``sparql_annotations_query``
    doesn't return any results, issue a warning."""

    documentstore_class = DocumentStore
    """Class that implements the :class:`~ferenda.DocumentStore`
    interface. If you want to customize how this repo stores files, you
    can create a subclass of :class:`~ferenda.DocumentStore` and then set
    this attribute to that class in your docrepo."""

    requesthandler_class = RequestHandler
    """Class that implements the :class:`~ferenda.RequestHandler`
    interface. If you want to customize how this repo serves its contents
    over HTTP(S), you can create a subclass of
    :class:`~ferenda.RequestHandler` and set this attribute to that class
    in your docrepo."""

    def __init__(self, config=None, **kwargs):
        """See :py:class:`~ferenda.DocumentRepository`."""
        if not config:
            codedefaults = self.get_default_options()
            defaults = util.merge_dict_recursive(codedefaults, kwargs)
            self._config = LayeredConfig(Defaults(defaults))
        else:
            self._config = config
        if not hasattr(self, 'store'):
            self.store = self.documentstore_class(self.config.datadir + os.sep + self.alias, compression=self.config.compress)
        self.requesthandler = self.requesthandler_class(self)
        
        # allow this docrepo to override a particular property of its
        # docstore if the repo (but not the store) has customized it
        # (this allows us to only create a custom repo, not a custom
        # store, in many cases).
        if self.downloaded_suffix != ".html" and self.store.downloaded_suffixes == [".html"]:
            self.store.downloaded_suffixes = [self.downloaded_suffix]
        self.store.storage_policy = self.storage_policy

        logname = self.alias
        # alternatively (nonambigious and helpful for debugging, but verbose)
        # logname = self.__class__.__module__+"."+self.__class__.__name__
        # self.log = self._setup_logger(logname)
        self.log = logging.getLogger(logname)

        self.ns = {}
        for ns in self.namespaces:
            if isinstance(ns, tuple):
                prefix, uri = ns
                self.ns[prefix] = Namespace(uri)
            else:
                prefix = ns
                # assume that any standalone prefix is well known
                self.ns[prefix] = Namespace(util.ns[prefix])

        # Only the download* methods needs this, but having it
        # available on every created objects makes patching easier
        # when testing. FIXME: A better alternative would be to use
        # the responses library to mock calls to requests.
        self.session = requests.session()
        loadpath = ResourceLoader.make_loadpath(self)
        # if the class specifieds additional path(s), these have
        # priority over the inheritance-graph derived loadpath:
        if self.loadpath:
            loadpath = self.loadpath + loadpath
        # A "res/" in the the current directory has priority over
        # class loadpaths:
        if os.path.exists("res") and os.path.isdir("res"):
            loadpath = ["res"] + loadpath
        # if the user has specified an additional loadpath, it has
        # priority over anything else.
        if 'loadpath' in self.config:
            loadpath = self.config.loadpath + loadpath
            
        self.resourceloader = ResourceLoader(*loadpath)

    @cached_property
    def ontologies(self):
        """Provides a :py:class:`~rdflib.graph.Graph` loaded with the
        ontologies/vocabularies that this docrepo uses (as determined by the
        :py:data:`~ferenda.DocumentRepository.namespaces`` property).

        If you're using your own vocabularies, you can place them (in
        Turtle format) as ``vocab/[prefix].ttl`` somewhere in your
        resource loadpath to have them loaded into the graph.

        .. note::

           Some system-like vocabularies (``rdf``, ``rdfs`` and ``owl``)
           are never loaded into the graph.

        """
        # in most cases, the user of the Docrepo object won't want to
        # look at the defined ontologies. But in case one does!
        o = Graph()
        for prefix, uri in self.ns.items():
            # , "foaf", "skos", "dcterms", "bibo", "prov"):
            if prefix in ("rdf", "rdfs", "owl"):
                continue
            ontopath = "vocab/%s.ttl" % prefix
            if self.resourceloader.exists(ontopath):
                with self.resourceloader.open(ontopath) as fp:
                    o.parse(data=fp.read(), format="turtle")                 
                    o.bind(prefix, uri)
        return o


    @cached_property
    def commondata(self):
        """Provides a :py:class:`~rdflib.graph.Graph` containing any extra data that is common to
        documents in this docrepo -- this can be information about
        different entities that publishes the documents, the printed
        series in which they're published, and so on. The data is
        taken from ``extra/[repoalias].ttl``.
        """
        cd = Graph()
        for cls in inspect.getmro(self.__class__):
            if hasattr(cls, "alias"):
                commonpath = "extra/%s.ttl" % cls.alias
                if self.resourceloader.exists(commonpath):
                    with self.resourceloader.open(commonpath, binary=True) as fp:
                        cd.parse(data=fp.read(), format="turtle")                 
        return cd

    @property
    def config(self):
        """The :py:class:`~layeredconfig.LayeredConfig` object that contains the
        current configuration for this docrepo instance. You can read or write
        individual properties of this object, or replace it with a new
        :py:class:`~layeredconfig.LayeredConfig` object entirely."""

        return self._config

    @config.setter
    def config(self, config):
        """TBD"""
        self._config = config
        downloaded_suffixes = None
        if self.store:
            # DocumentRepository.__init__ may set this attribute on
            # it's store after initialization. We need to save it
            # prior to creating a new store, so that we can re-set it
            # on the new store.
            downloaded_suffixes = self.store.downloaded_suffixes
        self.store = self.documentstore_class(
            config.datadir + os.sep + self.alias,
            storage_policy=self.storage_policy,
            compression=config.compress)
        if downloaded_suffixes and downloaded_suffixes != self.store.downloaded_suffixes:
            self.store.downloaded_suffixes.clear()
            self.store.downloaded_suffixes.extend(downloaded_suffixes)

    def lookup_resource(self, label, predicate=FOAF.name, cutoff=0.8, warn=True):
        """Given a textual identifier (ie. the name for something), lookup the
        canonical uri for that thing in the RDF graph containing extra
        data (i.e. the graph that
        :py:data:`~ferenda.DocumentRepository.commondata`
        provides). The graph should have a `foaf:name``` statement
        about the url with the sought label as the object.

        Since data is imperfect, the textual label may be spelled or
        expressed different in different contexts. This method
        therefore performs fuzzy matching (using
        :py:func:`difflib.get_close_matches`) using the cutoff
        parameter determines exactly how fuzzy this matching is.

        If no resource matches the given label, a
        :py:exc:`KeyError` is raised.

        :param label: The textual label to lookup
        :type  label: str
        :param predicate: The RDF predicate to use when looking for the label
        :type  predicate: rdflib.term.URIRef
        :param cutoff: How fuzzy the matching may be (1 = must match
                       exactly, 0 = anything goes)
        :type  cutoff: float
        :param warn: Whether to log a warning when an inexact match is
                     performed
        :type  warn: bool
        :returns: The matching resource
        :rtype: rdflib.term.URIRef

        """

        resources = {}
        for (resource, candidate_label) in self.commondata.subject_objects(predicate):
            if label == str(candidate_label):
                return resource
            else:
                resources[str(candidate_label)] = resource

        fuzz = difflib.get_close_matches(label, resources.keys(), 1, cutoff)
        if fuzz:
            # even if we want warnings, we don't want warnings for case changes
            if warn and label.lower() != fuzz[0].lower():
                self.log.warning("Assuming that '%s' should be '%s'?" %
                                 (label, fuzz[0]))
            return URIRef(resources[fuzz[0]])
        else:
            raise KeyError("No good match for '%s'" % label)

    @classmethod
    def get_default_options(cls):
        """Returns the class' configuration default configuration
        properties. These can be overridden by a configution file, or
        by named arguments to
        :py:meth:`~ferenda.DocumentRepository.__init__`. See
        :ref:`configuration` for a list of standard configuration
        properties (your subclass is free to define and use additional
        configuration properties).

        :returns: default configuration properties
        :rtype: dict
        """
        return {  # 'loglevel': 'INFO',
            'allversions': False,
            'bulktripleload': False,
            'class': cls.__module__ + "." + cls.__name__,
            'clientname': '',
            'compress': "",  # don't compress by default
            'conditionalget': True,
            'datadir': 'data',
            'develurl': None,
            'download': True,
            'downloadmax': nativeint,
            'force': False,
            'frontpagefeed': False,
            'fsmdebug': False,
            'fulltextindex': True,
            'generateforce': False,
            'ignorepatch': False,
            'indexlocation': 'data/whooshindex',
            'indextype': 'WHOOSH',
            'lastdownload': datetime,
            'parseforce': False,
            'patchdir': 'patches',
            'patchformat': 'default',
            'primaryfrontpage': False,
            'processes': '1',
            'refresh': False,
            'relate': True,
            'removeinvalidlinks': True,
            'republishsource': False,
            'serializejson': False,
            'storelocation': 'data/ferenda.sqlite',
            'storerepository': 'ferenda',
            'storetype': 'SQLITE',
            'tabs': True,
            'url': 'http://localhost:8000/',
            'useragent': 'ferenda-bot',
            # FIXME: These only make sense at a global level, and
            # furthermore are duplicated in manager._load_config.
#            'cssfiles': ['css/ferenda.css'],
#            'jsfiles': ['js/ferenda.js'],
#            'imgfiles': ['img/atom.png'],
            'combineresources': False,
            'staticsite': False,
#            'legacyapi': False,
#            'sitename': 'MySite',
#            'sitedescription': 'Just another Ferenda site',
#            'apiendpoint': "/api/",
#            'searchendpoint': "/search/",
#            'acceptalldomains': False,
        }

    @classmethod
    def setup(cls, action, config, *args, **kwargs):
        """Runs before any of the ``*_all`` methods starts executing. It just
calls the appropriate setup method, ie if *action* is ``parse``, then
this method calls ``parse_all_setup`` (if defined) with the *config*
object as single parameter."""

        if hasattr(cls, action + "_all_setup"):
            cbl = getattr(cls, action + "_all_setup")
            if callable(cbl):
                return cbl(config, *args, **kwargs)

    @classmethod
    def teardown(cls, action, config, *args, **kwargs):
        """Runs after any of the ``*_all`` methods has finished executing. It
just calls the appropriate teardown method, ie if *action* is
``parse``, then this method calls ``parse_all_teardown`` (if defined)
with the *config* object as single parameter.

        """

        if hasattr(cls, action + "_all_teardown"):
            cbl = getattr(cls, action + "_all_teardown")
            if callable(cbl):
                return cbl(config, *args, **kwargs)

    def get_archive_version(self, basefile):
        """Get a version identifier for the current version of the
        document identified by ``basefile``.

        The default implementation simply increments most recent
        archived version identifier, starting at "1". If versions in
        your docrepo are normally identified in some other way (such
        as SCM revision numbers, dates or similar) you should override
        this method to return those identifiers.

        :param basefile: The basefile of the document to archive
        :type basefile: str
        :returns: The version identifier for the current version of
                  the document.
        :rtype:   str
        """
        return str(len(list(self.store.list_versions(basefile))) + 1)

    def qualified_class_name(self):
        """The qualified class name of this class

        :returns: class name (e.g. ``ferenda.DocumentRepository``)
        :rtype:   str
        """
        return self.__class__.__module__ + "." + self.__class__.__name__

    def canonical_uri(self, basefile, version=None):
        """The canonical URI for the document identified by ``basefile``.

        :returns: The canonical URI
        :rtype: str
        """
        # Note that there might not be a 1:1 mappning between
        # documents/basefiles and URIs -- don't know what we should do
        # in those cases.
        #
        # It might also be impossible to provide the canonical_uri
        # without actually parse()ing the document

        uri = "%sres/%s/%s" % (self.config.url, self.alias, basefile)
        if version:
            uri += "?version=" % urlencode(version)
        return uri

    def dataset_uri(self, param=None, value=None, feed=False):
        """Returns the URI that identifies the dataset that this docrepository
        provides. The default implementation is based on the url
        config parameter and the alias attribute of the class,
        c.f. ``http://localhost:8000/dataset/base``.

        :param param: An optional parameter name represeting a way of createing a subset of the dataset (eg. all document whose title starts with a particular letter)
        :param value: A value for *param* (eg. "a")

        >>> d = DocumentRepository()
        >>> d.alias
        'base'
        >>> d.config.url = "http://example.org/"
        >>> d.dataset_uri()
        'http://example.org/dataset/base'
        >>> d.dataset_uri("title","a")
        'http://example.org/dataset/base?title=a'
        >>> d.dataset_uri(feed=True)
        'http://example.org/dataset/base/feed'
        >>> d.dataset_uri("title", "a", feed=True)
        'http://example.org/dataset/base/feed?title=a'
        >>> d.dataset_uri("title", "a", feed=".atom")
        'http://example.org/dataset/base/feed.atom?title=a'

        """

        uri = "%sdataset/%s" % (self.config.url, self.alias)
        if feed:
            uri += "/feed"
            if not isinstance(feed, bool):
                # ie add an ".atom" suffix, if that's whats passed as the feed parameter
                uri += feed
        if param and value:
            uri += "?%s=%s" % (param, quote(value))
        return uri

    def basefile_from_uri(self, uri):
        """The reverse of :meth:`~ferenda.DocumentRepository.canonical_uri`.
        Returns ``None`` if the uri doesn't map to a basefile in this repo.

        >>> d = DocumentRepository()
        >>> d.alias
        'base'
        >>> d.config.url = "http://example.org/"
        >>> d.basefile_from_uri("http://example.org/res/base/123/a")
        '123/a'
        >>> d.basefile_from_uri("http://example.org/res/base/123/a#S1")
        '123/a'
        >>> d.basefile_from_uri("http://example.org/res/other/123/a") # None

        """
        if uri.startswith(self.config.url + "res/"):
            path = uri[len(self.config.url + "res/"):]
            if "/" in path:
                alias, basefile = path.split("/", 1)
                if "#" in basefile:
                    basefile = basefile.split("#")[0]
                elif "." in basefile:
                    basefile = basefile.split(".")[0]
                if alias == self.alias:
                    return basefile

    def get_required_predicates(self, doc):
        return list(self.required_predicates)


    # @decorators.action
    # def null(self):
    #     print("%s: null" % self.alias)
    
    #
    # STEP 1: Download documents from the web
    #
    #
    @decorators.action
    @decorators.recordlastdownload
    @decorators.updateentry('download')
    def download(self, basefile=None, reporter=None):
        """Downloads all documents from a remote web service.

        The default generic implementation assumes that all documents
        are linked from a single page (which has the url of
        :py:data:`~ferenda.DocumentRepository.start_url`), that they
        all have URLs matching the
        :py:data:`~ferenda.DocumentRepository.document_url_regex` or
        that the link text is always equal to basefile (as determined
        by :py:data:`~ferenda.DocumentRepository.basefile_regex`). If
        these assumptions don't hold, you need to override this
        method.

        If you do override it, your download method should read and set the
        ``lastdownload`` parameter to either the datetime of the last
        download or any other module-specific string (id number or
        similar).

        You should also read the ``refresh`` parameter. If it is
        ``True`` (the default), then you should call
        :py:meth:`~ferenda.DocumentRepository.download_single` for
        every basefile you encounter, even though they may already
        exist in some form on
        disk. :py:meth:`~ferenda.DocumentRepository.download_single`
        will normally be using conditional GET to see if there is a
        newer version available.

        See :ref:`implementing-download` for more details.

        :returns: True if any document was downloaded, False otherwise.
        :rtype: bool
        """
        if basefile:
            if self.document_url_template:
                return self.download_single(basefile)
            else:
                raise ValueError(
                    "Downloading single basefile '%s' not supported "
                    "(no way to convert basefile to url)" % basefile)
        if 'lastdownload' in self.config:
            self.log.debug("download: Last download was at %s" %
                           self.config.lastdownload)
        else:
            self.log.debug("download: Starting full download")
        # NOTE: This very generic implementation of download has no
        # use for lastdownload, as all the documents it can find are
        # the one linked from the start page. Therefore it's not used
        # for anything else than a diagnostic tool.
        refresh = self.config.refresh
        if refresh:
            self.log.debug("download: Refreshing all downloaded files")
        else:
            self.log.debug("download: Not re-downloading downloaded files")

        self.log.debug("Starting at %s" % self.start_url)
        updated = False
        resp = self.download_get_first_page()
        resp.raise_for_status()
        if self.download_iterlinks:
            tree = lxml.html.document_fromstring(resp.text)
            tree.make_links_absolute(self.start_url, resolve_base_href=True)
            source = tree.iterlinks()
        else:
            source = resp.text
            
        for (basefile, params) in self.download_get_basefiles(source):
            assert isinstance (params, dict), "You need to update your implementation of download_get_basefiles to return a dict instead of a string"
            link = params['uri']
            downloaded_path = self.store.downloaded_path(basefile)
            if (refresh or
                not os.path.exists(downloaded_path) or
                os.path.getsize(downloaded_path) == 0):
                ret = None
                try:
                    if 'title' in params:
                        callback = lambda e: setattr(e, 'title', params['title'])
                        
                    else:
                        callback = None
                    ret = DocumentEntry.updateentry(self.download_single,
                                                    'download',
                                                    self.store.documententry_path,
                                                    basefile,
                                                    callback,
                                                    basefile,
                                                    link)
                except requests.exceptions.HTTPError as e:
                    if self.download_accept_404 and e.response.status_code == 404:
                        self.log.error("%s: %s %s" % (basefile, link, e))
                        ret = False
                    elif self.download_accept_406 and e.response.status_code == 406:
                        # The Eurlex CELLAR service sometimes return
                        # this (if a doc is not available in our
                        # wanted language, I think?) and we'd like to
                        # distinguish this from a 404 error
                        self.log.error("%s: %s %s" % (basefile, link, e))
                        ret = False
                    elif self.download_accept_400 and e.response.status_code == 400:
                        # KKV does this for some (malformed) URLs like http://www.konkurrensverket.se/beslut/1_20160922110607_Nordic%20Camping%20&%20Resort%20AB.pdf
                        self.log.error("%s: %s %s" % (basefile, link, e))
                        ret = False
                    else:
                        raise e
                except errors.DownloadFileNotFoundError as e:
                    if self.download_accept_404:
                        self.log.error("%s: %s %s" % (basefile, link, e))
                        ret = False
                    else:
                        raise e
                except errors.DocumentRemovedError as e:
                    # download_single has signalled that a document
                    # that download_get_basefiles thought would exist
                    # did not in fact exist. Make a note of this so
                    # that we don't need to call download_single for
                    # this basefile ever again:
                    if e.dummyfile:
                        util.writefile(e.dummyfile, "")
                    pass
                finally:
                    if reporter:
                        reporter(basefile)
                        
                updated = updated or ret
        # self.config.lastdownload = datetime.now()
        return updated

    def download_get_first_page(self):
        """TBD"""
        resp = self.session.get(self.start_url)
        return resp

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        """Given *source* (a iterator that provides (element, attribute, link,
        pos) tuples, like ``lxml.etree.iterlinks()``), generate tuples
        ``(basefile, parameters)`` for all documents found in
        *source*. ``basefile`` is a regular string that will be used
        as the basefile for the identified document. ``parameters`` is
        a dict that contains various metadata about the identified
        document. It may be empty, but can also contain various bits
        of metadata about the document. Three common fields are
        ``url`` which is the full link to the document (as expected by
        download_single), ``orig_url`` which is a link to the main
        page (eg a landing page) that contains the download link to
        the document in question, and ``title`` which is the title of
        the document.

        If you override both
        :py:meth:`~ferenda.DocumentRepository.download` and this
        method, you can pass whatever useful metadata you like in this
        dict.

        """
        yielded = set()
        if self.download_reverseorder:
            source = reversed(list(source))
        for (element, attribute, link, pos) in source:
            basefile = None

            # Two step process: First examine link text to see if
            # basefile_regex match. If not, examine link url to see
            # if document_url_regex
            if (self.basefile_regex and
                element.text and
                    re.search(self.basefile_regex, element.text)):
                m = re.search(self.basefile_regex, element.text)
                basefile = m.group("basefile")
            elif self.document_url_regex and re.match(self.document_url_regex,
                                                      link):
                m = re.match(self.document_url_regex, link)
                if m:
                    basefile = m.group("basefile")
            if basefile and (basefile, link) not in yielded:
                yielded.add((basefile, link))
                yield (basefile, {'uri': link})

    def download_single(self, basefile, url=None, orig_url=None):
        """Downloads the document from the web (unless explicitly
        specified, the URL to download is determined by
        :py:data:`~ferenda.DocumentRepository.document_url_template` combined
        with basefile, the location on disk is determined by the
        function
        :py:meth:`~ferenda.DocumentStore.downloaded_path`).

        If the document exists on disk, but the version on the web is
        unchanged (determined using a conditional GET), the file on disk
        is left unchanged (i.e. the timestamp is not modified).

        :param basefile: The basefile of the document to download
        :type basefile: string
        :param url: The URL to download (optional)
        :type url: str
        :param url: The URL to store in the documententry file (might be a landing page containing the actual document URL)
        :type url: str
        :returns: ``True`` if the document was downloaded and stored on
                  disk, ``False`` if the file on disk was not updated.
        """
        if url is None:
            url = self.remote_url(basefile)

        updated = False
        created = False

        filename = self.store.downloaded_path(basefile)
        created = not os.path.exists(filename) or os.path.getsize(filename) == 0
        # util.print_open_fds()
        
        if self.download_if_needed(url, basefile, archive=self.download_archive):
            if created:
                self.log.info("%s: download OK from %s" % (basefile, url))
            else:
                self.log.info(
                    "%s: download OK (new version) from %s" % (basefile, url))
            updated = True
        else:
            self.log.debug("%s: exists and is unchanged" % basefile)

        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        if orig_url is None:
            orig_url = url
        entry.orig_url = orig_url
        if created:
            entry.orig_created = now
        if updated:
            entry.orig_updated = now
        entry.orig_checked = now
        entry.save()

        return updated

    def _addheaders(self, url, filename=None):
        headers = {"User-agent": self.config.useragent}
        if filename:
            # we set both if-none-match and if-modified-since if we
            # can. We've encountered at least one server which sends
            # ETags but don't return 304 when the appropriate ETag is
            # returned in a if-none-match header (but return 304 when
            # if-modified-since is used)
            if os.path.exists(filename + ".etag"):
                headers["If-none-match"] = util.readfile(filename + ".etag")
            if os.path.exists(filename):
                stamp = os.stat(filename).st_mtime
                headers["If-modified-since"] = format_http_date(stamp)
        return headers

    def download_if_needed(self, url, basefile, archive=True, filename=None, sleep=1, extraheaders=None):
        """Downloads a remote resource to a local file. If a different
        version is already in place, archive that old version.

        :param      url: The url to download
        :type       url: str
        :param basefile: The basefile of the document to download
        :type  basefile: str
        :param  archive: Whether to archive existing older versions of
                         the document, or just delete the previously
                         downloaded file.
        :type   archive: bool
        :param filename: The filename to download to. If not provided,
                         the filename is derived from the supplied
                         basefile
        :type  filename: str
        :returns:        True if the local file was updated (and archived),
                         False otherwise.
        :rtype:          bool

        """
        if not filename:
            assumedfilename = self.store.downloaded_path(basefile)
        else:
            assumedfilename = filename

        if self.config.conditionalget:
            # sets if-none-match and/or if-modified-since headers
            headers = self._addheaders(url, assumedfilename)
        else:
            headers = self._addheaders(url)
        if extraheaders:
            headers.update(extraheaders)

        fileno, tmpfile = mkstemp()
        fp = os.fdopen(fileno)
        fp.close()

        # Take extra precautions in the event of temporary network
        # failures etc -- try 5 times with 1 second pause inbetween
        # before giving up.
        response = util.robust_fetch(self.session.get, url, self.log,
                                     sleep=sleep, headers=headers, timeout=10)

        if response is False:  # not modified
            return False
        with open(tmpfile, "wb") as fp:
            fp.write(response.content)

        if not filename:
            filename = self.download_name_file(tmpfile,
                                               basefile,
                                               assumedfilename)
        if not os.path.exists(filename):
            util.robust_rename(tmpfile, filename)
            updated = True
        elif self.download_is_different(filename, tmpfile):
            if archive:
                version = self.get_archive_version(basefile)
                self.store.archive(basefile, version, overwrite=self.download_archive_overwrite, copy=self.download_archive_copy)
            util.robust_rename(tmpfile, filename)
            updated = True
        else:
            updated = False

        if updated:
            # OK we have a new file in place. Now examine the
            # headers to find if we should change file
            # modification time (last-modified) and/or create a
            # .etag file (etag)
            if response.headers.get("last-modified"):
                mtime = calendar.timegm(util.parse_rfc822_date(
                    response.headers["last-modified"]).timetuple())
                os.utime(filename, (time.time(), mtime))
            if response.headers.get("etag"):
                with open(filename + ".etag", "w") as fp:
                    etag = response.headers["etag"]
                    if isinstance(etag, bytes):
                        etag = etag.decode()
                    fp.write(etag)
            # FIXME: temporary workaround of the issue that opening a
            # tempfile creates files readably only by the creating
            # user
            os.chmod(filename, stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP|stat.S_IWGRP|stat.S_IROTH)
        return updated

    def download_name_file(self, tmpfile, basefile, assumedfile):
        """TBD"""
        return assumedfile

    def download_is_different(self, existing, new):
        """Returns True if the new file is semantically different from the
        existing file.

        """
        return not filecmp.cmp(new, existing, shallow=False)

    def remote_url(self, basefile):
        """Get the URL of the source document at it's remote location,
        unless the source document is fetched by other means or if it
        cannot be computed from basefile only. The default
        implementation uses
        :py:data:`~ferenda.DocumentRepository.document_url_template`
        to calculate the url.

        Example:

        >>> d = DocumentRepository()
        >>> d.remote_url("123/a")
        'http://example.org/docs/123/a.html'
        >>> d.document_url_template = "http://mysite.org/archive/%(basefile)s/"
        >>> d.remote_url("123/a")
        'http://mysite.org/archive/123/a/'

        :param basefile: The basefile of the source document
        :type basefile: str
        :returns: The remote url where the document can be fetched, or ``None``.
        :rtype: str
        """
        return self.document_url_template % {'basefile': quote(basefile)}

    def generic_url(self, basefile, maindir, suffix):
        """
        Analogous to
        :py:meth:`ferenda.DocumentStore.path`, calculate
        the full local url for the given basefile and stage of
        processing.

        :param basefile: The basefile for which to calculate the local url
        :type  basefile: str
        :param  maindir: The processing stage directory (normally
                         ``downloaded``, ``parsed``, or ``generated``)
        :type   maindir: str
        :param   suffix: The file extension including period (i.e. ``.txt``,
                         not ``txt``)
        :type    suffix: str
        :returns: The local url
        :rtype: str
        """
        path = "%s/%s/%s%s" % (self.alias, maindir, basefile, suffix)
        return self.config.url + path

    def downloaded_url(self, basefile):
        """Get the full local url for the downloaded file for the
        given basefile.

        :param basefile: The basefile for which to calculate the local url
        :type  basefile: str
        :returns: The local url
        :rtype: str

        >>> d = DocumentRepository()
        >>> d.downloaded_url("123/a")
        'http://localhost:8000/base/downloaded/123/a.html'
        """

        return self.generic_url(basefile, 'downloaded', self.downloaded_suffix)

    # STEP 2: Parse the downloaded data into a structured XML document
    # with RDFa metadata.
    @classmethod
    def parse_all_setup(cls, config, *args, **kwargs):
        """
        Runs any action needed prior to parsing all documents in a
        docrepo. The default implementation does nothing.

        .. note::

           This is a classmethod for now (and that's why a config
           object is passsed as an argument), but might change to a
           instance method.
        """

    @classmethod
    def parse_all_teardown(cls, config, *args, **kwargs):
        """
        Runs any cleanup action needed after parsing all documents in
        a docrepo. The default implementation does nothing.

        .. note::

           Like :py:meth:`~ferenda.DocumentRepository.parse_all_setup`
           this might change to a instance method.
        """

    @decorators.action
    @decorators.managedparsing
    def parse(self, doc, needed=True):
        """Parse downloaded documents into structured XML and RDF.

        It will also save the same RDF statements in a separate
        RDF/XML file.

        You will need to provide your own parsing logic, but often
        it's easier to just override parse_{metadata,
        document}_from_soup (assuming your indata is in a HTML format
        parseable by BeautifulSoup) and let the base class read and
        write the files.

        If your data is not in a HTML format, or BeautifulSoup is not
        an appropriate parser to use, override this method.

        :param doc: The document object to fill in.
        :type  doc: ferenda.Document

        """
        soup = self.soup_from_basefile(doc.basefile, self.source_encoding)
        self.parse_metadata_from_soup(soup, doc)
        self.parse_document_from_soup(soup, doc)
        self.parse_entry_update(doc)
        return True  # Signals that everything is OK

    def parse_entry_update(self, doc):
        """Update the DocumentEntry json file for this document."""
        entry = DocumentEntry(self.store.documententry_path(doc.basefile))
        entry.basefile = doc.basefile  # do we even need this?
        entry.id = self.parse_entry_id(doc)
        entry.title = self.parse_entry_title(doc)
        entry.summary = self.parse_entry_summary(doc)
        entry.save()

    def parse_entry_id(self, doc):
        """Construct a id (URI) for the document, 
        to be stored in it's DocumentEntry json file.

        Normally, this is identical to the main document URI as
        specified in doc.uri.

        """
        return doc.uri

    def parse_entry_title(self, doc):
        """Construct a useful title for the document, like it's dcterms:title,
        to be stored in it's DocumentEntry json file."""
        title = doc.meta.value(URIRef(doc.uri), DCTERMS.title)
        if title:
            return str(title)
        else:
            return "Doc %s" % doc.basefile

    def parse_entry_summary(self, doc):
        """Construct a useful summary for the document, like it's dcterms:abstract,
        to be stored in it's DocumentEntry json file."""
        summary = doc.meta.value(URIRef(doc.uri), DCTERMS.abstract)
        if summary:
            if summary.datatype == RDF.XMLLiteral:
                return summary
            else:
                return str(summary)

    def soup_from_basefile(self, basefile, encoding='utf-8', parser='lxml'):
        """
        Load the downloaded document for basefile into a BeautifulSoup object

        :param basefile: The basefile for the downloaded document to parse
        :type  basefile: str
        :param encoding: The encoding of the downloaded document
        :type  encoding: str
        :returns: The parsed document as a ``BeautifulSoup`` object

        .. note::

           Helper function. You probably don't need to override it.
        """
        filename = self.store.downloaded_path(basefile)
        if not os.path.exists(filename):
            raise errors.NoDownloadedFileError("File '%s' not found" % filename)
        with codecs.open(filename, encoding=encoding, errors='replace') as fp:
            soup = bs4.BeautifulSoup(fp.read(), parser)
        return soup

    def parse_metadata_from_soup(self, soup, doc):
        """
        Given a BeautifulSoup document, retrieve all document-level
        metadata from it and put it into the given ``doc`` object's
        ``meta`` property.

        .. note::

           The default implementation sets ``rdf:type``,
           ``dcterms:title``, ``dcterms:identifier`` and
           ``prov:wasGeneratedBy`` properties in ``doc.meta``, as well
           as setting the language of the document in ``doc.lang``.

        :param soup: A parsed document, as ``BeautifulSoup`` object
        :param  doc: Our document
        :type   doc: ferenda.Document
        :returns: None
        """
        # set rdf:type and dcterms:identifier of document automatically?
        # set title and other simple things
        # Default language unless we can find out from source doc?
        # Check html/@xml:lang || html/@lang
        root = soup.find('html')
        try:
            doc.lang = root['xml:lang']
        except (KeyError, TypeError):
            try:
                doc.lang = root['lang']
            except (KeyError, TypeError):
                doc.lang = self.lang
        try:
            title = soup.find('title').string
        except AttributeError:
            title = None
        # create document-level metadata
        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        if title:
            d.value(self.ns['dcterms'].title, Literal(title, lang=doc.lang))
        d.value(self.ns['dcterms'].identifier, doc.basefile)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())

    def parse_document_from_soup(self, soup, doc):
        """
        Given a BeautifulSoup document, convert it into the provided
        ``doc`` object's ``body`` property as suitable
        :py:mod:`ferenda.elements` objects.

        .. note::

           The default implementation respects
           :py:data:`~ferenda.DocumentRepository.parse_content_selector`
           and
           :py:data:`~ferenda.DocumentRepository.parse_filter_selectors`.

        :param soup: A parsed document as a ``BeautifulSoup`` object
        :param  doc: Our document
        :type   doc: ferenda.Document
        :returns: None
        """
        soups = soup.select(native_str(self.parse_content_selector))
        if len(soups) == 0:
            raise errors.ParseError("%s: parse_content_selector %r matches nothing" %
                                    (doc.basefile, self.parse_content_selector))
        if len(soups) > 1:
            self.log.warning("parse_content_selector %r matches more than one tag" %
                             (self.parse_content_selector))
        soup = soups[0]
        for filter_selector in self.parse_filter_selectors:
            for tag in soup.select(native_str(filter_selector)):
                # tag.decompose()
                tag.extract()  # decompose fails on some trees

        doc.body = elements_from_soup(soup)

    def patch_if_needed(self, basefile, text):
        """Given *basefile* and the entire *text* of the downloaded or
        intermediate document, find if there exists a patch file under
        ``self.config.patchdir``, and if so, applies it. Returns
        (patchedtext, patchdescription) if so, (text,None)
        otherwise.

        :param basefile: The basefile of the text
        :type basefile: str
        :param text: The text to be patched
        :type text: bytes

        """

        # 1. do we have a patch?
        if self.config.ignorepatch is True:
            return text, None
            
        patchstore = self.documentstore_class(self.config.patchdir + os.sep + self.alias)
        patchpath = patchstore.path(basefile, "patches", ".patch")
        descpath = patchstore.path(basefile, "patches", ".desc")

        if not os.path.exists(patchpath):
            return text, None
        from .thirdparty.patchit import PatchSet, PatchSyntaxError, PatchConflictError
        with codecs.open(patchpath, 'r', encoding=self.source_encoding) as pfp:
            if self.config.patchformat == "rot13":
                # replace the rot13 obfuscated stream with a plaintext stream
                pfp = StringIO(codecs.decode(pfp.read(), "rot13"))
            # this might raise a PatchSyntaxError
            try:
                ps = PatchSet.from_stream(pfp)
            except PatchSyntaxError as e:
                raise errors.PatchError(e)
            
        assert len(ps.patches) == 1

        if ps.patches[0].hunks[0].comment:
            desc = ps.patches[0].hunks[0].comment
        elif os.path.exists(descpath):
            desc = util.readfile(descpath)
        else:
            desc = "(No patch description available)"
        try:
            lines = text.split("\n")
            ps.patches[0].adjust(lines)
            stream = ps.patches[0].merge(lines)
            return "\n".join(stream), desc
        except PatchConflictError as e:
            raise errors.PatchError(e)

    def make_document(self, basefile=None, version=None):
        """
        Create a :py:class:`~ferenda.Document` objects with basic
        initialized fields.

        .. note::

           Helper method used by the
           :py:func:`~ferenda.decorators.makedocument` decorator.

        :param basefile: The basefile for the document
        :type  basefile: str
        :rtype: ferenda.Document
        """
        doc = Document()

        # when parsing a single file from the command line, the
        # basefile might be in unicode NFD (eg "./ferenda.build myndfs
        # säifs/2000:6" on mac). Normalize this.
        if basefile:
            basefile = unicodedata.normalize("NFC", basefile)
            doc.uri = self.canonical_uri(basefile)
        doc.basefile = basefile
        doc.version = version
        doc.meta = self.make_graph()
        doc.lang = self.lang
        doc.body = Body()
        return doc

    def make_graph(self):
        """
        Initialize a rdflib Graph object with proper namespace prefix
        bindings (as determined by
        :py:data:`~ferenda.DocumentRepository.namespaces`)

        :rtype: rdflib.Graph
        """
        g = Graph()
        for prefix, uri in list(self.ns.items()):
            # print "Binding %s to %s" % (prefix,uri)
            g.bind(prefix, uri)
        return g

    def create_external_resources(self, doc):
        """Optionally create external files that go together with the
        parsed file (stylesheets, images, etc).

        The default implementation does nothing.

        :param doc: The document
        :type  doc: ferenda.Document
        """

    def render_xhtml(self, doc, outfile=None):
        """Renders the parsed object structure as a XHTML file with
        RDFa attributes (also returns the same XHTML as a string).

        :param doc: The document to render
        :type  doc: ferenda.Document
        :param outfile: The file name for the XHTML document
        :type  outfile: str
        :returns: The XHTML document
        :rtype: str
        """
        xhtmldoc = self.render_xhtml_tree(doc)
        # Doctypes for XHTML+RDFa documents seem to be optional in RDFa 1.1
        # doctype = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML+RDFa 1.0//EN" '
        #           '"http://www.w3.org/MarkUp/DTD/xhtml-rdfa-1.dtd">')
        res = etree.tostring(xhtmldoc,
                             pretty_print=True,
                             xml_declaration=True,
                             encoding='utf-8',
                             # method='c14n',  # doesn't seem to produce pretty_print output
                             # doctype=doctype
                             )
        err = self.render_xhtml_validate(xhtmldoc)
        if err:
            util.ensure_dir(outfile)
            with open(outfile+".invalid", "wb") as fp:
                fp.write(res)
            raise errors.InvalidTree("%s. Invalid tree saved as %s.invalid" % (err, outfile))

        with self.store.open_parsed(doc.basefile, mode="wb", version=doc.version) as fp:
            fp.write(res)
        # it's a bit nonsensical to first use _open (which leaves the
        # target file untouched if the contents don't change) and then
        # go ahead and update the timestamp, but it helps those cases
        # where a file gets parsed again and again and again.
        os.utime(outfile, None)  # update access/modified timestamp
        return res

    def render_xhtml_tree(self, doc):
        """Renders the parsed object structure as a :py:class:`lxml.etree._Element` object.

        :param doc: The document to render
        :type  doc: ferenda.Document
        :returns: The XHTML document as a lxml structure
        :rtype: lxml.etree._Element

        """
        XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
        XSI_SCHEMALOC = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"
        META = "{http://www.w3.org/1999/xhtml}meta"
        TITLE = "{http://www.w3.org/1999/xhtml}title"
        LINK = "{http://www.w3.org/1999/xhtml}link"
        HEAD = "{http://www.w3.org/1999/xhtml}head"

        def render_head(g, uri, children=None):
            if not children:
                children = []
                # if revlink == True, we're serializing triples for
                # the main subject. So other triples that references
                # the main subject should have the @rev attribute
                # set. This also means we don't have to set @about
                # below, and that we should create a <title> tag for
                # any dcterms:title triple (ideally, for any property
                # that is rdfs:subPropertyOf dcterms:title, but...
                revlink = True
            else:
                revlink = False
            # we sort to get a predictable order (by predicate, then by object)
            for (subj, pred, obj) in sorted(g, key=lambda t: (t[1], t[2])):
                if str(subj) != uri and str(obj) != uri:
                    # This isn't a triple we should serialize to RDFa,
                    # at least not in this iteration
                    continue

                if g.qname(pred) == "dcterms:title" and revlink:
                    childattrs = OrderedDict([('property', 'dcterms:title')])
                    if obj.language != doc.lang:
                        childattrs[XML_LANG] = obj.language or ""
                    e = Element(TITLE, childattrs)
                    e.text = str(obj)
                    children.append(e)

                elif isinstance(obj, URIRef) and str(subj) == uri:
                    childattrs = OrderedDict([('rel', g.qname(pred)),
                                              ('href', str(obj))])
                    children.append(Element(LINK, childattrs))
                    if not revlink:
                        children[-1].set('about', uri)
                    if str(obj) == doc.uri:
                        self.log.warning(
                            "Avoiding serializing circular graph (%s)" %
                            doc.uri)
                    else:
                        render_head(g, str(obj), children)

                elif isinstance(obj, URIRef):
                    if revlink:
                        childattrs = OrderedDict([('rev', g.qname(pred)),
                                                  ('href', str(subj))])
                        children.append(Element(LINK, childattrs))
                elif isinstance(obj, BNode):
                    if g.value(obj, RDF.first):
                        # the BNode is really a RDF list
                        coll = Collection(g, obj)
                        for thing in coll:
                            if isinstance(thing, URIRef):
                                childattrs = OrderedDict([('rel', g.qname(pred)),
                                                          ('inlist', ''),
                                                          ('href', str(thing))])
                                children.append(Element(LINK, childattrs))
                            elif isinstance(thing, Literal):
                                childattrs = OrderedDict([('property', g.qname(pred)),
                                                          ('inlist', ''),
                                                          ('content', str(obj))])
                                # FIXME possibly add datatype and/or lang
                                children.append(Element(META, childattrs))
                        for thing in coll:
                            if isinstance(thing, URIRef):
                                render_head(g, str(thing), children)

                    else:
                        # serialize this triple and any other triples
                        # where this BNode is a subject of a triple with a
                        # URIRef or Literal as object (bnodes pointing to
                        # bnodes not supported)
                        childattrs = OrderedDict([('rel', g.qname(pred)),
                                                  ('resource', obj.n3())])
                        children.append(Element(LINK, childattrs))
                        if not revlink:
                            children[-1].set('about', uri)
                        for (p, o) in sorted(g.predicate_objects(obj)):
                            if isinstance(o, URIRef):
                                childattrs = OrderedDict([('about', obj.n3()),
                                                         ('rel', g.qname(p)),
                                                         ('href', str(o))])
                                children.append(Element(LINK, childattrs))
                            elif isinstance(o, Literal):
                                childattrs = OrderedDict([('about', obj.n3()),
                                                          ('property', g.qname(p)),
                                                          ('content', str(o))])
                                if o.datatype:
                                    childattrs['datatype'] = g.qname(o.datatype)
                                if o.language:
                                    childattrs[XML_LANG] = o.language
                                children.append(Element(META, childattrs))
                            else:
                                raise errors.ParseError("Can't serialize a BNode-%s triple" % o.__class__.__name__)
                else:  # this must be a literal, ie something to be
                       # rendered as <meta property="..."
                       # content="..."/>
                    childattrs = OrderedDict([('property', g.qname(pred)),
                                              ('content', str(obj))])
                    if obj.datatype:
                        childattrs['datatype'] = g.qname(obj.datatype)
                    elif obj.language:
                        childattrs[XML_LANG] = obj.language
                    elif doc.lang:
                        childattrs[XML_LANG] = ""
                    if not revlink:
                        childattrs['about'] = uri
                    children.append(Element(META, childattrs))
            e = Element(HEAD, {'about': uri})
            e.extend(children)
            return e
        bodycontent = doc.body.as_xhtml(doc.uri)
        headcontent = render_head(doc.meta, doc.uri)

        # add any css files to headcontent
        if hasattr(doc, 'cssuris'):
            for cssuri in doc.cssuris:
                headcontent.append(Element(LINK, {"rel": "stylesheet", "href": cssuri}))

        # examine headcontent and bodycontent to only use prefixes
        # that are actually used

        prefixes = dict([(str(x[1]), x[0]) for x in self.ns.items()])
        used = {"http://www.w3.org/1999/xhtml": None}
        for e in bodycontent.iter():
            # Find the "jclark" syntax namespaces (eg "{http://www.cars.com/xml}part")
            if "}" in e.tag:
                ns = e.tag.split("}", 1)[0][1:]
                if ns not in used:
                    used[ns] = prefixes[ns]
            # Find undeclared prefixes and guess which NS they map to
            # (similarly to the expansion of property/datatype/rel below):
            for attr in ('typeof', 'rel'):
                if e.get(attr) and ':' in e.get(attr):
                    prefix = e.get(attr).split(":", 1)[0]
                    ns = str(self.ns[prefix])
                    if ns not in used:
                        used[ns] = prefixes[ns]

        nsmap = dict([(x[1], x[0]) for x in used.items()])
        for e in headcontent.iter():
            # examine @property @datatype @rel for CURIEs and make
            # sure they're mapped
            for a in ('property', 'datatype', 'rel'):
                v = e.get(a)
                if v and ":" in v:
                    prefix = v.split(":")[0]
                    if prefix not in nsmap:
                        nsmap[prefix] = str(self.ns[prefix])
                if v == "rdf:type":
                    # prefixes *used by* any rdf:type declarations
                    # must also be included. The href of that element
                    # includes the resource URI, but not in CURIE
                    # form, so we compare agains all known namespace
                    # URI:s
                    uri = e.get("href")
                    for prefix, nsuri in self.ns.items():
                        if uri.startswith(str(nsuri)):
                            nsmap[prefix] = str(nsuri)

        E = ElementMaker(namespace="http://www.w3.org/1999/xhtml",
                         nsmap=nsmap)
        htmlattrs = {XSI_SCHEMALOC: "http://www.w3.org/1999/xhtml http://www.w3.org/MarkUp/SCHEMA/xhtml-rdfa-2.xsd",
                     "version": "XHTML+RDFa 1.1"}
        if doc.lang:
            htmlattrs[XML_LANG] = doc.lang
        xhtmldoc = E.html(
            htmlattrs,
            headcontent,
            bodycontent,
        )
        return xhtmldoc

    def render_xhtml_validate(self, xhtmldoc):
        """TBD"""
        # the default validator makes sure we haven't created
        # duplicate sub-resources, and that we haven't created too
        # many resources.
        resources = set()
        # it's important that we only search for divs, since spans are
        # used inside divs with same @abouts to add extra metadata to
        # the @about resource
        for divnode in xhtmldoc.xpath(".//x:div[@about]",
                                      namespaces={'x': 'http://www.w3.org/1999/xhtml'}):
            if divnode.get("about") in resources:
                return "Resource %s encountered twice" % divnode.get("about")
            resources.add(divnode.get("about"))
        if self.max_resources and len(resources) > self.max_resources:
            return "Found over %s resources (%s), that's probably not right" % (self.max_resources, len(resources))
        return None  # no news is good news

    def parsed_url(self, basefile):
        """Get the full local url for the parsed file for the
        given basefile.

        :param basefile: The basefile for which to calculate the local url
        :type  basefile: str
        :returns: The local url
        :rtype: str
        """
        return self.generic_url(basefile, 'parsed', '.xhtml')

    def distilled_url(self, basefile):
        """Get the full local url for the distilled RDF/XML file for the
        given basefile.

        :param basefile: The basefile for which to calculate the local url
        :type  basefile: str
        :returns: The local url
        :rtype: str
        """
        return self.generic_url(basefile, 'distilled', '.rdf')

    #
    #
    # STEP 3: Extract and store the RDF data
    #
    #
    @classmethod
    def relate_all_setup(cls, config, *args, **kwargs):
        """Runs any cleanup action needed prior to relating all documents in
        a docrepo. The default implementation clears the corresponsing
        context (see :py:meth:`~ferenda.DocumentRepository.dataset_uri`)
        in the triple store.

        .. note::

           Like :py:meth:`~ferenda.DocumentRepository.parse_all_setup`
           this might change to a instance method.

        Returns False if no relation needs to be done (as determined
        by the timestamp on the dump nt file)

        """
        # FIXME: should use dataset_uri(), but that's a instancemethod
        context = "%sdataset/%s" % (config.url, cls.alias)
        
        docstore = cls.documentstore_class(config.datadir + os.sep + cls.alias, storage_policy=cls.storage_policy)
        dumppath = docstore.resourcepath("distilled/dump.nt")

        # log = cls._setup_logger(cls.alias)
        log = logging.getLogger(cls.alias)

        # check if we need to work at all.
        xhtmlfiles = (docstore.distilled_path(x)
                      for x in docstore.list_basefiles_for("generate"))
        if (not config.force and util.outfile_is_newer(xhtmlfiles, dumppath)):
            if 'upload' in config and config.upload:
                log.info("Clearing context %s before uploading dump" % (
                    context))
                store = TripleStore.connect(config.storetype,
                                            config.storelocation,
                                            config.storerepository)
                store.clear(context)
                log.info("Adding %s to %s" % (dumppath, context))
                store.add_serialized_file(dumppath, "nt", context)
            return False  # signals to Manager that no work needs to be done

        if config.force:
            log.info("Clearing context %s at repository %s" % (
                context, config.storerepository))
            store = TripleStore.connect(config.storetype,
                                        config.storelocation,
                                        config.storerepository)
            store.clear(context)

        if 'relate' in config and config.relate is False:
            log.info("%s: Not relating" % cls.alias)
            return False
        # if config.fulltextindex, we should attempt to connect to the
        # index to see if the server is up. Also, a good idea in a
        # multiprocessing context to have the main controlling process
        # create the schema rather than the first random worker
        # process.
        if config.fulltextindex:
            repos = kwargs.get("otherrepos", [])
            if kwargs.get("currentrepo"):
                repos.insert(0, kwargs["currentrepo"])
            FulltextIndex.connect(config.indextype,
                                  config.indexlocation,
                                  repos=repos)

        # Bulk upload: We implemented an alternate way of loading the
        # triplestore, where we didn't POST into the triplestore
        # once for each basefile, but instead appended everything to a
        # tempfile which was then bulk loaded into the triplestore at
        # teardown. However, this was not faster (slightly slower)
        # and more complex. In order to enable it again, just
        # uncomment below.

        # create the empty temp NTriples file for appending to:
        # with docstore._open(docstore.resourcepath("distilled/_dump.nt.temp"), "w"):
        #     pass

        # we can't clear the whoosh index in the same way as one index
        # contains documents from all repos. But we need to be able to
        # clear it from time to time, maybe with a clear/setup method
        # in manager? Or fulltextindex maybe could have a clear method
        # that removes all documents for a particular repo?
        return True

    @classmethod
    def relate_all_teardown(cls, config, *args, **kwargs):
        """Runs any cleanup action needed after relating all documents in a
        docrepo. The default implementation dumps all RDF data loaded
        into the triplestore into one giant N-Triples file.

        .. note::

           Like :py:meth:`~ferenda.DocumentRepository.parse_all_setup`
           this might change to a instance method.

        """
        # FIXME: should use dataset_uri(), but that's a instancemethod
        # log = cls._setup_logger(cls.alias)
        log = logging.getLogger(cls.alias)

        context = "%sdataset/%s" % (config.url, cls.alias)
        docstore = DocumentStore(config.datadir + os.sep + cls.alias)
        dumppath = docstore.resourcepath("distilled/dump.nt")
        temppath = docstore.resourcepath("distilled/dump.nt.temppath")
        store = TripleStore.connect(config.storetype,
                                    config.storelocation,
                                    config.storerepository)
        values = {'repository': config.storerepository,
                  'context': context,
                  'dumpfile': dumppath,
                  'tempfile': temppath}

        # If using the Bulk upload functionality (see
        # relate_all_setup), do the actual bulk upload.
        if config.bulktripleload:
            with open(temppath, "wb") as fp:
                filecount = 0
                for filename in os.listdir(os.path.dirname(dumppath)):
                    if not filename.endswith(".nt") or filename == "dump.nt":
                        continue
                    filecount += 1
                    filename = os.path.dirname(dumppath) + os.sep + filename
                    with open(filename, "rb") as ffp:
                        fp.write(ffp.read())
                    util.robust_remove(filename)
            log.debug("Concatenated %s nt files into %s" % (filecount, temppath))
            with util.logtime(log.info,
                              "Loaded %(triplecount)s triples to context %(context)s from %(tempfile)s (%(elapsed).3f sec)",
                              values):
                store.add_serialized_file(temppath, format="nt", context=context)
                # just to report the number of dumped triples -- may be unneccesary
                values['triplecount'] = sum(1 for line in open(temppath))
                os.unlink(temppath)

        # then extract a new dumppath file (which should have the exact
        # same contents as the temppath file, but this comes directly from
        # the triplestore
        try:
            with util.logtime(log.info,
                              "Dumped %(triplecount)s triples from context %(context)s to %(dumpfile)s (%(elapsed).3f sec)",
                              values):
                util.ensure_dir(dumppath)
                store.get_serialized_file(dumppath, format="nt", context=context)
                # just to report the number of dumped triples -- may be unneccesary
                with open(dumppath) as fp:
                    values['triplecount'] = sum(1 for line in fp)
        except requests.exceptions.HTTPError as e:
            # probably the dataset URI didn't exist because no triples
            # have been stored. Create a empty dumpfile.
            log.warning("Couldn't get dataset, creating empty %s: %s" %
                        (dumppath, e))
            util.ensure_dir(dumppath)
            with open(dumppath, "w"):
                pass
        return True

    @decorators.action
    @decorators.ifneeded('relate')
    @decorators.updateentry('relate')
    def relate(self, basefile, otherrepos=[], needed=RelateNeeded(True,True,True)):
        """Runs various indexing operations for the document.

           This includes inserting RDF statements into a triple store,
           adding this document to the dependency list to all
           documents that it refers to, and putting the text of the
           document into a fulltext index.

        """
        if self.config.relate is False:
            self.log.warning("%s: repo %s config has relate=False" %
                             (basefile, self.alias))
            return False
        entry = DocumentEntry(self.store.documententry_path(basefile))
        timings = {'basefile': basefile,
                   'e_triples': 0,
                   'e_deps': 0,
                   'e_fulltext': 0,
                   'v_triples': -1,
                   'v_deps': -1,
                   'v_fulltext': -1}
        with util.logtime(self.log.info,
                          "relate OK (%(elapsed).3f sec) [%(e_triples).3f:%(v_triples)s/%(e_deps).3f:%(v_deps)s/%(e_fulltext).3f:%(v_fulltext)s]",
                          timings):
            # first, load fulltextindex, then add dependencies, lastly
            # load triplestore. fulltextindex is slightly more picky
            # abt types (it requires date fields to actually be dates,
            # not random strings), which keeps us from entering
            # mistyped info into the triplestore.

            # When otherrepos = [], should we still provide self as one repo? Yes.
            if self not in otherrepos:
                otherrepos.append(self)

            if self.config.fulltextindex and needed.fulltext:
                start = time.time()
                timings['v_fulltext'] = self.relate_fulltext(basefile, otherrepos)
                timings['e_fulltext'] = time.time() - start
                entry.indexed_ft = datetime.now()

            if needed.dependencies:
                start = time.time()
                timings['v_deps'] = self.relate_dependencies(basefile, otherrepos)
                timings['e_deps'] = time.time() - start
                entry.indexed_dep = datetime.now()
            if needed.triples:
                # If using the Bulk upload feature, append to the
                # temporary file that is to be bulk uploaded (see
                # relate_all_setup).
                #
                # Exception: In order to make relate of a single basefile
                # meaningful (ie when self.config.all is False), we'd
                # like to insert into the db right away in those cases.
                if self.config.bulktripleload and self.config.all:  
                    nttemp = self.store.resourcepath("distilled/dump.%s.%s.nt" % (self.config.clientname, os.getpid()))
                    values = {'basefile': basefile,
                              'nttemp': nttemp}
                    with util.logtime(self.log.debug,
                                      "Added %(triplecount)s triples to %(nttemp)s (%(elapsed).3f sec)",
                                      values):
                        data = open(self.store.distilled_path(basefile), "rb").read()
                        g = Graph().parse(data=data)
                        with open(nttemp, "ab") as fp:
                            fp.write(g.serialize(format="nt"))
                        values['triplecount'] = len(g)
                else:
                    start = time.time()
                    if self.config.force:
                        self.relate_triples(basefile)
                    else:
                        timings['v_triples'] = self.relate_triples(basefile, removesubjects=True)
                    timings['e_triples'] = time.time() - start
                entry.indexed_ts = datetime.now()
        entry.save()

    def _get_triplestore(self, **kwargs):
        if not hasattr(self, '_triplestore'):
            self._triplestore = TripleStore.connect(self.config.storetype,
                                                    self.config.storelocation,
                                                    self.config.storerepository,
                                                    **kwargs)
        return self._triplestore

    def relate_triples(self, basefile, removesubjects=False):
        """Insert the (previously distilled) RDF statements into the
        triple store.

        :param basefile: The basefile for the document containing the
                         RDF statements.
        :type  basefile: str
        :param removesubjects: Whether to remove all identified subjects
                               from the triplestore beforehand (to clear
                               the previous version of this basefile's
                               metadata). FIXME: not yet used
        :type  removesubjects: bool
        :returns: None
        """
        ts = self._get_triplestore()  # init self._triplestore
        with util.logtime(self.log.debug,
                          "Added %(rdffile)s to context %(context)s (%(elapsed).3f sec)",
                          {'basefile': basefile,
                           'context': self.dataset_uri(),
                           'dataset': self.dataset_uri(),
                           'rdffile': self.store.distilled_path(basefile),
                           'triplestore': self.config.storelocation}):
            with open(self.store.distilled_path(basefile), "rb") as fp:
                data = fp.read()
            ts.add_serialized(data, format="xml", context=self.dataset_uri())
            #ts.add_serialized_file(self.store.distilled_path(basefile), format="xml",
            #                       context=self.dataset_uri())
            return len(data)

    def _get_fulltext_indexer(self, repos, batchoptimize=False):
        if not hasattr(self, '_fulltextindexer'):

            idx = FulltextIndex.connect(self.config.indextype,
                                        self.config.indexlocation,
                                        repos=repos)
            self._fulltextindexer = idx

            # The batchwriter functionality seems a litte broken --
            # gave a "ValueError: seek of closed file" error. Since
            # it's used to speed things up, and we now have
            # ElasticSearch support for that, it's disabled until
            # further notice.
            # if 'all' in self.config:
            #     self._fulltextindexer._batchwriter = True

        return self._fulltextindexer

    def relate_dependencies(self, basefile, repos=[]):
        """For each document that the basefile document refers to, attempt to
find this document in the current or any other docrepo, and add the
parsed document path to that documents dependency file."""
        values = {'basefile': basefile,
                  'deps': 0}
        with util.logtime(self.log.debug,
                          "Registered %(deps)s dependencies (%(elapsed).3f sec)",
                          values):
            g = Graph().parse(data=util.readfile(self.store.distilled_path(basefile), encoding="utf-8"), format="xml")
            subjects = set([s for s, p, o in g])
            for (s, p, o) in g:
                # the graph for a single doc can describe
                # multiple, linked, resources. Don't attempt to
                # find basefiles for these resources, even if they
                # occur as objects in the graphs as well.
                if p in (RDF.type, OWL.sameAs):
                    continue
                if o in subjects:
                    continue
                # for each URIRef in graph
                if isinstance(o, URIRef):
                    handled = False
                    # find out if any docrepo can handle it
                    for repoidx, repo in enumerate(repos):
                        dep_basefile = repo.basefile_from_uri(str(o))
                        if dep_basefile and (
                                (repo != self) or
                                (dep_basefile != basefile)):
                            # if so, add to that repo's dependencyfile
                            pp = self.store.parsed_path(basefile)
                            res = repo.add_dependency(dep_basefile, pp)
                            handled = True
                            values['deps'] += 1
                            break
                    if handled:
                        # reorder repos in MRU order
                        repos.insert(0, repos.pop(repoidx))
        return "%s[%s]" % (values['deps'], len(repos))

    def add_dependency(self, basefile, dependencyfile):
        """Add the *dependencyfile* to *basefile* s dependency file. Returns
        True if anything new was added, False otherwise

        """
        
        present = False
        if os.path.exists(self.store.dependencies_path(basefile)):
            with self.store.open_dependencies(basefile) as fp:
                for line in fp:
                    if isinstance(line, bytes):
                        line = line.decode('utf-8')
                    if line.strip() == dependencyfile:
                        present = True
        if not present:
            with self.store.open_dependencies(basefile, "ab") as fp:
                fp.write((dependencyfile + os.linesep).encode("utf-8"))
            self.log.debug("Adding %s to %s (basefile %s in repo %s)" %
                           (dependencyfile,
                            self.store.dependencies_path(basefile),
                            basefile,
                            self.alias))
        return not present  # return True if we added something, False otherwise

    def relate_fulltext(self, basefile, repos=None):
        """Index the text of the document into fulltext index. Also indexes
        all metadata that facets() indicate should be indexed.

        :param basefile: The basefile for the document to be indexed.
        :type  basefile: str
        :returns: None

        """
        values = {'basefile': basefile,
                  'resources': 0,
                  'words': 0}
        with util.logtime(self.log.debug,
                          "Added %(resources)s resources (%(words)s words) to fulltext index  (%(elapsed).3f sec)", values):
            if repos is None:
                repos = []
            indexer = self._get_fulltext_indexer(repos)
            tree = etree.parse(self.store.parsed_path(basefile))
            g = Graph()
            desc = Describer(
                g.parse(
                    data=util.readfile(
                        self.store.distilled_path(basefile))))
            qname_graph = self.make_graph()
            body = tree.find(".//{http://www.w3.org/1999/xhtml}body")
            resources = self._relate_fulltext_resources(body)
            for resource in resources:
                if isinstance(resource, tuple):
                    # new-style API -- each resource may be
                    # accompanied with a default metadata dict
                    resource, kwargs = resource
                else:
                    # old-style API
                    kwargs = {}
                if resource.tag == "{http://www.w3.org/1999/xhtml}head":
                    continue
                about = resource.get('about')
                if not about:  # if the <body> element lacks @about
                    # maybe we can resolve about if we have an @id
                    if resource.get('id'):
                        about = body.get("about") + "#" + resource.get("id")
                        resource.set('about', about) # needed in _relate_fulltext_value
                    else:
                        continue
                if isinstance(about, bytes):  # happens under py2
                    about = about.decode()    # pragma: no cover
                desc.about(about)
                repo = self.alias
                if isinstance(repo, bytes):  # again, py2
                    repo = repo.decode()     # pragma: no cover
                plaintext = util.normalize_space(self._extract_plaintext(resource, resources))
                # print("%s -> %s" % (resource.get("about"), plaintext))
                for facet in self.facets():
                    k, v = self._relate_fulltext_value(facet, resource, desc)
                    if v is not None:
                        if k is None:
                            k = qname_graph.qname(facet.rdftype).replace(":", "_")
                        kwargs[k] = v
                # print("%s -> %s" % (about, kwargs))
                indexer.update(uri=about,
                               repo=repo,
                               basefile=basefile,
                               text=plaintext,
                               **kwargs)
                values['resources'] += 1
                values['words'] += len(plaintext.split())
            indexer.commit()  # NB: Destroys indexer._writer
            return values['resources']

    def _relate_fulltext_resources(self, body):
        res = []
        uris = set()
        for r in body.findall(".//*[@about]"):
            if r.get("about") not in uris:
                uris.add(r.get("about"))
                res.append(r)
        return [body] + res

    def _relate_fulltext_value(self, facet, resource, desc):
        if facet.toplevel_only and resource.tag != '{http://www.w3.org/1999/xhtml}body':
            return None, None
        # facets don't tell whether their sought subjects
        # are URIRefs or Literals. Look for both.
        v = desc.getrels(facet.rdftype)
        if isinstance(facet.indexingtype, fulltextindex.Resource):
            newv = []
            for value in sorted(v):
                # abuse the resourcelabel func a little
                label = facet.resourcelabel({None: value}, None, self.commondata)
                newv.append({'iri': value,
                             'label': label})
            v = newv
        elif not v:
            v = sorted(desc.getvalues(facet.rdftype))

        if not v:
            return None, None
        if facet.multiple_values:
            v = v
        elif len(v) > 1:
            self.log.warning(
                "%s had multiple values for %s but multiple_values was not specified, randomly selecting one" %
                (resource.get("about", "[Unknown URI]"), 
                 facet.rdftype))
            v = v[0]
        else:
            v = v[0]
        # FIXME: use facet.dimension_label iff present
        if facet.dimension_label:
            k = facet.dimension_label
            # if dimension_label specified, we
            # probably have a custom selector and a
            # synthesized property. Synthesize the
            # value by calling the selector (in a
            # roundabout way since selector expects a
            # dict and a key)
            v = facet.selector({None: v}, None, self.commondata)
        else:
            k = None
        return k, v

    def _extract_plaintext(self, node, resources):
        # helper to extract any text from a elementtree node,
        # excluding subnodes that are resources themselves (as
        # determined by _relate_fulltext_resources).  (Also, exclude
        # verbatim nodes -- these are almost by definition not part of
        # the enclosing resource, but rather something that we haven't
        # been able to model as a proper sub-resource (eg. verbatim
        # appendicies)
        plaintext = node.text if node.text else ""
        for subnode in node:
            if subnode not in resources and subnode.get("class") != "verbatim":
                plaintext += self._extract_plaintext(subnode, resources)
        if node.tail:
            plaintext += node.tail
        # append trailing space for block-level elements (including
        # <br>, <img> and some others that formally are inline
        # elements)
        trailspace = "" if node.tag in ("a" "b", "i", "span") else " "
        return plaintext.strip() + trailspace

    def facets(self):
        """Provides a list of :py:class:`~ferenda.Facet` objects that specify
        how documents in your docrepo should be grouped.

        Override this if you want to specify your own way of grouping data in your docrepo."""
        return [Facet(RDF.type),
                Facet(DCTERMS.title),
                Facet(DCTERMS.publisher),
                Facet(DCTERMS.identifier),
                Facet(DCTERMS.issued)
                ]

    def faceted_data(self):
        """Provides a list of dicts, each containing a row of information
        about a single document in the repository. The exact fields
        provided are controlled by the list of
        :py:class:`~ferenda.Facet` objects returned by
        :py:meth:`~ferenda.DocumentRepository.facet`.

        .. note::

           The same document can occur multiple times if any of it's
           facets have ``multiple_values`` set, once for each different
           values that that facet has.

        """
        # use some caching logic around the actual meat of the
        # function (the call to facet_query and facet_select. Custom
        # implementations might prefer to override facet_select
        # (eg. to add additional useful data).
        cachepath = self.store.resourcepath("toc/faceted_data.json")
        dumppath = self.store.resourcepath("distilled/dump.nt")
        if ((not self.config.force) and
                os.path.exists(cachepath) and
                os.path.getsize(cachepath) > 2 and  # a empty resultset is '[]' ie two bytes
                util.outfile_is_newer([dumppath], cachepath)):
            self.log.debug("Loading faceted_data from %s" % cachepath)
            hook = util.make_json_date_object_hook('dcterms_issued')
            with open(cachepath) as fp:
                data = json.load(fp, object_hook=hook)
        else:
            data = self.facet_select(self.facet_query(self.dataset_uri()))
            # make sure the dataset contains no duplicate entries --
            # note that it's not enough to check for row['uri']
            # uniqueness, as multiple rows can share uri but differ in
            # other values (if multiple_values = True for that facet)
            rows = set()
            dupes = []
            for idx, row in enumerate(list(data)):
                t = tuple(sorted(row.items())) # maybe use t.__hash__() to save space?
                if t not in rows:
                    rows.add(t)
                else:
                    self.log.warning("faceted_data: found duplicate row (uri %s) at #%s" % (row['uri'], idx))
                    dupes.append(idx)
            for idx in reversed(dupes):
                self.log.warning("faceted_data: popping %s" % idx)
                data.pop(idx) # note
            uris = None


            util.ensure_dir(cachepath)
            with open(cachepath, "w") as fp:
                self.log.debug("Saving faceted_data to %s" % cachepath)
                s = json.dumps(data, indent=4, separators=(', ', ': '), default=util.json_default_date)
                fp.write(s)
            if os.path.getsize(cachepath) == 0:
                util.robust_remove(cachepath)
        return data

    def facet_query(self, context):
        """Constructs a SPARQL SELECT query that fetches all
        information needed to create faceted data.

        :param context: The context (named graph) to which to limit
                        the query.
        :type  context: str
        :returns: The SPARQL query
        :rtype: str

        Example:

        >>> d = DocumentRepository()
        >>> expected = \"""PREFIX dcterms: <http://purl.org/dc/terms/>
        ... PREFIX foaf: <http://xmlns.com/foaf/0.1/>
        ... PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        ...
        ... SELECT DISTINCT ?uri ?rdf_type ?dcterms_title ?dcterms_publisher ?dcterms_identifier ?dcterms_issued
        ... FROM <http://example.org/ctx/base>
        ... WHERE {
        ...     ?uri rdf:type foaf:Document .
        ...     OPTIONAL { ?uri rdf:type ?rdf_type . }
        ...     OPTIONAL { ?uri dcterms:title ?dcterms_title . }
        ...     OPTIONAL { ?uri dcterms:publisher ?dcterms_publisher . }
        ...     OPTIONAL { ?uri dcterms:identifier ?dcterms_identifier . }
        ...     OPTIONAL { ?uri dcterms:issued ?dcterms_issued . }
        ...
        ... }\"""
        >>> d.facet_query("http://example.org/ctx/base") == expected
        True
        """
        g = self.make_graph()
        from_graph = "FROM <%s>" % context
        predicates = [f.rdftype for f in self.facets()]
        # FIXME: is it a good idea to let the bindings be affected by
        # a defined dimension_label? Particularly if the RDF.type
        # facet has a dimension_label, that means we can't rely on a
        # 'rdf_type' key always being present.
        bindings = [
            f.dimension_label if f.dimension_label else g.qname(
                f.rdftype).replace(
                ":",
                "_") for f in self.facets()]
        rdftypes = self.rdf_type
        # assume that self.rdf_type normally is a list/iterable
        if isinstance(rdftypes, URIRef):
            rdftypes = [rdftypes]
        else:
            rdftypes = list(rdftypes)
        namespaces = [
            ns for ns in self.ns.values() if [
                f for f in predicates +
                rdftypes if f.startswith(ns)]]
        if self.ns['rdf'] not in namespaces:
            namespaces.append(self.ns['rdf'])

        selectbindings = " ".join(["?" + b for b in bindings])
        # FIXME: the below whereclause is meant to select only
        # top-level documents (not documentparts), but does so by
        # requiring that all top-level documents should have rdf:type
        # == self.rdf_type which is inflexible.
        # whereclause = "?uri %s ?%s" % (g.qname(predicates[0]),
        #                                util.uri_leaf(predicates[0]))
        types = "(" + "|".join([g.qname(x) for x in rdftypes]) + ")"
        types = g.qname(rdftypes[0])
        if len(rdftypes) == 1:
            whereclause = "?uri rdf:type %s" % types
            filterclause = ""
        else:
            whereclause = "?uri rdf:type ?type"
            filterclause = "    FILTER (?type in (%s)) ." % ", ".join(
                [g.qname(x) for x in rdftypes])

        optclauses = "".join(
            ["    OPTIONAL { ?uri %s ?%s . }\n" % (g.qname(p), b) for p, b in zip(predicates, bindings)])[:-1]

        # FIXME: The above doctest looks like crap since all
        # registered namespaces in the repo is included. Should only
        # include prefixes actually used
        prefixes = "".join(["PREFIX %s: <%s>\n" % (p, u)
                            for p, u in sorted(self.ns.items()) if u in namespaces])

        query = """%(prefixes)s
SELECT DISTINCT ?uri %(selectbindings)s
%(from_graph)s
WHERE {
    %(whereclause)s .
%(optclauses)s
%(filterclause)s
}""" % locals()
        return query

    def facet_select(self, query):
        """Select all data from the triple store needed to create faceted data.

        :param context: The context (named graph) to restrict the query to.
                        If None, search entire triplestore.
        :type  context: str
        :returns: The results of the query, as python objects
        :rtype: set of dicts"""
        store = TripleStore.connect(self.config.storetype,
                                    self.config.storelocation,
                                    self.config.storerepository)
        res = store.select(query, "python")
        store.close()
        return res

    #
    #
    # STEP 4: Generate browser-ready HTML with navigation panels,
    # information about related documents and so on.
    #
    #

    @classmethod
    def generate_all_setup(cls, config, *args, **kwargs):
        """
        Runs any action needed prior to generating all documents in a
        docrepo. The default implementation does nothing.

        .. note::

           Like :py:meth:`~ferenda.DocumentRepository.parse_all_setup`
           this might change to a instance method.
        """

    @classmethod
    def generate_all_teardown(cls, config, *args, **kwargs):
        """
        Runs any cleanup action needed after generating all documents
        in a docrepo. The default implementation does nothing.

        .. note::

           Like :py:meth:`~ferenda.DocumentRepository.parse_all_setup`
           this might change to a instance method.
        """

    @decorators.action
    @decorators.ifneeded('generate')
    @decorators.updateentry('generate')
    def generate(self, basefile, version=None, otherrepos=[], needed=True):
        """Generate a browser-ready HTML file from structured XML and RDF.

        Uses the XML and RDF files constructed by
        :py:meth:`ferenda.DocumentRepository.parse`.

        The generation is done by XSLT, and normally you won't need to
        override this, but you might want to provide your own xslt
        file and set
        :py:data:`ferenda.DocumentRepository.xslt_template` to the
        name of that file.

        If you want to generate your browser-ready HTML by any other
        means than XSLT, you should override this method.

        :param basefile: The basefile for which to generate HTML
        :type  basefile: str
        :returns: None
        """
        with util.logtime(self.log.info, "generate OK (%(elapsed).3f sec)",
                          {'basefile': basefile}):

            self.log.debug("Starting")

            # All bookkeping done, now lets prepare and transform!

            infile = self.store.parsed_path(basefile, version)
            outfile = self.store.generated_path(basefile, version)
            # The annotationfile might be newer than all dependencies
            # (and thus not need regenerateion) even though the
            # outfile is older.
            if os.path.exists(self.store.dependencies_path(basefile)):
                deptxt = util.readfile(self.store.dependencies_path(basefile))
                dependencies = deptxt.strip().split("\n")
            else:
                dependencies = []
            if (self.config.force or (not
                                      util.outfile_is_newer(dependencies, self.store.annotation_path(basefile, version)))):
                with util.logtime(self.log.debug,
                                  "prep_annotation_file (%(elapsed).3f sec)",
                                  {'basefile': basefile}):
                    # annotation_file should be the same as annotations above?
                    annotation_file = self.prep_annotation_file(basefile, version)
            else:
                annotation_file = self.store.annotation_path(basefile, version)
            params = {}
            if annotation_file:
                params['annotationfile'] = annotation_file
            if version:
                params['version'] = version
            params = self.generate_set_params(basefile, version, params)

            with util.logtime(self.log.debug,
                              "transform (%(elapsed).3f sec)",
                              {'basefile': basefile}):
                conffile = os.path.abspath(
                    os.sep.join([self.config.datadir, 'rsrc', 'resources.xml']))
                if self.xslt_template.startswith("/"):
                    templatedir = "."
                elif "/" in self.xslt_template:
                    templatedir = self.xslt_template.rsplit("/", 1)[0]
                else:
                    templatedir = "."
                transformer = Transformer('XSLT', self.xslt_template, templatedir,
                                          resourceloader=self.resourceloader,
                                          config=conffile,
                                          documentroot=self.config.datadir)

                repos = list(otherrepos)
                if self not in repos:
                    repos.append(self)
                if self.config.staticsite:
                    depth = None
                else:
                    # since the URI for a document does not have to
                    # have any correspondance to where the underlying
                    # file for the document is situated, we must take
                    # the URI into account when relative paths are
                    # constructed with the depth argument to
                    # transform_file
                    depth = urlparse(self.canonical_uri(basefile, version)).path[1:-1].count("/")
                with RDFQuery.enable(self.config):
                    transformer.transform_file(infile, outfile, params, depth=depth)

            # At this point, outfile may appear untouched if it already
            # existed and wasn't actually changed. But this will cause the
            # above outfile_is_newer check to fail next time around. Also,
            # the docentry.updated parameter will be incosistent with the
            # timestamp on the file. What to do?
            os.utime(outfile, None)  # update access/modified timestamp
            now = datetime.now()
            docentry = DocumentEntry(self.store.documententry_path(basefile))
            if not docentry.published:
                docentry.published = now
            docentry.updated = now
            docentry.save()

    def generate_set_params(self, basefile, version, params):
        return params


    def get_url_transform_func(self, repos=None, basedir=None,
                               develurl=None, remove_missing=False, wsgiapp=None):
        """Returns a function that, when called with a URI, transforms that
        URI to another suitable reference. This can be used to eg. map
        between canonical URIs and local URIs. The function is run on
        all URIs in a post-processing step after
        :py:meth:`~ferenda.DocumentRepository.generate` runs. The
        default implementatation maps URIs to local file paths, and is
        only run if ``config.staticsite``is ``True``.

        """
        def getpath(url, repos):
            if url == self.config.url:
                return self.config.datadir + os.sep + "index.html"
            # http://example.org/foo/bar.x -> |/foo/bar.x (for Rule.match)
            matchurl = "|/"+url.split("/", 3)[-1].split("?")[0]
            if "/" not in url:
                # this is definitly not a HTTP(S) url, might be a
                # mailto:? Anyway, we won't get a usable path from it
                # so don't bother.
                return None
            for (repoidx, repo) in enumerate(repos):
                supports = False
                for rule in wsgiapp.reporules[repo]:
                    if rule.match(matchurl) is not None:
                        rule.match(matchurl)
                        supports = True
                        break
                if supports:
                    if url.endswith(".png"):
                        # FIXME: This is slightly hacky as it returns
                        # the path to the generated HTML file, not the
                        # image, but calling path() will either
                        # return a filepath that doesn't exist yet
                        # (the facsimile image won't have been
                        # created) leading to a invalid-link class, or
                        # it will create the facsimile image before
                        # returning the path to it (which would be
                        # very bad).
                        #
                        # shouldn't this be repo.store.generated_path ??
                        return self.store.generated_path(self.basefile_from_uri(url))
                    else:
                        return repo.requesthandler.path(url)
        
        def simple_transform(url):
            if url.startswith(self.config.url):
                if base_transform(url) is False:
                    return False
                # convert eg.
                # "https://lagen.nu/dom/md/2014:2?repo=dv&attachment=1.pdf"
                # to just "/dom/md/2014:2?repo=dv&attachment=1.pdf"
                return url[len(self.config.url)-1:]
            else:
                return url

        def static_transform(url):
            path = None
            if url == self.config.url:
                path = self.config.datadir + os.sep + "index.html"
                # path = basedir + os.sep + "index.html"
            elif url.startswith("#"):
                return url
            else:
                path = getpath(url, repos)
            if path:
                if os.path.exists(path) or not remove_missing:
                    relpath = os.path.relpath(path, basedir)
                    if os.sep == "\\":
                        relpath = relpath.replace(os.sep, "/")
                    return relpath
                else:
                    # this is an implicit self.config.removemissing
                    return False
            else:
                return url

        def base_transform(url):
            if remove_missing:
                path = getpath(url, repos)
                if path and not (os.path.exists(path) and os.path.getsize(path) > 0):
                    return False
            return url

        if repos is None:
            repos = []
        if wsgiapp is None: 
            from ferenda.manager import make_wsgi_app
            wsgiapp = make_wsgi_app(self.config._parent, repos=repos)
        # sort repolist so that CompositeRepository instances come
        # before others (see comment in getpath)
        from ferenda import CompositeRepository
        repos = sorted(repos, key=lambda x: isinstance(x, CompositeRepository), reverse=True)
        if develurl:
            return simple_transform
        elif basedir:
            return static_transform
        else:
            return base_transform
        
        

    def prep_annotation_file(self, basefile, version):
        """Helper function used by
        :py:meth:`~ferenda.DocumentRepository.generate` -- prepares a
        RDF/XML file containing statements that in some way annotates
        the information found in the document that generate handles,
        like URI/title of other documents that refers to this one.

        :param basefile: The basefile for which to collect annotating
                         statements.
        :type basefile: str
        :returns: The full path to the prepared RDF/XML file
        :rtype: str

        """
        # return self.store.annotation_path(basefile)
        if not self.sparql_annotations:
            return
        graph = self.construct_annotations(self.canonical_uri(basefile))
        if graph and len(graph) > 0:
            with self.store.open_annotation(basefile, "w", version) as fp:
                fp.write(self.graph_to_annotation_file(graph))
            return self.store.annotation_path(basefile, version)
        elif self.sparql_expect_results:
            self.log.warning(
                "No annotation data fetched, something might be wrong with the SPARQL query")

    def construct_annotations(self, uri):
        """Construct a RDF graph containing metadata by running the query
        provided by
        :meth:`~ferenda.DocumentRepository.construct_sparql_query`

        """
        sq = self.construct_sparql_query(uri)
        if self.config.storelocation:
            kwargs = {}
            if self.config.storetype in ("SQLITE", "SLEEPYCAT"):
                kwargs['inmemory'] = True
            ts = self._get_triplestore(**kwargs)
            res = ts.construct(sq)
            # bind namespaces so that the constructed graph looks pretty
            for prefix, uri in list(self.ns.items()):
                res.bind(prefix, uri)

            return res

    def construct_sparql_query(self, uri):
        """Construct a SPARQL query that will select metadata relating to
        *uri* in some way, using the query template specified by
        :data:`~ferenda.DocumentRepository.sparql_annotations`

        """
        query_template = self.sparql_annotations
        with self.resourceloader.open(query_template) as fp:
            params = {'uri': uri}
            sq = fp.read() % params
        return sq

    # helper for the prep_annotation_file helper -- it expects a
    # RDFLib graph, and returns a XML string in Grit format
    def graph_to_annotation_file(self, graph):
        """Converts a RDFLib graph into a XML file with the same
        statements, ordered using the Grit format
        (https://code.google.com/p/oort/wiki/Grit) for easier XSLT
        inclusion.

        :param graph: The graph to convert
        :type  graph: rdflib.graph.Graph
        :returns: A serialized XML document with the RDF statements
        :rtype: str
        """
        fp = BytesIO(graph.serialize(format="xml"))
        intree = etree.parse(fp)
        with self.resourceloader.open("xsl/rdfxml-grit.xsl") as fp:
            transform = etree.XSLT(etree.parse(fp))
        resulttree = transform(intree)
        res = etree.tostring(resulttree, pretty_print=format)
        return res.decode('utf-8')

    # the inverse of graph_to_annotation_file
    def annotation_file_to_graph(self, annotation_file):
        """Converts a annotation file (using the Grit format) back into an
        RDFLib graph.

        :param graph: The filename of a serialized XML document with RDF statements
        :type  graph: str
        :returns: The RDF statements as a regular graph
        :rtype: rdflib.Graph

        """
        with open(annotation_file, "rb") as fp:
            intree = etree.parse(fp)
        with self.resourceloader.open("xsl/grit-grddl.xsl") as fp:
            transform = etree.XSLT(etree.parse(fp))
        resulttree = transform(intree)
        res = etree.tostring(resulttree, pretty_print=format)
        g = Graph()
        g.parse(data=res)
        return g

    def generated_url(self, basefile):
        """Get the full local url for the generated file for the
        given basefile.

        :param basefile: The basefile for which to calculate the local url
        :type  basefile: str
        :returns: The local url
        :rtype: str
        """
        return self.generic_url(basefile, 'generated', '.html')

    #
    #
    # STEP 4.5: After generating HTML, go through all links and
    # rewrite/transform them (we cannot do that as part of generate(),
    # since the transform may depend on whether other generated files
    # exist on disk, to know whether keep links to them or not)
    @decorators.action
    @decorators.ifneeded('transformlinks')
    def transformlinks(self, basefile, version=None, otherrepos=[]):
        """Transform links in generated HTML files.

        If the ``develurl`` or ``staticsite`` settings are used, this
        function makes sure links are transformed to appropriate local
        links.

        """
        # FIXME: Do we need any way of preventing double-transforming?
        # How should we detect that a file already has been
        # transformed? Right now documentstore.needed() detects that
        # by comparing the generated file timestamp with info from the
        # documententry file
        repos = list(otherrepos)
        generatedfile = self.store.generated_path(basefile, version=version)
        if self not in repos:
            repos.append(self)
        transformargs = {'repos': repos,
                         'remove_missing': self.config.removeinvalidlinks}
        if self.config.staticsite:
            transformargs['basedir'] = os.path.dirname(generatedfile)
        elif getattr(self.config, 'develurl', None):
            transformargs['develurl'] = self.config.develurl
        elif self.config.removeinvalidlinks is False:
            return None
        
        with util.logtime(self.log.info, "transformlinks OK (%(elapsed).3f sec)"):
            urltransform = self.get_url_transform_func(**transformargs)
            doc = etree.parse(generatedfile)
            doctype = doc.docinfo.doctype
            tree = doc.getroot()
            conffile = os.path.abspath(
                os.sep.join([self.config.datadir, 'rsrc', 'resources.xml']))
            # NOTE: most arguments to the constructor might never be needed
            transformer = Transformer('XSLT', None, None)
            transformer.transform_links(tree, urltransform)
            transformer.t.native_to_file(tree, generatedfile, doctype=doctype)
        return True
    #
    #
    # STEP 5: Generate HTML pages for a TOC of a all documents, news
    # pages of new/updated documents, and other odds'n ends.
    #

    @decorators.action
    def toc(self, otherrepos=[]):
        """Creates a set of pages that together acts as a table of contents
        for all documents in the repository. For smaller repositories
        a single page might be enough, but for repositoriees with a
        few hundred documents or more, there will usually be one page
        for all documents starting with A, starting with B, and so
        on. There might be different ways of browseing/drilling down,
        i.e. both by title, publication year, keyword and so on.

        The default implementation calls
        :py:meth:`~ferenda.DocumentRepository.faceted_data` to get all
        data from the triple store,
        :py:meth:`~ferenda.DocumentRepository.facets` to find
        out the facets for ordering,
        :py:meth:`~ferenda.DocumentRepository.toc_pagesets` to
        calculate the total set of TOC html files,
        :py:meth:`~ferenda.DocumentRepository.toc_select_for_pages` to
        create a list of documents for each TOC html file, and finally
        :py:meth:`~ferenda.DocumentRepository.toc_generate_pages` to
        create the HTML files. The default implemention assumes that
        documents have a title (in the form of a ``dcterms:title``
        property) and a publication date (in the form of a
        ``dcterms:issued`` property).

        You can override any of these methods to customize any part of
        the toc generation process. Often overriding
        :py:meth:`~ferenda.DocumentRepository.facets` to specify other
        document properties will be sufficient.

        """
        if not self.config.tabs:
            self.log.info("%s: Not creating TOC (config has tabs=False)" % self.alias)
            return
        tocindex = self.store.resourcepath("toc/index.html")
        faceted_data = self.store.resourcepath("toc/faceted_data.json")
        if (not self.config.force) and util.outfile_is_newer([faceted_data], tocindex):
            self.log.debug("Not regenerating TOCs")
            return

        params = {}
        with util.logtime(self.log.debug,
                          "toc: selected %(rowcount)s rows (%(elapsed).3f sec)",
                          params):
            data = self.faceted_data()
            params['rowcount'] = len(data)
        if len(data) > 0:
            facets = self.facets()
            pagesets = self.toc_pagesets(data, facets)
            pagecontent = self.toc_select_for_pages(data, pagesets, facets)
            self.toc_generate_pages(pagecontent, pagesets, otherrepos=otherrepos)
            self.toc_generate_first_page(pagecontent, pagesets, otherrepos=otherrepos)
        else:
            self.log.error("faceted_data found 0 results for query, can't generate TOC")
            self.log.info("(query PROBABLY was '%s')" %
                          self.facet_query(self.dataset_uri()))

    def toc_pagesets(self, data, facets):
        """Calculate the set of needed TOC pages based on the result rows

        :param data: list of dicts, each dict containing metadata about
                     a single document
        :param facets: list of Facet objects
        :returns: A set of Pageset objects
        :rtype: list

        Example:

        >>> d = DocumentRepository()
        >>> from rdflib.namespace import DCTERMS
        >>> rows = [{'uri':'http://ex.org/1','dcterms_title':'Abc','dcterms_issued':'2009-04-02'},
        ...         {'uri':'http://ex.org/2','dcterms_title':'Abcd','dcterms_issued':'2010-06-30'},
        ...         {'uri':'http://ex.org/3','dcterms_title':'Dfg','dcterms_issued':'2010-08-01'}]
        >>> from rdflib.namespace import DCTERMS
        >>> facets = [Facet(DCTERMS.title), Facet(DCTERMS.issued)]
        >>> pagesets=d.toc_pagesets(rows,facets)
        >>> pagesets[0].label
        'Sorted by title'
        >>> pagesets[0].pages[0]
        <TocPage binding=dcterms_title linktext=a title=Documents starting with "a" value=a>
        >>> pagesets[0].pages[0].linktext
        'a'
        >>> pagesets[0].pages[0].title
        'Documents starting with "a"'
        >>> pagesets[0].pages[0].binding
        'dcterms_title'
        >>> pagesets[0].pages[0].value
        'a'
        >>> pagesets[1].label
        'Sorted by publication year'
        >>> pagesets[1].pages[0]
        <TocPage binding=dcterms_issued linktext=2009 title=Documents published in 2009 value=2009>
        """

        qname_graph = self.make_graph()
        res = []
        for facet in facets:
            if not facet.use_for_toc:
                continue
            selector_values = {}
            selector_fragments = {}
            selector = facet.selector

            if facet.dimension_label:
                binding = facet.dimension_label
                term = facet.dimension_label
            else:
                binding = qname_graph.qname(facet.rdftype).replace(":", "_")
                term = util.uri_leaf(facet.rdftype)

            pageset = TocPageset(label=facet.label % {'term': term},
                                 predicate=facet.rdftype,
                                 pages=[])

            for row in data:
                try:
                    selected = selector(row, binding, self.commondata)
                    selector_values[selected] = True
                    selector_fragments[selected] = facet.identificator(
                        row,
                        binding,
                        self.commondata)
                except KeyError:  # as e:
                    # this will happen a lot on simple selector
                    # functions when handed incomplete data
                    pass
            with util.switch_locale(self.collate_locale, locale.LC_COLLATE):
                for value in sorted(
                        list(selector_values.keys()), reverse=facet.selector_descending, key=locale.strxfrm):
                    urlfragment = selector_fragments[value]
                    pageset.pages.append(TocPage(linktext=value,
                                                 title=facet.pagetitle % {'term': term,
                                                                          'selected': value},
                                                 binding=binding,
                                                 value=urlfragment))
            res.append(pageset)
        return res

    def toc_select_for_pages(self, data, pagesets, facets):
        """Go through all data rows (each row representing a document)
        and, for each toc page, select those documents that are to
        appear in a particular page.

        Example:

        >>> d = DocumentRepository()
        >>> rows = [{'uri':'http://ex.org/1','dcterms_title':'Abc','dcterms_issued':'2009-04-02'},
        ...         {'uri':'http://ex.org/2','dcterms_title':'Abcd','dcterms_issued':'2010-06-30'},
        ...         {'uri':'http://ex.org/3','dcterms_title':'Dfg','dcterms_issued':'2010-08-01'}]
        >>> from rdflib.namespace import DCTERMS
        >>> facets = [Facet(DCTERMS.title), Facet(DCTERMS.issued)]
        >>> pagesets=d.toc_pagesets(rows,facets)
        >>> expected={('dcterms_title','a'):[[Link('Abc',uri='http://ex.org/1')],
        ...                                  [Link('Abcd',uri='http://ex.org/2')]],
        ...           ('dcterms_title','d'):[[Link('Dfg',uri='http://ex.org/3')]],
        ...           ('dcterms_issued','2009'):[[Link('Abc',uri='http://ex.org/1')]],
        ...           ('dcterms_issued','2010'):[[Link('Abcd',uri='http://ex.org/2')],
        ...                                      [Link('Dfg',uri='http://ex.org/3')]]}
        >>> d.toc_select_for_pages(rows, pagesets, facets) == expected
        True

        :param data: List of dicts as returned by :meth:`~ferenda.DocumentRepository.toc_select`
        :param pagesets: Result from :meth:`~ferenda.DocumentRepository.toc_pagesets`
        :param facets: Result from :meth:`~ferenda.DocumentRepository.facets`
        :returns: mapping between toc basefile and documentlist for that basefile
        :rtype: dict
        """

        # to 1-dimensional dict (odict?): {(binding,value): [list-of-Elements]}
        res = {}
        qname_graph = self.make_graph()
        facets = [f for f in facets if f.use_for_toc]
        for pageset, facet in zip(pagesets, facets):
            documents = defaultdict(list)
            if facet.dimension_label:
                binding = facet.dimension_label
            else:
                binding = qname_graph.qname(facet.rdftype).replace(":", "_")

            for row in data:
                try:
                    key = facet.selector(row, binding, self.commondata)
                    documents[key].append(row)
                except KeyError:
                    pass
            for key in documents.keys():
                # find appropriate page in pageset and read it's basefile
                for page in pageset.pages:
                    if page.linktext == key:
                        keyfunc = functools.partial(facet.key,
                                                    binding=binding,
                                                    resource_graph=self.commondata)
                        s = sorted(documents[key],
                                   key=keyfunc,
                                   reverse=facet.key_descending)
                        res[(page.binding, page.value)] = [self.toc_item(binding, row)
                                                           for row in s]
        return res

    def toc_item(self, binding, row):
        """Returns a formatted version of row, using Element objects"""
        # default impl always just a simple link with title as link text
        return [Link(row['dcterms_title'],  # yes, ignore binding
                     uri=row['uri'])]

    # pagecontent -> documentlists?
    def toc_generate_pages(self, pagecontent, pagesets, otherrepos=[]):
        """Creates a set of TOC pages by calling
         :meth:`~ferenda.DocumentRepository.toc_generate_page`.

        :param pagecontent: Result from
                            :meth:`~ferenda.DocumentRepository.toc_select_for_pages`
        :param pagesets: Result from
                         :meth:`~ferenda.DocumentRepository.toc_pagesets`
        :param otherrepos: A list of document repository instances

        """
        paths = []
        for (binding, value), documents in sorted(pagecontent.items()):
            paths.append(self.toc_generate_page(
                binding, value, documents, pagesets, effective_basefile=None, otherrepos=otherrepos))
        return paths

    def toc_generate_first_page(self, pagecontent, pagesets, otherrepos=[]):
        """Generate the main page of TOC pages."""
        firstpage = pagesets[0].pages[0]  # has .binding and .value
        documents = pagecontent[(firstpage.binding, firstpage.value)]
        return self.toc_generate_page(firstpage.binding, firstpage.value,
                                      documents, pagesets, effective_basefile="index", otherrepos=otherrepos)

    def toc_generate_page(self, binding, value, documentlist, pagesets,
                          effective_basefile=None, title=None, otherrepos=[]):
        """Generate a single TOC page.

        :param binding: The binding used (eg. 'title' or 'issued')
        :param value: The value for the used binding (eg. 'a' or '2013'
        :param documentlist: Result from
                       :meth:`~ferenda.DocumentRepository.toc_select_for_pages`
        :param pagesets: Result from
                       :meth:`~ferenda.DocumentRepository.toc_pagesets`
        :param effective_basefile: Place the resulting page somewhere else
                                   than ``toc/*binding*/*value*.html``
        :param otherrepos: A list of document repository instances
        """
        if effective_basefile is None:
            effective_basefile = binding + "/" + value
        outfile = self.store.resourcepath("toc/%s.html" % effective_basefile)
        doc = self.make_document()
        doc.uri = self.dataset_uri(binding, value)
        d = Describer(doc.meta, doc.uri)
        nav = UnorderedList(role='navigation')
        for pageset in pagesets:
            sublist = UnorderedList()
            for page in pageset.pages:
                if page.binding == binding and page.value == value:
                    title = page.title
                    sublist.append(ListItem([page.linktext]))
                else:
                    href = self.dataset_uri(page.binding, page.value)
                    sublist.append(ListItem([Link(str(page.linktext), uri=href)]))
            nav.append(ListItem([Paragraph([pageset.label]), sublist]))

        d.value(self.ns['dcterms'].title, title)

        # Override toc_generate_page_body to implement other
        # presentation strategies; definition lists with subheadings,
        # orderedlists, tables...
        
        doc.body = self.toc_generate_page_body(documentlist, nav)
        
        conffile = os.path.abspath(
            os.sep.join([self.config.datadir, 'rsrc', 'resources.xml']))
        transformer = Transformer('XSLT', "xsl/toc.xsl", "xsl",
                                  resourceloader=self.resourceloader,
                                  config=conffile)
        # FIXME: This is a naive way of calculating the relative depth
        # of the outfile.

        # FIXME: 2: transformer.transform_file should be able to
        # handle this

        depth = len(outfile[len(self.store.datadir) + 1:].split(os.sep))
        repos = [self] + otherrepos
        transformargs = {'repos': repos,
                         'remove_missing': False}  # never remove links
        if self.config.staticsite:
            transformargs['basedir'] = os.path.dirname(outfile)
        elif 'develurl' in self.config:
            transformargs['develurl'] = self.config.develurl

        urltransform = self.get_url_transform_func(**transformargs)
        tree = transformer.transform(
            self.render_xhtml_tree(doc),
            depth,
            uritransform=urltransform)

        # fixed = transformer.t.html5_doctype_workaround(etree.tostring(tree, pretty_print=True, encoding="utf-8"))
        fixed = etree.tostring(tree, pretty_print=True, encoding="utf-8")

        # with self.store.open(effective_basefile, 'toc', '.html', "wb") as fp:
        util.ensure_dir(outfile)
        with open(outfile, "wb") as fp:
            fp.write(fixed)

        self.log.info("Created %s" % outfile)
        return outfile

    def toc_generate_page_body(self, documentlist, nav):
        ul = UnorderedList([ListItem(x) for x in documentlist], role='main')
        return Body([nav,
                     ul
        ])


    news_sortkey = 'updated'
    @decorators.action
    def news(self, otherrepos=[]):
        """Create a set of Atom feeds and corresponding HTML pages for
        new/updated documents in different categories in the
        repository.

        """
        feedindex = self.store.resourcepath("news/main.atom")
        faceted_data = self.store.resourcepath("toc/faceted_data.json")
        if (not self.config.force) and util.outfile_is_newer([faceted_data], feedindex):
            self.log.debug("Not regenerating feeds")
            return

        params = {}
        # news_facet_entries employs caching
        with util.logtime(self.log.debug,
                          "news: selected %(rowcount)s decorated rows (%(elapsed).3f sec)",
                          params):
            keyfunc = itemgetter(self.news_sortkey)
            data = self.news_facet_entries(keyfunc)
            params['rowcount'] = len(data)

        # create an object for each Atom feed. This should include a
        # "main" feed that will contain all (published) entries in the
        # docrepo
        facets = self.facets()
        feedsets = self.news_feedsets(data, facets)

        # fill each such feed with relevant entries according to selectors
        feeds = self.news_select_for_feeds(data, feedsets, facets)

        # generate them feeds
        self.news_generate_feeds(feeds)

    def news_facet_entries(self, keyfunc=None, reverse=True):
        """Returns a set of entries, decorated with information from
        :py:meth:`~ferenda.DocumentRepository.faceted_data`, used for
        feed generation.

        :param keyfunc: Function that given a dict, returns an element
                        from that dict, used for sorting entries.
        :type keyfunc: callable
        :param reverse: The direction of the sorting
        :type reverse:
        :returns: entries, each represented as a dict
        :rtype: list
        """

        if keyfunc is None:
            keyfunc = itemgetter('updated')
        cachepath = self.store.resourcepath("feed/faceted_entries.json")

        # create an iterable of all the dependencies. If any of these
        # is newer than outfile (cachepath) the outfile_is_newer
        # immediately returns false.
        dependencies = chain(
            [self.store.resourcepath("feed/faceted_entries.json")],
            util.list_dirs(self.store.resourcepath("entries"), ".json")
        )
        if ((not self.config.force) and
                os.path.exists(cachepath) and
                util.outfile_is_newer(dependencies, cachepath)):

            self.log.debug("Loading faceted_entries from %s" % cachepath)
            # FIXME: Individual repos must be responsible for which
            # fields (apart from published/updated) that might contain
            # dates/datetimes
            datehook = util.make_json_date_object_hook('published', 'updated', 'dcterms_issued', 'rpubl_avgorandedatum', 'orig_created', 'orig_updated')
            ret = json.load(open(cachepath),
                            object_hook=datehook)
        else:
            data = self.faceted_data()
            # transform list of dicts into a dict with the uri field as
            # key and teh entire dict as value, for fast lookup in the next step
            datadict = dict([(x['uri'], x) for x in data])

            ret = []
            # decorate datadict with entries
            for entry in self.news_entries():
                # let's just hope that there always is one?
                if entry.id not in datadict:
                    self.log.warning("%s does not occur in faceted_data, "
                                     "mismatch between data in docentry files "
                                     "and data in triplestore" % entry.id)
                    continue   # ie skip this, since we can't decorate
                               # the row we skip it altogether

                d = datadict[entry.id]
                # or maybe we should just stash the DocumentEntry object in the
                # correct row of the faceted data? like:
                # d['entry'] = entry
                #
                # note in particular that the row/dict will have both a
                # uri and a url field (where the latter should be the URL
                # where the browser-ready file is published wich may or
                # may not be identical to the canonical URI of the
                # document).
                #
                # also, orig_updated (the date when the source doc was
                # last updated) might be more interesting than updated
                # (the last time anything happended with the entry)
                for prop in ('updated', 'published', 'basefile', 'title',
                             'summary', 'content', 'link', 'url', 'orig_updated', 'orig_created'):
                    d[prop] = getattr(entry, prop)
                ret.append(d)
            # is there any point to sorting at this time, as
            # news_select_for_feeds will sort the entries for each
            # feed (and that will have access to entries after
            # news_item have processed each, possibly recreating
            # missing values needed for sorting...)
            # ret = sorted(ret, key=keyfunc, reverse=reverse)
            util.ensure_dir(cachepath)
            with open(cachepath, "w") as fp:
                self.log.debug("Saving faceted_entries to %s" % cachepath)
                s = json.dumps(ret, indent=4, separators=(', ', ': '),
                               default=util.json_default_date)
                fp.write(s)
        return ret


    news_feedsets_main_label = "All documents"
    
    def news_feedsets(self, data, facets):
        """Calculate the set of needed feedsets based on facets and instance
        values in the data

        :param data: list of dicts, each dict containing metadata about
                     a single document
        :param facets: list of Facet objects
        :returns: A list of Feedset objects

        """
        cachepath = self.store.resourcepath("feed/feedsets.pickle")
        dependencies = chain([self.store.resourcepath("feed/faceted_entries.json")],
                             util.list_dirs(self.store.resourcepath("feed"), ".atom"))
        if ((not self.config.force) and
                os.path.exists(cachepath) and
                util.outfile_is_newer(dependencies, cachepath)):
            self.log.debug("loading pickled feedsets from %s" % cachepath)
            with open(cachepath, "rb") as fp:
                return pickle.load(fp)
        else:
            qname_graph = self.make_graph()
            res = []
            for facet in facets:
                if not facet.use_for_feed:
                    continue
                selector_values = {}
                selector_fragments = {}
                selector = facet.selector
                if facet.dimension_label:
                    binding = facet.dimension_label
                    term = facet.dimension_label
                else:
                    binding = qname_graph.qname(facet.rdftype).replace(":", "_")
                    term = util.uri_leaf(facet.rdftype)

                feedset = Feedset(label=facet.label % {'term': term},
                                  feeds=[],
                                  predicate=facet.rdftype)

                for row in data:
                    try:
                        selected = facet.selector(row, binding, self.commondata)
                        selector_values[selected] = True
                        selector_fragments[selected] = facet.identificator(
                            row,
                            binding,
                            self.commondata)
                    except KeyError:  # as e:
                        # this will happen a lot on simple selector
                        # functions when handed incomplete data
                        pass

                for value in sorted(
                        list(selector_values.keys()), reverse=facet.selector_descending):
                    urlfragment = selector_fragments[value]
                    slug = term + "/" + urlfragment.lower()
                    title = facet.pagetitle % {'term': term,
                                               'selected': value}
                    feedset.feeds.append(Feed(slug=slug,
                                              title=title,
                                              binding=binding,
                                              value=urlfragment))
                res.append(feedset)

            # finally add the built-in All feedset, which has only one feed.
            res.append(Feedset(label="All",
                               feeds=[Feed(slug="main",
                                           title=self.news_feedsets_main_label,
                                           binding=None,
                                           value=None)]))
            # and then, cache the results (can't use json for this, but pickle is acceptable
            util.ensure_dir(cachepath)
            with open(cachepath, "wb") as fp:
                pickle.dump(res, fp, pickle.HIGHEST_PROTOCOL)
        return res

    def news_entrysort_key(self):
        """Return a function that can act as a keyfunc in a sorted() call to
        sort your entries in whatever way suitable. The keyfunc takes
        three values (row, binding, resource_graph).

        Only really used for the main feedset? The other feedsets,
        based on facets, use that facets keyfunc.

        """
        return lambda row, binding, resource_graph: row[self.news_sortkey]        

    def news_select_for_feeds(self, data, feedsets, facets):
        """Go through all data rows (each row representing a document)
        and, for each newsfeed, select those document entries that are to
        appear in that feed

        :param data: List of dicts as returned by
                     :meth:`~ferenda.DocumentRepository.news_facet_entries`
        :param feedsets: List of feedset objects, the result from
                         :meth:`~ferenda.DocumentRepository.news_feedsets`
        :param facets: Result from :meth:`~ferenda.DocumentRepository.facets`
        :returns: mapping between a (binding, value) tuple and entries for
                  that tuple!
        """

        res = {}
        qname_graph = self.make_graph()
        facets = [f for f in facets if f.use_for_feed]
        if len(facets) < len(feedsets):
            # note: the last feedset will contain all published
            # documents in the repo. If there is no corresponding
            # facet, we have to fake one that accepts all and sorts
            # everything in the same bucket.
            facets.append(Facet(rdftype=RDFS.Resource,  # all the things
                                identificator=lambda x, y, z: None,
                                selector=lambda x, y, z: None,
                                key=self.news_entrysort_key(),
                                key_descending=True))
        for feedset, facet in zip(feedsets, facets):
            documents = defaultdict(list)
            if facet.dimension_label:
                binding = facet.dimension_label
            else:
                binding = qname_graph.qname(facet.rdftype).replace(":", "_")

            for row in data:
                try:
                    key = facet.identificator(row, binding, self.commondata)
                    documents[key].append(row)
                except KeyError:
                    pass
            for key in documents.keys():
                # find appropriate feed in feedset and read it's basefile
                for feed in feedset.feeds:
                    if feed.value == key:
                        keyfunc = functools.partial(facet.key,
                                                    binding=binding,
                                                    resource_graph=self.commondata)
                        # format each entry first
                        entries = [self.news_item(binding, entry)
                                   for entry in documents[key]]

                        # then sort them later (so that formatting may affect sort critera)
                        feed.entries = sorted(entries,
                                              key=keyfunc,
                                              reverse=facet.key_descending)
        return feedsets

    # it's possible this should be a property on a Facet object like
    # selector and indentificator are, but fow now this is congruent
    # with toc_item
    def news_item(self, binding, entry):
        """Returns a modified version of the news entry for use in a specific
        feed.

        You can override this if you eg. want to customize title or
        summary of each entry in a particular feed. The default
        implementation does not change the entry in any way.

        :param binding: identifier for the feed being constructed, derived
                        from a facet object.
        :type binding: str
        :param entry:  The entry object to modify
        :type entry: ferenda.DocumentEntry
        :returns: The modified entry
        :rtype: ferenda.DocumentEntry

        """

        # the default impl doesn't change a thing, but other impls
        # might fiddle with title and summary
        return entry

    def news_entries(self):
        """Return a generator of all available (and published) DocumentEntry
        objects.

        """
        from ferenda import CompositeRepository
        directory = os.path.sep.join((self.config.datadir, self.alias, "entries"))
        for basefile in self.store.list_basefiles_for("news"):
            path = self.store.documententry_path(basefile)
            try:
                entry = DocumentEntry(path)
            except Exception as e:
                self.log.warning("%s: Couldn't load entry: %s" % (basefile, e))
                continue
            dirty = False
            if not entry.published:
                # not published -> shouldn't be in feed
                continue
            if entry.status.get('parse', {}).get('success') == "removed":
                # document has been removed -> shouldn't be in
                # feed. FIXME: a lot of composite repos have not
                # updated this field even though they should have
                continue
            if not os.path.exists(self.store.distilled_path(basefile)):

                if (not isinstance(self, CompositeRepository) and
                    not (os.path.exists(self.store.downloaded_path(basefile)) or
                         os.path.exists(self.store.intermediate_path(basefile)))):
                    self.log.warning("%s: Entry file for %s probably stale" % (self.store.documententry_path(basefile), basefile))
                else:
                    self.log.warning("%s: No distilled file at %s, skipping" %
                                     (basefile,
                                      self.store.distilled_path(basefile)))
                continue
            # make sure common (and needed) properties are in fact set
            if not entry.id or ('forceid' in self.config and
                                self.config.forceid):
                entry.id = self.canonical_uri(basefile)
                dirty = True
            if not entry.url:
                entry.url = self.generated_url(basefile)
                dirty = True
            if not entry.basefile:
                entry.basefile = basefile
                dirty = True
            if not entry.title:
                entry.title = entry.id
                dirty = True

            # Set links to RDF metadata and document content
            if not entry.link:
                entry.set_link(self.store.distilled_path(basefile),
                               self.distilled_url(basefile))
                dirty = True

            # If we just republish eg. the original PDF file and don't
            # attempt to parse/enrich the document
            if not entry.content:
                if (self.config.republishsource):
                    entry.set_content(self.store.downloaded_path(basefile),
                                      self.downloaded_url(basefile))
                else:
                    # the parsed (machine reprocessable) version. The
                    # browser-ready version is referenced with the <link>
                    # element, separate from the set_link <link>
                    entry.set_content(self.store.parsed_path(basefile),
                                      self.parsed_url(basefile))
                dirty = True
            if dirty:
                entry.save()
            yield entry

    def news_generate_feeds(self, feedsets, generate_html=True):
        """Creates a set of Atom feeds (and optionally HTML equivalents) by
        calling :py:meth:`~ferenda.DocumentRepository.news_write_atom`
        for each feed in feedsets.

        :param feedsets: the result of :py:meth:`~ferenda.DocumentRepository.news_feedsets`
        :type feedsets: list
        :param generate_html: Whether to generate HTML equivalents of
                              the atom feeds
        :type generate_html: bool
        """

        if generate_html:
            conffile = os.path.abspath(
                os.sep.join([self.config.datadir, 'rsrc', 'resources.xml']))
            transformer = Transformer("XSLT", "xsl/atom.xsl", "xsl",
                                      resourceloader=self.resourceloader,
                                      documentroot=self.config.datadir,
                                      config=conffile)
            repos = [self]  # FIXME: we must make otherrespos (passed
                            #  to news()) available to this scope
            transformargs = {'repos': repos,
                             'remove_missing': False}  # never remove links
            if self.config.staticsite:
                transformargs['basedir'] = os.path.dirname(outfile)
            elif 'develurl' in self.config:
                transformargs['develurl'] = self.config.develurl
            urltransform = self.get_url_transform_func(**transformargs)

        for feedset in feedsets:
            for feed in feedset.feeds:
                # should reverse=True be configurable? For datetime
                # properties it makes sense to use most recent first, but
                # maybe other cases?
                self.log.info("feed %s: %s entries" % (feed.slug, len(feed.entries)))
                self.news_write_atom(feed.entries,
                                     feed.title,
                                     feed.slug)
                if generate_html:
                    # NB: infile must be initialized using the same
                    # method as is used to initialize feedfile in
                    # news_write_atom/write_file. Right now
                    # resourcepath is preferrable as it DOESN'T run
                    # its argument through basefile_to_pathfrag (since
                    # feed.slug isn't really a basefile)
                    infile = self.store.resourcepath("feed/%s.atom" % feed.slug)
                    # infile = self.store.atom_path(feed.slug)
                    outfile = self.store.resourcepath('feed/%s.html' % feed.slug)
                    transformer.transform_file(infile, outfile,
                                               uritransform=urltransform)

    def news_write_atom(self, entries, title, slug, archivesize=100):
        """Given a list of Atom entry-like objects, including links to RDF
        and PDF files (if applicable), create a rinfo-compatible Atom feed,
        optionally splitting into archives.

        :param entries: :py:class:`~ferenda.DocumentEntry` objects
        :type  entries: list
        :param title: feed title
        :type  title: str
        :param slug: used for constructing the path where the Atom files are
                     stored and the URL where it's published.
        :type  slug: str
        :param archivesize: The amount of entries in each archive
                            file. The main file might contain up to 2
                            x this amount.
        :type archivesize: int

        """

        # This nested func does most of heavy lifting, the main
        # function code only sets up basic constants and splits the
        # entries list into appropriate chunks
        def write_file(entries, suffix="", prevarchive=None, nextarchive=None):
            feedfile = self.store.resourcepath("feed/%s%s.atom" % (slug, suffix))
            nsmap = {None: 'http://www.w3.org/2005/Atom',
                     'le': 'http://purl.org/atompub/link-extensions/1.0'}
            E = ElementMaker(nsmap=nsmap)

            # entries SHOULD at this point be a list of DocumentEntry
            # object, not (DocumentEntry, Graph).
            if entries:
                # entries should now not be DocumentEntries but rather
                # dicts containing the same information
                assert isinstance(entries[0], dict)
                updated = max(entries, key=itemgetter('updated'))['updated']
            else:
                updated = datetime.now()  # or never
            contents = [E.id(feedid),
                        E.title(title),
                        E.updated(util.rfc_3339_timestamp(updated)),
                        E.author(
                            E.name("Ferenda"),
                            E.email("info@example.org"),
                            E.uri(self.config.url)
            ),
                E.link({'rel': 'self', 'href': feedurl})]
            if prevarchive:
                contents.append(E.link({'rel': 'prev-archive',
                                        'href': prevarchive}))
            if nextarchive:
                contents.append(E.link({'rel': 'next-archive',
                                        'href': nextarchive}))
            for entry in entries:
                assert isinstance(entry, dict)
                published_key = 'published'
                updated_key = 'updated'
                summary = entry['summary']
                if summary is None:
                    summary = ''
                if isinstance(summary, Literal) and summary.datatype == RDF.XMLLiteral:
                    datatype = "html" # not xhtml -- that requires
                                      # proper namespacing and stuff,
                                      # with html we just throw
                                      # encoded, possibly ill-formed
                                      # tag soup in the tree
                else:
                    datatype = "text"
                entrynodes = [E.title(entry['title']),
                              E.summary({'type': datatype}, summary),
                              E.id(entry['uri']),
                              E.published(util.rfc_3339_timestamp(entry[published_key])),
                              E.updated(util.rfc_3339_timestamp(entry[updated_key])),
                              E.link({'href': util.relurl(entry['url'], feedurl)})]
                if entry['link']:
                    node = E.link({'rel': 'alternate',
                                   'href': util.relurl(entry['link']['href'],
                                                       feedurl),
                                   'type': entry['link']['type'],
                                   'length': str(entry['link']['length']),
                                   'hash': entry['link']['hash']})
                    entrynodes.append(node)
                if entry['content'] and entry['content']['markup']:
                    node = E.content({'type': 'xhtml'},
                                     etree.XML(entry['content']['markup']))
                    entrynodes.append(node)
                elif entry['content'] and entry['content']['src']:
                    node = E.content({'src': util.relurl(entry['content']['src'],
                                                         feedurl),
                                      'type': entry['content']['type'],
                                      'hash': entry['content']['hash']})
                    entrynodes.append(node)
                contents.append(E.entry(*list(entrynodes)))
            feed = E.feed(*contents)
            res = etree.tostring(feed,
                                 pretty_print=True,
                                 xml_declaration=True,
                                 encoding='utf-8')
            with self.store.open_resource("feed/%s%s.atom" % (slug, suffix), mode="wb") as fp:
                fp.write(res)
            # FIXME: temporary workaround of the issue that
            # self.store._open creates files readably only by the
            # creating user
            os.chmod(feedfile, stat.S_IRUSR|stat.S_IWUSR|stat.S_IRGRP|stat.S_IWGRP|stat.S_IROTH)
            return feedfile

        assert isinstance(entries, list), 'entries should be a list, not %s' % type(entries)
        feedurl = self.generic_url(slug, 'feed', '.atom')
        # not sure abt this - should be uri of dataset?
        feedid = feedurl

        # assume entries are sorted newest first
        # could be simplified with more_itertools.chunked?
        cnt = 0
        res = []
        # print("chunking...")
        while len(entries) >= archivesize * 2:
            cnt += 1
            archiveentries = entries[-archivesize:]
            entries[:] = entries[:-archivesize]

            if cnt > 1:
                prev = "%s-archive-%s.atom" % (slug, cnt - 1)
            else:
                prev = None
            if len(entries) < archivesize * 2:
                next = "%s.atom" % slug
            else:
                next = "%s-archive-%s.atom" % (slug, cnt + 1)
            suffix = suffix = '-archive-%s' % cnt
            res.append(write_file(archiveentries, suffix=suffix,
                                  prevarchive=prev,
                                  nextarchive=next))

        res.insert(0, write_file(entries,
                                 prevarchive="%s-archive-%s.atom" % (slug, cnt)))
        return res

    def frontpage_content(self, primary=False):
        """If the module wants to provide any particular content on the
        frontpage, it can do so by returning a XHTML fragment (in text
        form) here.

        :param primary: Whether the caller wants the module to take
                        primary responsibility for the frontpage
                        content. If ``False``, the caller only expects
                        a smaller amount of content (like a smaller
                        presentation of the repository and the
                        document it contains).
        :type primary: bool
        :return: the XHTML fragment
        :rtype: str

        If primary is true, . If primary is false, the caller only expects a
        smaller amount of content (like a smaller presentation of the
        repository and the document it contains).

        """
        g = self.make_graph()
        if isinstance(self.rdf_type, (tuple, list)):
            qname = ", ".join([g.qname(x) for x in self.rdf_type])
        else:
            qname = g.qname(self.rdf_type)
        return ("<h2><a href='%s'>Document repository '%s'</a></h2>"
                "<p>Handles %s documents. "
                "Contains %s published documents.</p>"
                % (self.dataset_uri(), self.alias, qname,
                   len(list(self.store.list_basefiles_for("_postgenerate")))))

    def get_status(self):
        """Returns basic data about the state about this repository, used by
        :meth:`~ferenda.DocumentRepository.status`. Returns a dict of
        dicts, one per state ('download', 'parse' and 'generated'),
        each containing lists under the 'exists' and 'todo' keys.

        :returns: Status information
        :rtype: dict

        """
        # FIXME:
        # * This needs to output data about whether relate or
        #   transformlinks needs to run (even though these actions don'
        #   result in new files).
        # * It should use the logic provided by DocumentStore.needed,
        #   not calling outfile_is_newer et al on its own
        # * Should be able to run with a single basefile, and explain
        #   the status of the different actions (ie generate needed
        #   because a dependency is newer than existing generated
        #   file)
        status = OrderedDict()
        exists = []
        todo = []
        for basefile in self.store.list_basefiles_for("parse"):
            exists.append(basefile)
            # no point in trying to append
        status['download'] = {'exists': exists,
                              'todo': todo}

        # parse
        exists = []
        todo = []
        for basefile in self.store.list_basefiles_for("parse"):
            dependency = self.store.downloaded_path(basefile)
            target = self.store.parsed_path(basefile)
            if os.path.exists(target):
                exists.append(basefile)
            # Note: duplication of (part of) parseifneeded logic
            if not util.outfile_is_newer([dependency], target):
                todo.append(basefile)
        status['parse'] = {'exists': exists,
                           'todo': todo}

        # generated
        exists = []
        todo = []
        for basefile in self.store.list_basefiles_for("generate"):
            dependency = self.store.parsed_path(basefile)
            target = self.store.generated_path(basefile)
            if os.path.exists(target):
                exists.append(basefile)
            # Note: duplication (see above)
            if not util.outfile_is_newer([dependency], target):
                todo.append(basefile)
        status['generated'] = {'exists': exists,
                               'todo': todo}
        return status

    @decorators.action
    def remove(self, basefile):
        """Removes all traces of the specified basefile. This is only used
        manually for cleaning up invalid basefiles that may have been
        created by a buggy implementation of
        :py:meth:`~ferenda.DocumentRepository.remove`."""
        removed = self.store.remove(basefile)
        if removed:
            self.log.info("Removed %s files/directories related to %s" % (removed, basefile))
        else:
            raise errors.DocumentNotFound("No files/directories related to %s found" % (basefile))
            

    def tabs(self):

        """Get the navigation menu segment(s) provided by this docrepo.

        Returns a list of tuples, where each tuple will be rendered
        as a tab in the main UI. First element of the tuple is the
        link text, and the second is the link destination. Normally, a
        module will only return a single tab.

        :returns: (link text, link destination) tuples
        :rtype: list

        Example:

        >>> d = DocumentRepository()
        >>> d.tabs()
        [('base', 'http://localhost:8000/dataset/base')]

        """
        if self.config.tabs:
            uri = self.dataset_uri()
            if self.rdf_type == Namespace(util.ns['foaf']).Document:
                return [(self.alias, uri)]
            else:
                if isinstance(self.rdf_type, (tuple, list)):
                    return [(util.uri_leaf(str(x)), uri) for x in self.rdf_type]
                else:
                    return [(util.uri_leaf(str(self.rdf_type)), uri)]
        else:
            return []

    def footer(self):
        """Get a list of resources provided by this repo for publication in the site footer.

        Works like :meth:`~ferenda.DocumentRepository.tabs`, but
        normally returns an empty list. The repo
        :class:`ferenda.sources.general.Static` is an exception.

        :returns: (link text, link destination) tuples
        :rtype: list

        """
        return []

