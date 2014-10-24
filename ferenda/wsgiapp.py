# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import os
import sys
import json
from wsgiref.util import FileWrapper
import mimetypes
from operator import itemgetter
from datetime import date, datetime
import re

import six
from six.moves.urllib_parse import parse_qsl, urlencode
from six import text_type as str
from rdflib import URIRef, Namespace, Literal, Graph
from lxml import etree
from layeredconfig import LayeredConfig, Defaults, INIFile

from ferenda.compat import OrderedDict
from ferenda import DocumentRepository, FulltextIndex, Transformer, Facet
from ferenda import fulltextindex, util, elements
from ferenda.elements import html

class WSGIApp(object):
    """Implements a WSGI app.

    This class is not yet part of the public API -- clients should use
    manager.make_wsgi_app for now.

    """
    def __init__(self, repos, inifile=None, **kwargs):
        self.repos = repos
        # FIXME: need to specify documentroot?
        defaults = DocumentRepository().get_default_options()
        # NB: If both inifile and kwargs are specified, the latter
        # will take precedence. I think this is the expected
        # behaviour.
        defaults.update(kwargs)
        if inifile:
            assert os.path.exists(
                inifile), "INI file %s doesn't exist (relative to %s)" % (inifile, os.getcwd())

        self.config = LayeredConfig(Defaults(defaults),
                                    INIFile(inifile), cascade=True)

    ################################################################
    # Main entry point
    
    def __call__(self, environ, start_response):
        path = environ['PATH_INFO']
        # url = request_uri(environ)
        # FIXME: routing infrastructure -- could be simplified?
        if path.startswith(self.config.searchendpoint):
            return self.search(environ, start_response)
        elif (path.startswith(self.config.apiendpoint) or
              (self.config.legacyapi and path.startswith("/-/publ"))):
            return self.api(environ, start_response)
        else:
            return self.static(environ, start_response)

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
        conffile = os.sep.join([self.config.documentroot, 'rsrc', 'resources.xml'])
        transformer = Transformer('XSLT', "res/xsl/search.xsl", ["res/xsl"],
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

        data = json.dumps(dict(d), indent=4, default=util.json_default_date,
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
        for repo in self.repos:
            (fp, length, status, mimetype) = repo.http_handle(environ)  # and args?
            if fp:
                status = {200: "200 OK",
                          406: "406 Not Acceptable"}[status]
                iterdata = FileWrapper(fp)
                break
        # no repo handled the path
        if not fp:
            if self.config.legacyapi: # rewrite the path to some resources. FIXME:
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
                msg = "<h1>404</h1>The path %s not found at %s" % (environ['PATH_INFO'],
                                                                   fullpath)
                mimetype = "text/html"
                status = "404 Not Found"
                length = len(msg.encode('utf-8'))
                fp = six.BytesIO(msg.encode('utf-8'))
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
        res = {"type": "DataSet",
               "slices" : []
        }
        # 1. Fetch all the data we will need from all of the
        # repositories. While at it, collect all relevant facets,
        # namespace bindings and commondata as well.
        data = []
        facets = [] # will contain all unique facets
        qname_graph = Graph()
        resource_graph = Graph()
                        
        for repo in self.repos:
            for prefix, ns in repo.make_graph().namespaces():
                # print("repo %s: binding %s to %s" % (repo.alias, prefix, ns))
                qname_graph.bind(prefix, ns)
                resource_graph.bind(prefix, ns)
            resource_graph += repo.commondata
            repodata = repo.faceted_data()
            data.extend(repodata) # assume that no two repos ever have
                                  # data about the same URI
            for facet in repo.facets():
                if facet not in facets:
                    facets.append(facet)

        # if used in the resultset mode, only calculate stats for those
        # resources/documents that are in the resultset.
        if resultset:
            hits = {}
            for r in resultset:
                hits[r['iri']] = True
            data = [r for r in data if r['uri'] in hits]


        # 2. For each facet that makes sense (ie those that has .dimension
        # != None), collect the available observations and the count for
        # each
        for facet in facets:
            if not facet.dimension_type:
                continue
            binding = qname_graph.qname(facet.rdftype).replace(":", "_")
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
                transformer = lambda x: ("http://example.org/fake-resource/%s" % x).replace(" ", "_")
            elif self.config.legacyapi and dimension_type == "term":
                # legacyapi expects "Standard" over "bibo:Standard", which is what Facet.qname returns
                transformer = lambda x: x.split(":")[1]
            else:
                transformer = lambda x: x

            observations = {}
            observed = {} # one file per uri+observation  seen -- avoid
                          # double-counting
            for row in data:
                try:
                    # maybe if facet.dimension_type == "ref", selector
                    # should always be Facet.defaultselector?  NOTE:
                    # we look at facet.dimension_type, not
                    # dimension_type, as the latter may be altered if
                    # legacyapi == True
                    if facet.dimension_type == "ref":
                        observation = transformer(Facet.defaultselector(row, binding))
                    else:
                        observation = transformer(facet.selector(row, binding, resource_graph))

                    if not observation in observations:
                        observations[observation] = {dimension_type:observation,
                                                     "count":0}
                    if (row['uri'], observation) not in observed:
                        observed[(row['uri'], observation)] = True
                        observations[observation]["count"] += 1
                except:
                    pass
            res["slices"].append({"dimension": dimension_label,
                                  "observations": sorted(observations.values(), key=itemgetter(dimension_type))})
        return res
        
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
        q, param, pagenum, pagelen, stats = self.parse_parameters(environ['QUERY_STRING'], schema)
        res, pager = idx.query(q=q,
                               pagenum=pagenum,
                               pagelen=pagelen,
                               **param)

        # Mangle res into the expected JSON structure (see qresults.json)
        mangled = []
        for hit in res:
            mangledhit = {}
            for k, v in hit.items():
                if self.config.legacyapi:
                    if "_" in k:
                        # drop prefix (dcterms_issued -> issued)
                        k = k.split("_",1)[1]
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
               "duration": None, # none
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
            raise KeyError("Couldn't find anything that endswith(%s) in fulltextindex schema" % k)

        param = dict(parse_qsl(querystring))
        filtered = dict([(k,v) for k,v in param.items() if not (k.startswith("_") or k == "q")])
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
                    start = filtered["min-"+k]
                    stop = filtered["max-"+k]
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
                newfiltered[k] = fulltextindex.Between(date(int(v)-1, 12, 31),
                                                       date(int(v)+1, 1, 1))
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

                    if v.startswith("*/") and not isinstance(schema[k], fulltextindex.Resource):
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
                    filtered[k] = (filtered[k] == "true") # only "true" is True
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
            pagenum=1
            pagelen=100000
            stats = True
        else:
            pagenum=int(param.get('_page', '0'))+1
            pagelen=int(param.get('_pageSize', '10'))
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
        
