# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import xml.etree.cElementTree as ET
import os
from io import BytesIO
from rdflib import Literal, BNode, Namespace, URIRef
from rdflib import Graph
from rdflib.plugins.parsers.ntriples import NTriplesParser
from rdflib import Namespace, URIRef, Literal, RDFS, RDF, ConjunctiveGraph, plugin, store

import requests
import requests.exceptions

import six
from six import text_type as str
if six.PY3:
    from urllib.parse import quote
else:
    from urllib import quote

from ferenda.thirdparty import SQLite
from ferenda import errors
    
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


    :param location: The URL or file path where the main repository is stored
    :param repository: The name of the repository to use with the main repository storage
    :param context: The context (in the form of a URI) to use within the repository. This property can be set and re-set during the lifetime of a connection
    :param storetype: The type of store to connect to (``FUSEKI``, ``SESAME``, ``SLEEPYCAT`` or ``SQLITE``)
    :param communication: If using HTTP-based storetypes (``FUSEKI`` or ``SESAME``), whether to use python or a separate command line tool for communication.
    :param ping: When using HTTP based stores that supports a "ping" method, attempt to connect and return the result of that ping.
    """
    # triplestore flavors
    FUSEKI = 1
    """Constant for the ``storetype`` parameter for using Fuseki over HTTP."""
    SESAME = 2
    """Constant for the ``storetype`` parameter for using Sesame over HTTP."""
    SLEEPYCAT = 3 # by way of rdflib
    """Constant for the ``storetype`` parameter for using RDFLib + Sleepycat/BerkeleyDB."""
    SQLITE = 4    #      - "" -
    """Constant for the ``storetype`` parameter for using Fuseki over HTTP."""
    # communication flavors -- not applicable when using SLEEPYCAT/SQLITE
    REQUESTS = 1  # pure-python, no external deps
    """Constant for the ``communication`` parameter for using pure-python HTTP communication."""
    CURL = 2   # requires command line curl binary, faster
    """Constant for the ``communication`` parameter for using the ``curl`` command line tool."""

    # Inspired by http://www.openvest.com/trac/browser/rdfalchemy/trunk/rdfalchemy/sparql/sesame2.py
    # see Sesame REST API doc at http://www.openrdf.org/doc/sesame2/system/ch08.html

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

    def __init__(self, location, repository, context=None, storetype=SESAME, communication=REQUESTS, ping=False):
        self._closed = False
        self.location = location
        if self.location.endswith("/"):
            self.location = self.location[:-1]
        self.repository = repository
        self.pending_graph = Graph()
        self.namespaces = {}
        if isinstance(storetype,str):
            if storetype == "SESAME":
                self.storetype = self.SESAME
            elif storetype == "FUSEKI":
                self.storetype = self.FUSEKI
            elif storetype == "SLEEPYCAT":
                self.storetype = self.SLEEPYCAT
            elif storetype == "SQLITE":
                self.storetype = self.SQLITE
            else:
                raise ValueError("Unknown storetype %s" % storetype)
        else:
            self.storetype = storetype
        self.communication = communication
        self.context = context
        if self.storetype in (self.SLEEPYCAT,self.SQLITE):
            if self.storetype == self.SQLITE:
                storeid = "SQLite"
            else:
                storeid = "Sleepycat"
            self.graph = ConjunctiveGraph(store=storeid, identifier=URIRef(self.repository))
            if os.path.exists(self.location):
                ret = self.graph.open(self.location, create=False)
            else:
                ret = self.graph.open(self.location, create=True)
        # Ping the server and see what we have
        if ping and storetype == self.SESAME:
            requests.get(self.location + '/protocol')
            return r.text

    def close(self):
        """Close all connections to the triplestore. Needed if using RDFLib-based triple store, a no-op if using HTTP based stores."""
        if self.storetype in (self.SQLITE,self.SLEEPYCAT) and (not self._closed):
            import sqlite3
            try:
                self.graph.close(True)
            except sqlite3.ProgrammingError: # 'Cannot operate on a closed database' -- can't figure out why this happens on win32
                pass
            self._closed = True
            
    def __del__(self):
        self.close()

    def _statements_url(self):
        assert self.storetype not in (self.SQLITE,self.SLEEPYCAT)
        if self.storetype == self.SESAME:
            if self.context:
                return "%s/repositories/%s/statements?context=<%s>" % (self.location, self.repository, self.context)
            else:
                return "%s/repositories/%s/statements" % (self.location, self.repository)
        else:
            if self.context:
                return "%s/%s?graph=%s" % (self.location, self.repository, self.context)
            else:
                return "%s/%s?default" % (self.location, self.repository)

    def _endpoint_url(self):
        assert self.storetype not in (self.SQLITE,self.SLEEPYCAT)
        if self.storetype == self.SESAME:
            return "%s/repositories/%s" % (self.location,self.repository)
        else:
            return "%s/%s/query" % (self.location, self.repository)

    def _getcontextgraph(self):
        assert self.storetype in (self.SQLITE,self.SLEEPYCAT)
        if self.context:
            return self.graph.get_context(URIRef(self.context))
        else:
            return self.graph

    def _filter_null_datatype(self, graph):
        result = Graph()
        # the result graph may contains invalid
        # datatype attributes -- filter these
        for (s,p,o) in graph:
            if isinstance(o,Literal) and o.datatype == URIRef('NULL'):
                result.add((s,p,Literal(str(o))))
            else:
                result.add((s,p,o))
        return result
            
    def bind(self, prefix, namespace):
        """Bind a prefix to a namespace for the pending graph (used by :py:meth:`~ferenda.Triplestore.add_graph`)."""
        self.namespaces[prefix] = namespace
        # print "binding %s as %s" % (namespace,prefix)
        self.pending_graph.bind(prefix, namespace)

    def initialize_repository(self):
        """Creates the repository, if it's not available already.

        .. note::

           Only works for RDFLib-based stores.
        """
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
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            self.graph.open(self.location, create=True)
        else:
            pass
    
    def remove_repository(self):
        """Completely destroys the repository and all information.

        .. note::

           Only works for RDFLib-based stores.
        """
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            self.graph.destroy()
        else:
            pass
        
    def get_serialized(self, format="nt"):
        """Returns a string containing all statements in the store,
        serialized in the selected format. Returns byte string, not unicode array!"""
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            # g = self._filter_null_datatype(self._getcontextgraph())
            g = self._getcontextgraph()
            return g.serialize(format=format).decode('utf-8').strip()
            
            # FIXME: is utf-8 always the correct encoding?
            # return self._getcontextgraph().serialize(format=format) # .decode('utf-8')
        else:
            r = requests.get(self._statements_url(
                    ), headers={'Accept': self._contenttype[format]})
            r.raise_for_status()
            return r.text.strip()

    def get_serialized_file(self, path, format="nt"):
        """Saves all statements in the store to *path*."""
        
        # FIXME: 1. stream data instead of storing it in a in-memory string
        #        2. implement CURL support
        data = self.get_serialized(format)
        with open(path,"w") as fp:
            fp.write(data)
            
    def select(self, query, format="sparql"):
        """
        Run a SPARQL SELECT query against the triple store and returns the results.

        :param query: A SPARQL query with all neccessary prefixes defined.
        :type query: str
        :param format: Either one of the standard formats for queries (``"sparql"``, ``"json"`` or ``"binary"``) -- returns whatever ``requests.get().text`` returns -- or the special value ``"python"`` which returns a python list of dicts representing rows and columns.
        :type format: str
        """
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            g = self._getcontextgraph()
            res = g.query(query)
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
                    for (key,val) in r.items():
                        d[str(key)]=str(val)
                    l.append(d)
                return l
        else:
            url = self._endpoint_url()
            if "?" in url:
                url += "&"
            else:
                url += "?"
            url += "query=" + quote(query.replace("\n", " ")).replace("/","%2F")
            
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
                    return results.json
                else:
                    return results.text
            except requests.exceptions.HTTPError as e:
                raise errors.SparqlError(e)

    def _sparql_results_to_list(self, results):
        res = []
        tree = ET.fromstring(results)
        for row in tree.findall(".//{http://www.w3.org/2005/sparql-results#}result"):
            d = {}
            for element in row:
                #print element.tag # should be "binding"
                key = element.attrib['name']
                value = element[0].text
                d[key] = value
            res.append(d)
        return res

    def construct(self, query):
        """
        Run a SPARQL CONSTRUCT query against the triple store and returns the results as a RDFLib graph

        :param query: A SPARQL query with all neccessary prefixes defined.
        :type query: str
        """
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            #print(query)
            g = self._getcontextgraph()
            res = g.query(query)
            result = self._filter_null_datatype(res.graph)
            del g
            del res
            #print("-" * 70)
            #print(result.serialize(format="turtle").decode('utf-8'))
            #print("-" * 70)
            return result
        else:
            # query = " ".join(query.split())  # normalize space
            url = self._endpoint_url()
            if not self.context:
                url += "?"
            url += "query=" + quote(query)
            try:
                r = requests.get(
                    url)
                format = "xml"
                headers = {'Accept': self._contenttype[format]}
                resp = requests.get(url, headers=headers, data=query)
                resp.raise_for_status()
                result = Graph()
                result.parse(data=resp.text,format=format)
                return result
            except requests.exceptions.HTTPError as e:
                raise errors.SparqlError(e.response.text)

    def clear(self):
        """Removes all statements from the repository (without removing the repository as such)."""
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            for (s,p,o) in self._getcontextgraph():
                # print("Removing %s %s %s" % (s,p,o))
                self.graph.remove((s,p,o))
            self.graph.commit()
        else:        
            try:
                url = self._statements_url()
                resp = requests.delete(url)
                resp.raise_for_status()
                if self.storetype == self.FUSEKI and self.context is None:
                    self.context = "urn:x-arq:UnionGraph"
                    url = self._statements_url()
                    resp = requests.delete(url)
                    resp.raise_for_status()
                    self.context = None
                
            except requests.exceptions.ConnectionError as e:
                raise errors.TriplestoreError(
                    "Triplestore %s not responding: %s" % (url, e))
            except requests.exceptions.HTTPError as e:
                if (self.storetype == self.FUSEKI) and ("No such graph" in str(e)):
                    pass
                else:
                    raise errors.TriplestoreError(
                        "Triplestore %s returns error: %s" % (url, e))

    def triple_count(self):
        """Returns the numbers of triples in the store."""
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            g = self._getcontextgraph()
            if self.storetype == self.SQLITE:
                return len(list(g)) # bug? otherwise returns # of unique subjects
            else:
                return len(g)

        elif self.storetype == self.SESAME:
            if self.context:
                url = "%s/repositories/%s/size?context=<%s>" % (
                    self.location, self.repository, self.context)
            else:
                url = "%s/repositories/%s/size" % (self.location, self.repository)
            ret = requests.get(url)
            return int(ret.text)
        else:
            # For stores that doesn't provide a HTTP API for
            # retrieving the size of a repository, we must get the
            # entire repo and count the number of triples (by counting
            # newlines). This is obviously slow. Maybe a faster way is
            # a SPARQL COUNT() query?
            if self.context:
                try:
                    tmp = self.get_serialized(format="nt")
                    if tmp:
                        return tmp.count("\n") + 1
                    else:
                        return 0
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404:
                        return 0
                    else:
                        raise e
            else:
                orig_ctx = self.context
                tmp = self.get_serialized(format="nt")
                if tmp:
                    default_triples = tmp.count("\n") + 1
                else:
                    default_triples = 0
                # union of all named graphs, does not (in default config)
                # include the default graph
                self.context = "urn:x-arq:UnionGraph"
                tmp = self.get_serialized(format="nt")
                if tmp:
                    named_graph_triples = tmp.count("\n") + 1
                else:
                    named_graph_triples = 0
                default_triples += named_graph_triples
                self.context = orig_ctx
                return default_triples

#    def clear_subject(self, subject):
#        #print "Deleting all triples where subject is %s from %s" % (subject, self.statements_url)
#        req = Request(self.statements_url + "?subj=%s" % subject)
#        req.get_method = lambda : "DELETE"
#        return self.__urlopen(req)

    def add_graph(self, graph):
        """Prepares adding a rdflib.Graph to the store (use
:py:meth:`~ferenda.TripleStore.commit` to actually store it)"""
        self.pending_graph += graph

    def add_triple(self, subj, pred, obj):
        """Prepares adding a single rdflib triple to the store (use
        :py:meth:`~ferenda.TripleStore.commit` to actually store
        it)

        """
        self.pending_graph.add((subj, pred, obj))

    def commit(self):
        """Store all prepared (pending) RDF statements in the repository."""
        if len(self.pending_graph) == 0:
            return
        # print "Committing %s triples to %s" % (len(self.pending_graph), self.statements_url)
        data = self.pending_graph.serialize(format="nt")

        # RDFlibs nt serializer mistakenly serializes to UTF-8, not
        # the unicode escape sequence format mandated by the ntriples
        # spec -- fix this:

        # let's hope it's already fixed
        # data = ''.join([ord(c) > 127 and '\u%04X' % ord(c) or c for c in data.decode('utf-8')])

        # reinitialize pending_graph
        self.pending_graph = Graph()
        for prefix, namespace in list(self.namespaces.items()):
            self.pending_graph.bind(prefix, namespace)

        return self.add_serialized(data, "nt")

    def add_serialized_file(self, filename, format=None):
        """Add a file containing serialized RDF statements directly to the repository."""
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            g = self._getcontextgraph()
            g.parse(source=filename,format=format)
            g.commit()
        else:
            if self.communication == self.CURL:
                # initialize opt
                self.__curl(opt)
            else:
                # initialize req
                with open(filename, "rb") as fp:
                    resp = requests.post(self._statements_url(),
                                         headers={'Content-Type':
                                                  self._contenttype[format] + ";charset=UTF-8"},
                                         data=fp)
                    resp.raise_for_status()


    def add_serialized(self, data, format):
        """Add a serialized RDF statements directly to the repository."""
        if self.storetype in (self.SQLITE,self.SLEEPYCAT):
            g = self._getcontextgraph()
            g.parse(data=data,format=format)
            g.commit()
        else:
            if format == "turtle" and self.storetype == self.SESAME:
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
                g.parse(data=data,format="turtle")
                data = g.serialize(format="nt")

            if isinstance(data,str):
                # data = data.encode('ascii',errors="ignore")
                data = data.encode('utf-8')
                # pass
            if self.communication == self.CURL:
                tmp = mktemp()
                with open(tmp, "wb") as fp:
                    fp.write(data)
                self.add_serialized_file(tmp, format)
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
                headers={'Content-Type':
                         self._contenttype[format] + "; charset=UTF-8"}
                resp = requests.post(self._statements_url(),
                                     headers=headers,
                                     data=datastream)

                if resp.status_code >= 400:
                    print("Something went wrong posting to %s" % self._statements_url())
                    print(resp.text.encode('latin-1',errors='xmlcharrefreplace'))
                resp.raise_for_status()
