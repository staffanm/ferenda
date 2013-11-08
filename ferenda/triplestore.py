# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import xml.etree.cElementTree as ET
import os
from io import BytesIO
import tempfile
import logging
import re
from xml.sax import SAXParseException

from rdflib import URIRef
from rdflib import Graph
from rdflib import URIRef
from rdflib import ConjunctiveGraph
import requests
import requests.exceptions
import pyparsing

from six import text_type as str
from six.moves.urllib_parse import quote

from ferenda.thirdparty import SQLite
from ferenda import util, errors


class TripleStore(object):

    """Presents a limited but uniform interface to different triple
stores. It supports both standalone servers accessed over HTTP (Fuseki
and Sesame, right now) as well as RDFLib-based persistant stores (The
SQLite and Sleepycat/BerkeleyDB backends are supported).

    .. note::

       This class does not implement the `RDFlib store interface
       <http://rdflib.readthedocs.org/en/latest/univrdfstore.html>`_. Instead,
       it provides a small list of operations that is generally useful
       for the kinds of things that ferenda-based applications need to
       do.

    This class is an abstract base class, and is not directly
    instantiated.  Instead, call
    :py:meth:`~ferenda.TripleStore.connect`, which returns an
    initialized object of the appropriate subclass. All subclasses
    implements the following API.

    """

    @staticmethod
    def connect(storetype, location, repository, **kwargs):
        """Returns a initialized object, the exact type depending on the
        ``storetype`` parameter.

        :param storetype: The type of store to connect to (``"FUSEKI"``, ``"SESAME"``, ``"SLEEPYCAT"`` or ``"SQLITE"``)
        :param location: The URL or file path where the main repository is stored
        :param repository: The name of the repository to use with the main repository storage
        :param \*\*kwargs: Any other named parameters are passed to the appropriate class constructor (see "Store-specific parameters" below).

        Example:

        >>> # creates a new SQLite db at /tmp/test.sqlite if not already present
        >>> sqlitestore = TripleStore.connect("SQLITE", "/tmp/test.sqlite", "myrepo")
        >>> sqlitestore.triple_count()
        0
        >>> sqlitestore.close()
        >>> # connect to same db, but store all triples in memory (read-only)
        >>> sqlitestore = TripleStore.connect("SQLITE", "/tmp/test.sqlite", "myrepo", inmemory=True)
        >>> # connect to a remote Fuseki store over HTTP, using the command-line
        >>> # tool curl for faster batch up/downloads
        >>> fusekistore = TripleStore.connect("FUSEKI", "http://localhost:3030/", "ds", curl=True)

        **Store-specific parameters:**

        When using storetypes ``SQLITE`` or ``SLEEPYCAT``, the
        :meth:`~ferenda.TripleStore.select` and
        :meth:`~ferenda.TripleStore.construct` methods can be sped up
        (around 150%) by loading the entire content of the triple
        store into memory, by setting the ``inmemory`` parameter to
        ``True``

        When using storetypes ``FUSEKI`` or ``SESAME``, storage and
        retrieval of a large number of triples (particularly the
        :meth:`~ferenda.TripleStore.add_serialized_file` and
        :meth:`~ferenda.TripleStore.get_serialized_file` methods) can
        be sped up by setting the ``curl`` parameter to ``True``, if
        the command-line tool `curl <http://curl.haxx.se/>`_ is
        available.

        """
        assert isinstance(
            storetype, str), "storetype must be a (unicode) str, not %s" % type(storetype)
        cls = {'SESAME': SesameStore,
               'FUSEKI': FusekiStore,
               'SLEEPYCAT': SleepycatStore,
               'SQLITE': SQLiteStore}.get(storetype, None)
        if cls is None:
            raise ValueError("Unknown storetype %s" % storetype)
        return cls(location, repository, **kwargs)

    def __init__(self, location, repository, **kwargs):
        self.location = location
        self.repository = repository
        self.pending_graph = Graph()
        self.namespaces = {}

    def __del__(self):
        self.close()

    def add_serialized(self, data, format, context=None):
        """Add the serialized RDF statements in the string *data* directly to the repository."""
        raise NotImplementedError  # pragma: no cover

    def add_serialized_file(self, filename, format, context=None):
        """Add the serialized RDF statements contained in the file *filename* directly to the repository."""
        with open(filename, "rb") as fp:
            self.add_serialized(fp.read(), format, context)

    def get_serialized(self, format="nt", context=None):
        """Returns a string containing all statements in the store,
        serialized in the selected format. Returns byte string, not unicode array!"""
        raise NotImplementedError  # pragma: no cover

    def get_serialized_file(self, filename, format="nt", context=None):
        """Saves all statements in the store to *filename*."""
        data = self.get_serialized(format, context)
        with open(filename, "wb") as fp:
            fp.write(data)

    def select(self, query, format="sparql"):
        """Run a SPARQL SELECT query against the triple store and returns the results.

        :param query: A SPARQL query with all neccessary prefixes defined.
        :type  query: str
        :param format: Either one of the standard formats for queries
                       (``"sparql"``, ``"json"`` or ``"binary"``) --
                       returns whatever ``requests.get().text``
                       returns -- or the special value ``"python"``
                       which returns a python list of dicts
                       representing rows and columns.
        :type  format: str

        """
        raise NotImplementedError  # pragma: no cover

    def construct(self, query):
        """Run a SPARQL CONSTRUCT query against the triple store and returns
        the results as a RDFLib graph

        :param query: A SPARQL query with all neccessary prefixes defined.
        :type query: str

        """
        raise NotImplementedError  # pragma: no cover

    def triple_count(self, context=None):
        """Returns the number of triples in the repository."""
        raise NotImplementedError  # pragma: no cover

    def clear(self, context=None):
        """Removes all statements from the repository (without removing the
        repository as such)."""
        raise NotImplementedError  # pragma: no cover

    def close(self):
        """Close all connections to the triplestore. Needed if using
        RDFLib-based triple store, a no-op if using HTTP based stores."""
        raise NotImplementedError  # pragma: no cover


class RDFLibStore(TripleStore):

    def __init__(self, location, repository, inmemory=False):
        super(RDFLibStore, self).__init__(location, repository)
        self.inmemory = inmemory
        self.closed = False
        graphid = URIRef("file://" + self.repository)
        g = ConjunctiveGraph(store=self._storeid(), identifier=graphid)
        if os.path.exists(self.location):
            g.open(self.location, create=False)
        else:
            g.open(self.location, create=True)

        l = logging.getLogger(__name__)
        if inmemory:
            l.debug("Loading store into memory")
            ig = ConjunctiveGraph(identifier=graphid)
            ig.addN(g.quads())
            g.close()
            self.graph = ig
        else:
            l.debug("Using on-disk store")
            self.graph = g

    def add_serialized(self, data, format, context=None):
        if self.inmemory:
            raise errors.TriplestoreError("In-memory stores are read-only")
        g = self._getcontextgraph(context)
        g.parse(data=data, format=format)
        g.commit()

    def get_serialized(self, format="nt", context=None):
        g = self._getcontextgraph(context)
        return g.serialize(format=format)

    def triple_count(self, context=None):
        g = self._getcontextgraph(context)
        return len(g)

    def select(self, query, format="sparql"):
        # FIXME: workaround for the fact that rdflib select uses FROM
        # <%s> differently than Sesame/Fuseki. We remove the 'FROM
        # <%s>' part from the query and instead get a context graph
        # for the same URI.
        re_fromgraph = re.compile(r" FROM <(?P<graphuri>[^>]+)> ")
        graphuri = None
        m = re_fromgraph.search(query)
        if m:
            graphuri = m.group("graphuri")
            query = re_fromgraph.sub(" ", query)
        try:
            res = self._getcontextgraph(graphuri).query(query)
        except pyparsing.ParseException as e:
            raise errors.SparqlError(e)
        if format == "sparql":
            return res.serialize(format="xml")
        elif format == "json":
            return res.serialize(format="json")
        else:
            # or just
            # return self._sparql_results_to_list(res.serialize(format="xml"))
            l = []
            for r in res.bindings:
                d = {}
                for (key, val) in r.items():
                    d[str(key)] = str(val)
                l.append(d)
            return l

    def construct(self, query):
        """
        Run a SPARQL CONSTRUCT query against the triple store and returns the results as a RDFLib graph

        :param query: A SPARQL query with all neccessary prefixes defined.
        :type query: str
        """
        try:
            res = self.graph.query(query)
        except pyparsing.ParseException as e:
            raise errors.SparqlError(e)
        return res.graph

    def clear(self, context=None):
        for (s, p, o) in self._getcontextgraph(context):
            self.graph.remove((s, p, o))
        self.graph.commit()

    def close(self):
        if not self.closed:
            import sqlite3
            try:
                self.graph.close(True)
            # 'Cannot operate on a closed database' -- can't figure out why this happens on win32
            except sqlite3.ProgrammingError:
                pass
            self.closed = True

    def initialize_repository(self):
        self.graph.open(self.location, create=True)

    def remove_repository(self):
        self.graph.destroy()

    # returns a string we can pass as store parameter to the ConjunctiveGraph
    # constructor, see __init__
    def _storeid(self):
        raise NotImplementedError  # pragma: no cover

    def _getcontextgraph(self, context):
        if context:
            return self.graph.get_context(URIRef(context))
        else:
            return self.graph


class SleepycatStore(RDFLibStore):

    def _storeid(self):
        return "Sleepycat"


class SQLiteStore(RDFLibStore):

    def triple_count(self, context=None):
        g = self._getcontextgraph(context)
        return len(list(g))  # bug? otherwise returns # of unique subjects

    def _storeid(self):
        return "SQLite"

# -----------------
# For servers implementing the SPARQL 1.1 Graph Store HTTP Protocol
# http://www.w3.org/TR/sparql11-http-rdf-update/
class RemoteStore(TripleStore):

    def close(self):
        pass

    _contenttype = {"xml": "application/rdf+xml",
                    "sparql": "application/sparql-results+xml",
                    "nt": "text/plain",
                    "ttl": "application/x-turtle",
                    "turtle": "application/x-turtle",
                    "n3": "text/rdf+n3",
                    "trix": "application/trix",
                    "trig": "application/x-trig",
                    "json": "application/sparql-results+json",
                    "binary": "application/x-binary-rdf-results-table"}

    def __init__(self, location, repository, curl=False):
        super(RemoteStore, self).__init__(location, repository)
        self.curl = curl
        if self.location.endswith("/"):
            self.location = self.location[:-1]

    def add_serialized(self, data, format, context=None):
        if isinstance(data, str):
            data = data.encode('utf-8')
        if self.curl:
            fp = tempfile.NamedTemporaryFile(delete=False)
            fp.write(data)
            tmp = fp.name
            fp.close()
            self.add_serialized_file(tmp, format, context)
            os.unlink(tmp)
        else:
            # Requests 1.2 has a bug that leads to duplicated
            # Content-type headers under at least python 3, and
            # under py 3.3 this causes problem with both fuseki
            # and sesame. see end of prepare_body in models.py
            # (line 381).  one way of working around this bug is
            # to use a streaming request, so we wrap our data in a
            # file-like object. All ways are good except the bad.
            datastream = BytesIO(data)
            datastream.len = len(data)
            headers = {'Content-Type':
                       self._contenttype[format] + "; charset=UTF-8"}
            resp = requests.post(self._statements_url(context),
                                 headers=headers,
                                 data=datastream)
            resp.raise_for_status()

    def add_serialized_file(self, filename, format, context=None):
        if self.curl:
            opt = {'url': self._statements_url(context),
                   'contenttype': self._contenttype[format] + ";charset=UTF-8",
                   'filename': filename,
                   'method': 'POST'}
            self._run_curl(opt)
        else:
            # initialize req
            with open(filename, "rb") as fp:
                resp = requests.post(self._statements_url(context),
                                     headers={'Content-Type':
                                              self._contenttype[format] + ";charset=UTF-8"},
                                     data=fp)
                resp.raise_for_status()

    def get_serialized(self, format="nt", context=None):
        if self.curl:
            fileno, tmp = tempfile.mkstemp()
            fp = os.fdopen(fileno)
            fp.close()
            opt = {'url': self._statements_url(context),
                   'accept': self._contenttype[format],
                   'filename': tmp,
                   'method': 'GET'}
            self._run_curl(opt)
            with open(tmp, 'rb') as fp:
                data = fp.read()
            os.unlink(tmp)
            return data
        else:
            r = requests.get(self._statements_url(context),
                             headers={'Accept': self._contenttype[format]})
            r.raise_for_status()
            return r.content

    def get_serialized_file(self, filename, format="nt", context=None):
        if self.curl:
            opt = {'url': self._statements_url(context),
                   'accept': self._contenttype[format],
                   'filename': filename,
                   'method': 'GET'}
            self._run_curl(opt)
        else:
            super(RemoteStore, self).get_serialized_file(filename, format, context)

    def clear(self, context=None):
        try:
            url = self._statements_url(context)
            resp = requests.delete(url)
            resp.raise_for_status()

        except requests.exceptions.ConnectionError as e:
            raise errors.TriplestoreError(
                "Triplestore %s not responding: %s" % (url, e))
        except requests.exceptions.HTTPError as e:
            if (self.__class__ == FusekiStore) and ("No such graph" in str(e)):
                pass
            else:
                raise errors.TriplestoreError(
                    "Triplestore %s returns error: %s" % (url, e))

    def select(self, query, format="sparql"):
        url = self._endpoint_url()
        url += "?query=" + quote(query.replace("\n", " ")).replace("/", "%2F")

        headers = {}
        if format == "python":
            headers['Accept'] = self._contenttype["sparql"]
        else:
            headers['Accept'] = self._contenttype[format]
        try:
            results = requests.get(url, headers=headers, data=query)
            results.raise_for_status()
            if format == "python":
                return self._sparql_results_to_list(results.text)
            elif format == "json":
                return results.json()
            else:
                return results.text
        except requests.exceptions.HTTPError as e:
            raise errors.SparqlError(e)

    def construct(self, query):
        url = self._endpoint_url()
        url += "?query=" + quote(query)
        try:
            format = "xml"
            headers = {'Accept': self._contenttype[format]}
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            result = Graph()
            result.parse(data=resp.text, format=format)
            return result
        except requests.exceptions.HTTPError as e:
            raise errors.SparqlError(e)
        except SAXParseException as e:
            # No real error message, most likely a empty string. We'll
            # return a empty graph for now, which'll trigger a warning
            # by the caller
            return result

    def _sparql_results_to_list(self, results):
        res = []
        tree = ET.fromstring(results)
        for row in tree.findall(".//{http://www.w3.org/2005/sparql-results#}result"):
            d = {}
            for element in row:
                # print element.tag # should be "binding"
                key = element.attrib['name']
                # note: str is really six.text_type, ie str on py3, unicode on py2
                value = str(element[0].text)
                d[key] = value
            res.append(d)
        return res

    def _statements_url(self, context):
        if context:
            return "%s/%s?graph=%s" % (self.location, self.repository, context)
        else:
            return "%s/%s?default" % (self.location, self.repository)

    # this method does not take a context parameter. Restrict to
    # context/graph in the query instead.
    def _endpoint_url(self):
        return "%s/%s/query" % (self.location, self.repository)
        

    def _run_curl(self, options):
        if options['method'] == 'GET':
            cmd = 'curl -o "%(filename)s" --header "Accept:%(accept)s" "%(url)s"' % options
        elif options['method'] == 'POST':
            cmd = 'curl -X POST --data-binary "@%(filename)s" --header "Content-Type:%(contenttype)s" "%(url)s"' % options
        (ret, stdout, stderr) = util.runcmd(cmd)
        if ret != 0:
            raise errors.TriplestoreError(stderr)
        return stdout


class SesameStore(RemoteStore):

    def add_serialized(self, data, format, context=None):
        if format == "turtle":
            # Sesame has a problem with turtle like the following:
            #
            # """@prefix : <http://example.org/doc/>
            #
            # :1 a :Document"""
            #
            # which gets interpreted like the subject is a predicate like
            # "1"^^xsd:integer
            #
            # Therefore, we convert it to nt prior to posting
            g = Graph()
            g.parse(data=data, format="turtle")
            data = g.serialize(format="nt")
        super(SesameStore, self).add_serialized(data, format, context)

    def triple_count(self, context=None):
        # Sesame has a non-standardized but quick way of finding out # of triples
        if context:
            url = "%s/repositories/%s/size?context=<%s>" % (
                self.location, self.repository, context)
        else:
            url = "%s/repositories/%s/size" % (self.location, self.repository)
        ret = requests.get(url)
        return int(ret.text)

    def ping(self):
        resp = requests.get(self.location + '/protocol')
        return resp.text

    def initialize_repository(self):
        # For Sesame:
        # curl -H "Content-type: application/x-turtle" -d @createrepo.ttl  http://localhost:8080/openrdf-sesame/repositories/SYSTEM/statements
        # where createrepo.ttl is something like:
        #
        # @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
        # @prefix rep: <http://www.openrdf.org/config/repository#>.
        # @prefix sr: <http://www.openrdf.org/config/repository/sail#>.
        # @prefix sail: <http://www.openrdf.org/config/sail#>.
        # @prefix ns: <http://www.openrdf.org/config/sail/native#>.
        #
        # [] a rep:Repository ;
        #    rep:repositoryID "netstandards" ;
        #    rdfs:label "Ferenda repository for netstandards" ;
        #    rep:repositoryImpl [
        #       rep:repositoryType "openrdf:SailRepository" ;
        #       sr:sailImpl [
        #          sail:sailType "openrdf:NativeStore" ;
        #          ns:tripleIndexes "spoc,posc,cspo,opsc,psoc"
        #       ]
        #    ].
        #
        # See http://answers.semanticweb.com/questions/16108/createdelete-a-sesame-repository-via-http
        # Note in particular
        #
        # > Just to add one thing I noticed in actually implementing
        # > this, the graph created from a template must be POSTed to a
        # > named context in the SYSTEM repository otherwise Sesame
        # > doesn't like it i.e. if you just post a graph to the SYSTEM
        # > repo without a named context Sesame will recognize that it
        # > exists but won't be able to load it properly
        #
        # Good point Rob. In addition, the named context identifier is
        # expected to be typed as an instance of type
        # sys:RepositoryContext.  We are considering to change this
        # though, to make doing these kinds of things easier.
        pass

     # These deviate from the SPARQL 1.1 Protocol and SPARQL 1.1 Graph Store HTTP Protocol.
     # FIXME: Test if sesame still needs these or if we can use the standards

    def _statements_url(self, context):
        if context:
            return "%s/repositories/%s/statements?context=<%s>" % (self.location, self.repository, context)
        else:
            return "%s/repositories/%s/statements" % (self.location, self.repository)

    def _endpoint_url(self):
        return "%s/repositories/%s" % (self.location, self.repository)


class FusekiStore(RemoteStore):

    def triple_count(self, context=None):
        # Fuseki doesn't provide a HTTP API for retrieving the size of
        # a repository. We do one or two SPARQL COUNT() queries to
        # find out.
        if context:
            sq = "SELECT COUNT(*) WHERE { GRAPH <%s> { ?s ?p ?o}}" % context
            res = self.select(sq, format="python")
            return int(list(res[0].values())[0])
        else:
            # this ONLY counts triples in the default graph
            sq = "SELECT COUNT(*) WHERE {?s ?p ?o}"
            res = self.select(sq, format="python")
            default = int(list(res[0].values())[0])

            # this ONLY counts triples in all named graphs
            sq = "SELECT COUNT(*) WHERE { GRAPH <urn:x-arq:UnionGraph> {?s ?p ?o}}"
            res = self.select(sq, format="python")
            named = int(list(res[0].values())[0])
            return default + named

    def clear(self, context=None):
        super(FusekiStore, self).clear(context)
        if context is None:
            url = self._statements_url("urn:x-arq:UnionGraph")
            resp = requests.delete(url)
            # if the call to super().clear() went ok, so should this,
            # hence no error handling
            resp.raise_for_status()

    def construct(self, query, uniongraph=True):
        # This is to work around the default config where Fuseki does
        # not include all named graphs in the default
        # graph. Not very pretty...
        if uniongraph:
            query = re.sub(r"}\s+WHERE\s+{", "} WHERE { GRAPH <urn:x-arq:UnionGraph> {",
                           query, flags=re.MULTILINE)
            query += " }"
            
        return super(FusekiStore, self).construct(query)
            
    def initialize_repository(self):
        # I have no idea how to do this for Fuseki
        pass

    # To work around the fact that the default graph, by default in
    # Fuseki, does not contain any triples in any named graph. The
    # magic named graph <urn:x-arq:UnionGraph> however, contains all
    # triples in all named graphs. It does not contain anything in the
    # default graph, though, so we'll have get that separately. Note
    # that this kills performance for any other format than nt, as
    # other formats require that we join the two result sets using
    # rdflib
    def get_serialized(self, format="nt", context=None):
        default = super(FusekiStore, self).get_serialized(format, context)
        if context is not None:
            return default
        else:
            context = "urn:x-arq:UnionGraph"
            named = super(FusekiStore, self).get_serialized(format, context)
            if format == "nt":
                return default + named
            else:
                g = Graph()
                g.parse(data=default, format=format)
                g.parse(data=named, format=format)
                return g.serialize(format=format)

#    def get_serialized_file(self, filename, format="nt", context=None):
#        ret = super(FusekiStore, self).get_serialized_file(filename, format, context)
#        if context is not None:
#            return ret
#        else:
#            context = "urn:x-arq:UnionGraph"
#            named = super(FusekiStore, self).get_serialized(format, context)
#            if format == "nt":
#                # just append
#                with open(filename, "ab") as fp:
#                    fp.write(named)
#            else:
#                g = Graph()
#                g.parse(filename, format=format)
#                g.parse(data=named, format=format)
#                with open(filename, "wb") as fp:
#                    fp.write(g.serialize(format=format))
