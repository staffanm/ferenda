# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

from collections import defaultdict, OrderedDict, Counter
from datetime import date, datetime
from io import BytesIO
from operator import itemgetter
from wsgiref.util import FileWrapper
from urllib.parse import parse_qsl, urlencode
import inspect
import json
import logging
import mimetypes
import os
import pkg_resources
import re
import sys

from rdflib import URIRef, Namespace, Literal, Graph
from lxml import etree
from layeredconfig import LayeredConfig, Defaults, INIFile

from ferenda import (DocumentRepository, FulltextIndex, Transformer,
                     Facet, ResourceLoader)
from ferenda import fulltextindex, util, elements
from ferenda.elements import html


class WSGIApp(object):

    """Implements a WSGI app.
    """

    def __init__(self, repos, inifile=None, **kwargs):
        self.repos = repos
        self.log = logging.getLogger("wsgi")

        # FIXME: Cut-n-paste of the method in Resources.__init__
        loadpaths = [ResourceLoader.make_loadpath(repo) for repo in repos]
        loadpath = ["."]  # cwd always has priority -- makes sense?
        for subpath in loadpaths:
            for p in subpath:
                if p not in loadpath:
                    loadpath.append(p)
        self.resourceloader = ResourceLoader(*loadpath)
        # FIXME: need to specify documentroot?
        defaults = DocumentRepository.get_default_options()
        if inifile:
            assert os.path.exists(
                inifile), "INI file %s doesn't exist (relative to %s)" % (inifile, os.getcwd())

        # NB: If both inifile and kwargs are specified, the latter
        # will take precedence. I think this is the expected
        # behaviour.
        self.config = LayeredConfig(Defaults(defaults),
                                    INIFile(inifile),
                                    Defaults(kwargs),
                                    cascade=True)

    ################################################################
    # Main entry point

    def __call__(self, environ, start_response):
        from wsgiref.util import request_uri
        import logging
        import traceback
        from pprint import pformat
        log = logging.getLogger("wsgiapp")
        path = environ['PATH_INFO']
        url = request_uri(environ)
        self.log.info("Starting process for %s (path_info=%s, query_string=%s)" % (url, path, environ['QUERY_STRING']))
        # FIXME: routing infrastructure -- could be simplified?
        try:
            if path.startswith(self.config.searchendpoint):
                return self.search(environ, start_response)
            elif (path.startswith(self.config.apiendpoint) or
                  (self.config.legacyapi and path.startswith("/-/publ"))):
                return self.api(environ, start_response)
            else:
                return self.static(environ, start_response)
        except Exception:
            exc_type, exc_value, tb = sys.exc_info()
            exception_data = str(exc_type) + ": " + str(exc_value)
            tbstr = "\n".join(traceback.format_exception(exc_type, exc_value, tb))
            msg = """500 Internal Server Error

%s

----------------
request_uri: %s
QUERY_STRING: %s
PATH_INFO: %s
sys.path: %r
os.getcwd(): %s
----------------
Environ: %s

            """ % (tbstr, url, environ['QUERY_STRING'], environ['PATH_INFO'], sys.path, os.getcwd(), pformat(environ))
            msg = msg.encode('ascii')
            start_response("500 Internal Server Error", [
                ("Content-Type", "text/plain"),
                ("Content-Length", str(len(msg)))
            ])
            return iter([msg])
            
    ################################################################
    # WSGI methods

    def search(self, environ, start_response):
        """WSGI method, called by the wsgi app for requests that matches
           ``searchendpoint``."""
        idx = FulltextIndex.connect(self.config.indextype,
                                    self.config.indexlocation,
                                    self.repos)
        # FIXME: QUERY_STRING should probably be sanitized before calling
        # .query() - but in what way?
        querystring = OrderedDict(parse_qsl(environ['QUERY_STRING']))
        query = querystring['q']
        if not isinstance(query, str):  # happens on py26
            query = query.decode("utf-8")  # pragma: no cover
        pagenum = int(querystring.get('p', '1'))
        res, pager = idx.query(query, pagenum=pagenum)
        if pager['totalresults'] == 1:
            resulthead = "1 match"
        else:
            resulthead = "%s matches" % pager['totalresults']
        resulthead += " for '%s'" % query  # query will be escaped later

        # Creates simple XHTML result page
        repo = self.repos[0]
        doc = repo.make_document()
        doc.uri = "http://example.org/"
        doc.meta.add((URIRef(doc.uri),
                      Namespace(util.ns['dcterms']).title,
                      Literal(resulthead, lang="en")))
        doc.body = elements.Body()
        for r in res:
            if not 'dcterms_title' in r or r['dcterms_title'] is None:
                r['dcterms_title'] = r['uri']
            if r.get('dcterms_identifier', False):
                r['dcterms_title'] = r['dcterms_identifier'] + ": " + r['dcterms_title']
            doc.body.append(html.Div(
                [html.H2([elements.Link(r['dcterms_title'], uri=r['uri'])]),
                 r['text']], **{'class': 'hit'}))

        pages = [
            html.P(["Results %(firstresult)s-%(lastresult)s of %(totalresults)s" % pager])]
        for pagenum in range(pager['pagecount']):
            if pagenum + 1 == pager['pagenum']:
                pages.append(html.Span([str(pagenum + 1)], **{'class': 'page'}))
            else:
                querystring['p'] = str(pagenum + 1)
                url = environ['PATH_INFO'] + "?" + urlencode(querystring)
                pages.append(html.A([str(pagenum + 1)], **{'class': 'page',
                                                           'href': url}))
        doc.body.append(html.Div(pages, **{'class': 'pager'}))
        # Transform that XHTML into HTML5
        conffile = os.sep.join([self.config.documentroot, 'rsrc',
                                'resources.xml'])
        transformer = Transformer('XSLT', "xsl/search.xsl", "xsl",
                                  resourceloader=self.resourceloader,
                                  config=conffile)
        # '/mysearch/' = depth 1
        depth = len(self.config.searchendpoint.split("/")) - 2
        repo = DocumentRepository()
        tree = transformer.transform(repo.render_xhtml_tree(doc), depth)
        data = transformer.t.html5_doctype_workaround(etree.tostring(tree))
        start_response(self._str("200 OK"), [
            (self._str("Content-Type"), self._str("text/html; charset=utf-8")),
            (self._str("Content-Length"), self._str(str(len(data))))
        ])
        return iter([data])

    def api(self, environ, start_response):
        """WSGI method, called by the wsgi app for requests that matches
           ``apiendpoint``."""
        path = environ['PATH_INFO']
        if path.endswith(";stats"):
            d = self.stats()
        else:
            d = self.query(environ)
        data = json.dumps(d, indent=4, default=util.json_default_date,
                          sort_keys=True).encode('utf-8')
        start_response(self._str("200 OK"), [
            (self._str("Content-Type"), self._str("application/json")),
            (self._str("Content-Length"), self._str(str(len(data))))
        ])
        return iter([data])

    def static(self, environ, start_response):
        """WSGI method, called by the wsgi app for all other requests not
        handled by :py:func:`~ferenda.Manager.search` or
        :py:func:`~ferenda.Manager.api`

        """
        fullpath = self.config.documentroot + environ['PATH_INFO']
        # we start by asking all repos "do you handle this path"?
        # default impl is to say yes if 1st seg == self.alias and the
        # rest can be treated as basefile yielding a existing
        # generated file.  a yes answer contains a FileWrapper around
        # the repo-selected file and optionally length (but not
        # status, always 200, or mimetype, always text/html). None
        # means no.
        fp = None
        if not(environ['PATH_INFO'].startswith("/rsrc") and os.path.exists(fullpath)):
            for repo in self.repos:
                (fp, length, status, mimetype) = repo.http_handle(environ)  # and args?
                if fp:
                    status = {200: "200 OK",
                              406: "406 Not Acceptable"}[status]
                    iterdata = FileWrapper(fp)
                    break
        # no repo handled the path
        if not fp:
            if self.config.legacyapi:  # rewrite the path to some resources. FIXME:
                          # shouldn't hardcode the "rsrc" path of the path
                if environ['PATH_INFO'] == "/json-ld/context.json":
                    fullpath = self.config.documentroot + "/rsrc/api/context.json"
                elif environ['PATH_INFO'] == "/var/terms":
                    fullpath = self.config.documentroot + "/rsrc/api/terms.json"
                elif environ['PATH_INFO'] == "/var/common":
                    fullpath = self.config.documentroot + "/rsrc/api/common.json"
            if os.path.isdir(fullpath):
                fullpath = fullpath + "index.html"
            if os.path.exists(fullpath):
                ext = os.path.splitext(fullpath)[1]
                # if not mimetypes.inited:
                #     mimetypes.init()
                mimetype = mimetypes.types_map.get(ext, 'text/plain')
                status = "200 OK"
                length = os.path.getsize(fullpath)
                fp = open(fullpath, "rb")
                iterdata = FileWrapper(fp)
            else:
                msg = """
<h1>404</h1>

The path %s not found at %s.

Examined %s repos.""" % (environ['PATH_INFO'],
                         fullpath,
                         len(self.repos))
                mimetype = "text/html"
                status = "404 Not Found"
                length = len(msg.encode('utf-8'))
                fp = BytesIO(msg.encode('utf-8'))
                iterdata = FileWrapper(fp)
        length = str(length)
        start_response(self._str(status), [
            (self._str("Content-Type"), self._str(mimetype)),
            (self._str("Content-Length"), self._str(length))
        ])
        return iterdata
        # FIXME: How can we make sure fp.close() is called, regardless of
        # whether it's a real fileobject or a BytesIO object?

    ################################################################
    # API Helper methods
    def stats(self, resultset=()):
        slices = OrderedDict()

        datadict = defaultdict(list)

        # 1: Create a giant RDF graph consisting of all triples of all
        #    repos' commondata. To avoid parsing the same RDF files
        #    over and over, this section duplicates the logic of
        #    DocumentRepository.commondata to make sure each RDF
        #    file is loaded only once.
        ttlfiles = set()
        resource_graph = Graph()
        namespaces = {}
        for repo in self.repos:
            for prefix, ns in repo.make_graph().namespaces():
                assert ns not in namespaces or namespaces[ns] == prefix, "Conflicting prefixes for ns %s" % ns
                namespaces[ns] = prefix
                resource_graph.bind(prefix, ns)
                for cls in inspect.getmro(repo.__class__):
                    if hasattr(cls, "alias"):
                        commonpath = "res/extra/%s.ttl" % cls.alias
                        if os.path.exists(commonpath):
                            ttlfiles.add(commonpath)
                        elif pkg_resources.resource_exists('ferenda', commonpath):
                            ttlfiles.add(pkg_resources.resource_filename('ferenda', commonpath))

        self.log.debug("stats: Loading resources %s into a common resource graph" %
                       list(ttlfiles))
        for filename in ttlfiles:
            resource_graph.parse(filename, format="turtle")
        pkg_resources.cleanup_resources()


        # 2: if used in the resultset mode, only calculate stats for those
        # resources/documents that are in the resultset.
        resultsetmembers = set()
        if resultset:
            for r in resultset:
                resultsetmembers.add(r['iri'])

        # 3: using each repo's faceted_data and its defined facet
        # selectors, create a set of observations for that repo
        # 
        # FIXME: If in resultset mode, we might ask a repo for its
        # faceted data and then use exactly none of it since it
        # doesn't match anything in resultsetmembers. We COULD analyze
        # common resultset iri prefixes and then only call
        # faceted_data for some (or one) repo.
        for repo in self.repos:
            data = repo.faceted_data()
            if resultsetmembers:
                data = [r for r in data if r['uri'] in resultsetmembers]

            for facet in repo.facets():
                if not facet.dimension_type:
                    continue
                dimension, obs = self.stats_slice(data, facet, resource_graph)
                if dimension in slices:
                    # since observations is a Counter not a regular
                    # dict, if slices[dimensions] and observations
                    # have common keys this will add the counts not
                    # replace them.
                    slices[dimension].update(obs)
                else:
                    slices[dimension] = obs

        # 4. Transform our easily-updated data structures to the list
        # of dicts of lists that we're supposed to return.
        res = {"type": "DataSet",
               "slices": []
               }
        for k, v in slices.items():
            observations = []
            for ok, ov in sorted(v.items()):
                observations.append({ok[0]: ok[1],
                                     "count": ov})
            res['slices'].append({"dimension": k,
                                  "observations": observations})
        return res

    def stats_slice(self, data, facet, resource_graph):
        binding = resource_graph.qname(facet.rdftype).replace(":", "_")
        if facet.dimension_label:
            dimension_label = facet.dimension_label
        elif self.config.legacyapi:
            dimension_label = util.uri_leaf(str(facet.rdftype))
        else:
            dimension_label = binding

        dimension_type = facet.dimension_type
        if (self.config.legacyapi and
                dimension_type == "value"):
            # legacyapi doesn't support the value type, we must
            # convert it into ref, and convert all string values to
            # fake resource ref URIs
            dimension_type = "ref"
            transformer = lambda x: (
                "http://example.org/fake-resource/%s" %
                x).replace(
                " ",
                "_")
        elif self.config.legacyapi and dimension_type == "term":
            # legacyapi expects "Standard" over "bibo:Standard", which is what
            # Facet.qname returns
            transformer = lambda x: x.split(":")[1]
        else:
            transformer = lambda x: x

        observations = Counter()
        # one file per uri+observation seen -- avoid
        # double-counting
        observed = {}
        for row in data:
            observation = None
            try:
                # maybe if facet.dimension_type == "ref", selector
                # should always be Facet.defaultselector?  NOTE:
                # we look at facet.dimension_type, not
                # dimension_type, as the latter may be altered if
                # legacyapi == True
                if facet.dimension_type == "ref":
                    observation = transformer(Facet.defaultselector(
                        row, binding))
                else:
                    observation = transformer(
                        facet.selector(
                            row,
                            binding,
                            resource_graph))

            except Exception as e:
                # most of the time, we should swallow this
                # exception since it's a selector that relies on
                # information that is just not present in the rows
                # from some repos. I think.
                if hasattr(facet.selector, 'im_self'):
                    # try to find the location of the selector
                    # function for easier debugging
                    fname = "%s.%s.%s" % (facet.selector.__module__,
                                          facet.selector.im_self.__name__,
                                          facet.selector.__name__)
                else:
                    # probably a lambda function
                    fname = facet.selector.__name__
                # FIXME: do we need the repo name here to provide useful
                # messages?
                # self.log.warning("facet %s (%s) fails for row %s : %s %s" % (binding, fname, row['uri'], e.__class__.__name__, str(e)))

                pass
            if observation is not None:
                k = (dimension_type, observation)
                if (row['uri'], observation) not in observed:
                    observed[(row['uri'], observation)] = True
                    observations[k] += 1
        return dimension_label, observations

    def query(self, environ):

        def _elements_to_html(elements):
            res = ""
            for e in elements:
                if isinstance(e, str):
                    res += e
                else:
                    res += '<em class="match">%s</em>' % str(e)
            return res
        idx = FulltextIndex.connect(self.config.indextype,
                                    self.config.indexlocation,
                                    self.repos)
        schema = idx.schema()
        q, param, pagenum, pagelen, stats = self.parse_parameters(
            environ['QUERY_STRING'], schema)
        res, pager = idx.query(q=q,
                               pagenum=pagenum,
                               pagelen=pagelen,
                               **param)
        # Mangle res into the expected JSON structure (see qresults.json)
        mangled = []
        for hit in sorted(res, key=itemgetter("uri"), reverse=True):
            mangledhit = {}
            for k, v in hit.items():
                if self.config.legacyapi:
                    if "_" in k:
                        # drop prefix (dcterms_issued -> issued)
                        k = k.split("_", 1)[1]
                if k == "uri":
                    k = "iri"
                if k == "text":
                    mangledhit["matches"] = {"text": _elements_to_html(hit["text"])}
                elif k in ("basefile", "repo"):
                    # these fields should not be included in results
                    pass
                else:
                    mangledhit[k] = v
            mangled.append(mangledhit)

        # 3.1 create container for results
        res = {"startIndex": pager['firstresult'] - 1,
               "itemsPerPage": int(param.get('_pageSize', '10')),
               "totalResults": pager['totalresults'],
               "duration": None,  # none
               "current": environ['PATH_INFO'] + "?" + environ['QUERY_STRING'],
               "items": mangled}

        # 4. add stats, maybe
        if stats:
            res["statistics"] = self.stats(mangled)
        return res

    def parse_parameters(self, querystring, schema):
        def _guess_real_fieldname(k, schema):
            for fld in schema:
                if fld.endswith(k):
                    return fld
            raise KeyError(
                "Couldn't find anything that endswith(%s) in fulltextindex schema" %
                k)

        if isinstance(querystring, bytes):
            # Assume utf-8 encoded URL -- when is this assumption
            # incorrect?
            querystring = querystring.decode("utf-8")

        param = dict(parse_qsl(querystring))
        filtered = dict([(k, v)
                         for k, v in param.items() if not (k.startswith("_") or k == "q")])
        # Range: some parameters have additional parameters, eg
        # "min-dcterms_issued=2014-01-01&max-dcterms_issued=2014-02-01"
        newfiltered = {}
        for k, v in list(filtered.items()):
            if k.startswith("min-") or k.startswith("max-"):
                op = k[:4]
                compliment = k.replace(op, {"min-": "max-",
                                            "max-": "min-"}[op])
                k = k[4:]
                if compliment in filtered:
                    start = filtered["min-" + k]
                    stop = filtered["max-" + k]
                    newfiltered[k] = fulltextindex.Between(datetime.strptime(start, "%Y-%m-%d"),
                                                           datetime.strptime(stop, "%Y-%m-%d"))
                else:
                    cls = {"min-": fulltextindex.More,
                           "max-": fulltextindex.Less}[op]
                    # FIXME: need to handle a greater variety of str->datatype conversions
                    v = datetime.strptime(v, "%Y-%m-%d")
                    newfiltered[k] = cls(v)
            elif k.startswith("year-"):
                # eg for year-dcterms_issued=2013, interpret as
                # Between(2012-12-31 and 2014-01-01)
                k = k[5:]
                newfiltered[k] = fulltextindex.Between(date(int(v) - 1, 12, 31),
                                                       date(int(v) + 1, 1, 1))
            else:
                newfiltered[k] = v
        filtered = newfiltered

        if self.config.legacyapi:
            # 2.3 legacyapi requires that parameters do not include
            # prefix. Therefore, transform publisher.iri =>
            # dcterms_publisher (ie remove trailing .iri and append a
            # best-guess prefix
            newfiltered = {}
            for k, v in filtered.items():
                if k.endswith(".iri"):
                    k = k[:-4]
                    # the parameter *looks* like it's a ref, but it should
                    # be interpreted as a value -- remove starting */ to
                    # get at actual querystring

                    # FIXME: in order to lookup k in schema, we may need
                    # to guess its prefix, but we're cut'n pasting the
                    # strategy from below. Unify.
                    if k not in schema and "_" not in k and k not in ("uri"):
                        k = _guess_real_fieldname(k, schema)

                    if v.startswith(
                            "*/") and not isinstance(schema[k], fulltextindex.Resource):
                        v = v[2:]
                if k not in schema and "_" not in k and k not in ("uri"):
                    k = _guess_real_fieldname(k, schema)
                    newfiltered[k] = v
                else:
                    newfiltered[k] = v
            filtered = newfiltered

        # 2.1 some values need to be converted, based upon the
        # fulltextindex schema.
        # if schema[k] == fulltextindex.Datetime, do strptime.
        # if schema[k] == fulltextindex.Boolean, convert 'true'/'false' to True/False.
        # if k = "rdf_type" and v looks like a qname or termname, expand v
        for k, fld in schema.items():
            # NB: Some values might already have been converted previously!
            if k in filtered and isinstance(filtered[k], str):
                if isinstance(fld, fulltextindex.Datetime):
                    filtered[k] = datetime.strptime(filtered[k], "%Y-%m-%d")
                elif isinstance(fld, fulltextindex.Boolean):
                    filtered[k] = (filtered[k] == "true")  # only "true" is True
                elif k == "rdf_type" and re.match("\w+:[\w\-_]+", filtered[k]):
                    # expand prefix ("bibo:Standard" -> "http://purl.org/ontology/bibo/")
                    (prefix, term) = re.match("(\w+):([\w\-_]+)", filtered[k]).groups()
                    for repo in self.repos:
                        if prefix in repo.ns:
                            filtered[k] = str(repo.ns[prefix]) + term
                            break
                    else:
                        self.log.warning("Can't map %s to full URI" % (filtered[k]))
                    pass
                elif k == "rdf_type" and self.config.legacyapi and re.match("[\w\-\_]+", filtered[k]):
                    filtered[k] = "*" + filtered[k]

        q = param['q'] if 'q' in param else None

        # find out if we need to get all results (needed when stats=on) or
        # just the first page
        if param.get("_stats") == "on":
            pagenum = 1
            pagelen = 10000 # this is the max that default ES 2.x will allow
            stats = True
        else:
            pagenum = int(param.get('_page', '0')) + 1
            pagelen = int(param.get('_pageSize', '10'))
            stats = False

        return q, filtered, pagenum, pagelen, stats

    def _str(self, s, encoding="ascii"):
        """If running under python2, return byte string version of the
        argument, otherwise return the argument unchanged.

        Needed since wsgiref under python 2 hates unicode.

        """
        if sys.version_info < (3, 0, 0):
            return s.encode("ascii")  # pragma: no cover
        else:
            return s
