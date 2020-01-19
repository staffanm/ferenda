# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *
from future import standard_library
standard_library.install_aliases()

from collections import defaultdict, OrderedDict, Counter, Iterable
from datetime import date, datetime
from io import BytesIO
from operator import itemgetter
from wsgiref.util import FileWrapper, request_uri
from urllib.parse import parse_qsl, urlencode
import inspect
import json
import logging
import mimetypes
import os
import pkg_resources
import re
import sys
import traceback

from rdflib import URIRef, Namespace, Literal, Graph
from rdflib.namespace import DCTERMS
from lxml import etree
from layeredconfig import LayeredConfig, Defaults, INIFile
from werkzeug.wrappers import Request, Response
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.middleware.shared_data import SharedDataMiddleware
from werkzeug.utils import redirect
from werkzeug.wsgi import wrap_file

from ferenda import (DocumentRepository, FulltextIndex, Transformer,
                     Facet, ResourceLoader)
from ferenda import fulltextindex, util, elements
from ferenda.elements import html


class WSGIOutputHandler(logging.Handler):
    
    def __init__(self, writer):
        self.writer = writer
        super(WSGIOutputHandler, self).__init__()

    def emit(self, record):
        entry = self.format(record) + "\n"
        try:
            self.writer(entry.encode("utf-8"))
        except OSError as e:
            # if self.writer has closed, it probably means that the
            # HTTP client has closed the connection. But we don't stop
            # for that.
            pass

class WSGIApp(object):

    #
    # SETUP
    # 
    def __init__(self, repos, config):
        self.repos = repos
        self.config = config
        self.log = logging.getLogger("wsgi")
        # at this point, we should build our routing map
        rules = [
            Rule("/", endpoint="frontpage"),
            Rule(self.config.apiendpoint, endpoint="api"),
            Rule(self.config.apiendpoint+";stats", endpoint="api"),
            Rule(self.config.searchendpoint, endpoint="search")
        ]
        if self.config.legacyapi:
            rules.append(Rule("/-/publ", endpoint="api"))
        converters = []
        self.reporules = {}
        for repo in self.repos:
            # a typical repo might provide two rules:
            # * Rule("/doc/<repo>/<basefile>", endpoint=repo.alias + ".doc")
            # * Rule("/dataset/<repo>?param1=x", endpoint=repo.alias + ".ds")
            # 
            # although werkzeug.routing.RuleTemplate seems like it could do that generically?
            self.reporules[repo] = repo.requesthandler.rules
            rules.extend(self.reporules[repo])
            converters.extend(repo.requesthandler.rule_converters)
            # at this point, we could maybe write a apache:mod_rewrite
            # or nginx compatible config based on our rules?
        # from pprint import pprint
        # pprint(sorted(x.rule for x in rules))
        # import threading, traceback
        # print("Pid: %s, thread id: %s" % (os.getpid(), threading.get_ident()))
        # traceback.print_stack()
        self.routingmap = Map(rules, converters=dict(converters))
        base = self.config.datadir
        exports = {
            '/index.html': os.path.join(base, 'index.html'),
            '/rsrc':       os.path.join(base, 'rsrc'),
            '/robots.txt': os.path.join(base, 'robots.txt'),
            '/favicon.ico': os.path.join(base, 'favicon.ico')
        }
        if self.config.legacyapi:
            exports.extend({
                '/json-ld/context.json': os.path.join(base, 'rsrc/api/context.json'),
                '/var/terms':            os.path.join(base, 'rsrc/api/terms.json'),
                '/var/common':           os.path.join(base, 'rsrc/api/common.json')
                })
        self.wsgi_app = SharedDataMiddleware(self.wsgi_app, exports)

    def __call__(self, environ, start_response):
        try:
            return self.wsgi_app(environ, start_response)
        except Exception as e:
            if self.config.wsgiexceptionhandler:
                return self.handle_exception(environ, start_response)
            elif isinstance(e, HTTPException):
                return e.get_response(environ)(environ, start_response)
            else:
                raise e
            

    #
    # REQUEST ENTRY POINT
    # 
    def wsgi_app(self, environ, start_response):
        # due to nginx config issues we might have to add a bogus
        # .diff suffix to our path. remove it as early as possible,
        # before creating the (immutable) Request object
        if environ['PATH_INFO'].endswith(".diff"):
            environ['PATH_INFO'] = environ['PATH_INFO'][:-5]

        request = Request(environ)
        adapter = self.routingmap.bind_to_environ(request.environ)
        endpoint, values = adapter.match()
        if not callable(endpoint):
            endpoint = getattr(self, "handle_" + endpoint)
            
        if self.streaming_required(request):
            # at this point we need to lookup the route, but maybe not
            # create a proper Response object (which consumes the
            # start_response callable)
            content_type = 'application/octet-stream'
            # the second header disables nginx/uwsgi buffering so that
            # results are actually streamed to the client, see
            # http://nginx.org/en/docs/http/ngx_http_uwsgi_module.html#uwsgi_buffering
            writer = start_response('200 OK', [('Content-Type', content_type),
                                               ('X-Accel-Buffering', 'no'),
                                               ('X-Content-Type-Options', 'nosniff')])
            writer(b"")
            rootlogger = self.setup_streaming_logger(writer)
            try:
                endpoint(request, writer=writer, **values)
            except Exception as e:
                exc_type, exc_value, tb = sys.exc_info()
                tblines = traceback.format_exception(exc_type, exc_value, tb)
                msg = "\n".join(tblines)
                writer(msg.encode("utf-8"))
            finally:
                self.shutdown_streaming_logger(rootlogger)
                # ok we're done
            return [] #  an empty iterable -- we've already used the writer object to send our response
        else:
            res = endpoint(request, **values)
            if not isinstance(res, Response):
                res = Response(res) # set mimetype?
            res.headers["X-WSGI-App"] ="ferenda"
            # add X-WSGI-App: ferenda and possibly other data as well
            return res(environ, start_response)

    #
    # HELPERS
    # 

    def return_response(self, data, start_response, status="200 OK",
                         contenttype="text/html; charset=utf-8", length=None):
        if length is None:
            length = len(data)
        if contenttype == "text/html":
            # add explicit charset if not provided by caller (it isn't by default)
            contenttype = "text/html; charset=utf-8"
        # logging.getLogger("wsgi").info("Calling start_response")
        start_response(status, [
            ("X-WSGI-app", "ferenda"),
            ("Content-Type", contenttype),
            ("Content-Length", "%s" % length),
        ])
        
        if isinstance(data, Iterable) and not isinstance(data, bytes):
            return data
        else:
            return iter([data])

    #
    # ENDPOINTS
    # 

    def handle_frontpage(self, request, **values):
        # this handler would be unnecessary if we could make
        # SharedDataMiddleware handle it, but it seems like its lists
        # of exports is always just the prefix of a path, not the
        # entire path, so we can't just say that "/" should be handled
        # by it.
        fp = open(os.path.join(self.config.datadir, "index.html"))
        return Response(wrap_file(request.environ, fp), mimetype="text/html")

    def handle_search(self, request, **values):
        # return Response("<h1>Hello search: " + request.args.get("q") +" </h1>", mimetype="text/html")
        res, pager = self._search_run_query(request.args)
        
        if pager['totalresults'] == 1:
            title = "1 match"
        else:
            title = "%s matches" % pager['totalresults']
        title += " for '%s'" % request.args.get("q")
        
        body = html.Body()
        for r in res:
            if not 'dcterms_title' in r or r['dcterms_title'] is None:
                r['dcterms_title'] = r['uri']
            if r.get('dcterms_identifier', False):
                r['dcterms_title'] = r['dcterms_identifier'] + ": " + r['dcterms_title']
            body.append(html.Div(
                [html.H2([elements.Link(r['dcterms_title'], uri=r['uri'])]),
                 r.get('text', '')], **{'class': 'hit'}))
        pagerelem = self._search_render_pager(pager, dict(request.args), request.path)
        body.append(html.Div([
            html.P(["Results %(firstresult)s-%(lastresult)s "
                    "of %(totalresults)s" % pager]), pagerelem],
                                 **{'class':'pager'}))
        data = self._transform(title, body, request.environ, template="xsl/search.xsl")
        return Response(data, mimetype="text/html")

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
            resource_graph.parse(data=util.readfile(filename), format="turtle")
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
        for k, v in sorted(slices.items()):
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

    def query(self, request, options=None):
        # this is needed -- but the connect call shouldn't neccesarily
        # have to call exists() (one HTTP call)
        idx = FulltextIndex.connect(self.config.indextype,
                                    self.config.indexlocation,
                                    self.repos)
        # parse_parameters -> {
        #  "q": "freetext",
        #  "fields": {"dcterms_publisher": ".../org/di",
        #             "dcterms_issued": "2018"}
        #  "pagenum": 1,
        #  "pagelen": 10,
        #  "autocomplete": False,
        #  "exclude_repos": ["mediawiki"],
        #  "boost_repos": [("sfs", 10)],
        #  "include_fragments": False
        # }
        if options is None:
            options = {}
        options.update(self.parse_parameters(request, idx))
        res, pager = idx.query(q=options.get("q"),
                               pagenum=options.get("pagenum"),
                               pagelen=options.get("pagelen"),
                               ac_query=options.get("autocomplete"),
                               exclude_repos=options.get("exclude_repos"),
                               boost_repos=options.get("boost_repos"),
                               include_fragments=options.get("include_fragments"),
                               **options.get("fields"))
        mangled = self.mangle_results(res, options.get("autocomplete"))
        # 3.1 create container for results
        res = {"startIndex": pager['firstresult'] - 1,
               "itemsPerPage": options["pagelen"],
               "totalResults": pager['totalresults'],
               "duration": None,  # none
               "current": request.path + "?" + request.query_string.decode("utf-8"),
               "items": mangled}

        # 4. add stats, maybe
        if options["stats"]:
            res["statistics"] = self.stats(mangled)

        # 5. possibly trim results for easier json consumption
        if options["autocomplete"]:
            res = res["items"]
        return res


    def mangle_results(self, res, ac_query):
        def _elements_to_html(elements):
            res = ""
            for e in elements:
                if isinstance(e, str):
                    res += e
                else:
                    res += '<em class="match">%s</em>' % str(e)
            return res

        # Mangle res into the expected JSON structure (see qresults.json)
        if ac_query:
            # when doing an autocomplete query, we want the relevance order from ES
            hiterator = res
        else:
            # for a regular API query, we need another order (I forgot exactly why...)
            hiterator = sorted(res, key=itemgetter("uri"), reverse=True)
        mangled = []
        for hit in hiterator:
            mangledhit = {}
            for k, v in hit.items():
                if self.config.legacyapi:
                    if "_" in k:
                        # drop prefix (dcterms_issued -> issued)
                        k = k.split("_", 1)[1]
                    elif k == "innerhits":
                        continue  # the legacy API has no support for nested/inner hits
                if k == "uri":
                    k = "iri"
                    # change eg https://lagen.nu/1998:204 to
                    # http://localhost:8080/1998:204 during
                    # development
                    if v.startswith(self.config.url) and 'develurl' in self.config:
                        v = v.replace(self.config.url, self.config.develurl)
                if k == "text":
                    mangledhit["matches"] = {"text": _elements_to_html(hit["text"])}
                elif k in ("basefile", "repo"):
                    # these fields should not be included in results
                    pass
                else:
                    mangledhit[k] = v
            mangledhit = self.mangle_result(mangledhit, ac_query)
            mangled.append(mangledhit)
        return mangled

    def mangle_result(self, hit, ac_query=False):
        return hit

    def parse_parameters(self, request, idx):
        def _guess_real_fieldname(k, schema):
            for fld in schema:
                if fld.endswith(k):
                    return fld
            raise KeyError(
                "Couldn't find anything that endswith(%s) in fulltextindex schema" %
                k)

        param = request.args.to_dict()
        filtered = dict([(k, v)
                         for k, v in param.items() if not (k.startswith("_") or k == "q")])
        if filtered:
            # OK, we have some field parameters. We need to get at the
            # current schema to know how to process some of these and
            # convert them into fulltextindex.SearchModifier objects
            
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

            schema = idx.schema()
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
                        # get at actual value

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

        options = {
            "q": param.get("q"),
            "stats": param.get("_stats") == "on",
            "autocomplete": param.get("_ac") == "true",
            "fields": filtered
        }
        # find out if we need to get all results (needed when stats=on) or
        # just the first page
        if options["stats"]:
            options["pagenum"] = 1
            options["pagelen"] = 10000 # this is the max that default ES 2.x will allow
        else:
            options["pagenum"] = int(param.get('_page', '0')) + 1
            options["pagelen"] = int(param.get('_pageSize', '10'))
        return options

    def _search_run_query(self, queryparams, boost_repos=None):
        idx = FulltextIndex.connect(self.config.indextype,
                                    self.config.indexlocation,
                                    self.repos)
        query = queryparams.get('q')
        if isinstance(query, bytes):  # happens on py26
            query = query.decode("utf-8")  # pragma: no cover
#        query += "*"  # we use a simple_query_string query by default,
#                      # and we probably want to do a prefix query (eg
#                      # "personuppgiftslag" should match a label field
#                      # containing "personuppgiftslag (1998:204)",
#                      # therefore the "*"
#
#        # maybe not, though -- seems to conflict with
#        # stemming/indexing, ie "bulvanutredningen*" doesn't match the
#        # indexed "bulvanutredningen" (which has been stemmed to
#        # "bulvanutredning"
        pagenum = int(queryparams.get('p', '1'))
        qpcopy = dict(queryparams)
        # we've changed a parameter name in our internal API:s from
        # "type" to "repo" since ElasticSearch 7.x doesn't have types
        # anymore (and the corresponding data is now stored in a
        # "repo" field), but we haven't changed our URL parameters
        # (yet). In the meantime, map the external type parameter to
        # the internal repo parameter
        if 'type' in qpcopy:
            qpcopy["repo"] = qpcopy.pop("type")
        for x in ('q', 'p'):
            if x in qpcopy:
                del qpcopy[x]
        res, pager = idx.query(query, pagenum=pagenum, boost_repos=boost_repos, **qpcopy)
        return res, pager

    def _search_render_pager(self, pager, queryparams, path_info):
        # Create some HTML code for the pagination. FIXME: This should
        # really be in search.xsl instead
        pages = []
        pagenum = pager['pagenum']
        startpage = max([0, pager['pagenum'] - 4])
        endpage = min([pager['pagecount'], pager['pagenum'] + 3])
        if startpage > 0:
            queryparams['p'] = str(pagenum - 2)
            url = path_info + "?" + urlencode(queryparams)
            pages.append(html.LI([html.A(["«"], href=url)]))

        for pagenum in range(startpage, endpage):
            queryparams['p'] = str(pagenum + 1)
            url = path_info + "?" + urlencode(queryparams)
            attrs = {}
            if pagenum + 1 == pager['pagenum']:
                attrs['class'] = 'active'
            pages.append(html.LI([html.A([str(pagenum + 1)], href=url)],
                                 **attrs))

        if endpage < pager['pagecount']:
            queryparams['p'] = str(pagenum + 2)
            url = path_info + "?" + urlencode(queryparams)
            pages.append(html.LI([html.A(["»"], href=url)]))

        return html.UL(pages, **{'class': 'pagination'})

    def _transform(self, title, body, environ, template="xsl/error.xsl"):
        fakerepo = self.repos[0]
        doc = fakerepo.make_document()
        doc.uri = request_uri(environ)
        doc.meta.add((URIRef(doc.uri),
                      DCTERMS.title,
                      Literal(title, lang="sv")))
        doc.body = body
        xhtml = fakerepo.render_xhtml_tree(doc)
        conffile = os.sep.join([self.config.datadir, 'rsrc',
                                'resources.xml'])
        transformer = Transformer('XSLT', template, "xsl",
                                  resourceloader=fakerepo.resourceloader,
                                  config=conffile)
        urltransform = None
        if 'develurl' in self.config:
            urltransform = fakerepo.get_url_transform_func(
                repos=self.repos, develurl=self.config.develurl,wsgiapp=self)
        depth = len(doc.uri.split("/")) - 3
        tree = transformer.transform(xhtml, depth,
                                     uritransform=urltransform)
        return etree.tostring(tree, encoding="utf-8")


    def handle_api(self, request, **values):
        if request.path.endswith(";stats"):
            d = self.stats()
        else:
            d = self.query(request)
        data = json.dumps(d, indent=4, default=util.json_default_date,
                          sort_keys=True).encode('utf-8')
        return Response(data, content_type="application/json")


    exception_heading = "Something is broken"
    exception_description = "Something went wrong when showing the page. Below is some troubleshooting information intended for the webmaster."
    def handle_exception(self, environ, start_response):
        import traceback
        from pprint import pformat
        exc_type, exc_value, tb = sys.exc_info()
        tblines = traceback.format_exception(exc_type, exc_value, tb)
        tbstr = "\n".join(tblines)
        # render the error
        title = tblines[-1]
        body = html.Body([
            html.Div([html.H1(self.exception_heading),
                      html.P([self.exception_description]),
                      html.H2("Traceback"),
                      html.Pre([tbstr]),
                      html.H2("Variables"),
                      html.Pre(["request_uri: %s\nos.getcwd(): %s" % (request_uri(environ), os.getcwd())]),
                      html.H2("environ"),
                      html.Pre([pformat(environ)]),
                      html.H2("sys.path"),
                      html.Pre([pformat(sys.path)]),
                      html.H2("os.environ"),
                      html.Pre([pformat(dict(os.environ))])
        ])])
        msg = self._transform(title, body, environ)
        if isinstance(exc_value, HTTPException):
            status = "%s %s" % (exc_value.code, exc_value.name)
        else:
            status = "500 Server error"
        return self.return_response(msg, start_response,
                                    status,
                                    contenttype="text/html")


    # STREAMING
    # 
        
    def setup_streaming_logger(self, writer):
        # these internal libs use logging to log things we rather not disturb the user with
        for logname in ['urllib3.connectionpool',
                        'chardet.charsetprober',
                        'rdflib.plugins.parsers.pyRdfa']:
            log = logging.getLogger(logname)
            log.propagate = False

        wsgihandler = WSGIOutputHandler(writer)
        wsgihandler.setFormatter(
            logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s",
                 datefmt="%H:%M:%S"))
        rootlogger = logging.getLogger()
        rootlogger.setLevel(logging.DEBUG)
        for handler in rootlogger.handlers:
            rootlogger.removeHandler(handler)
        logging.getLogger().addHandler(wsgihandler)
        return rootlogger

    def shutdown_streaming_logger(self, rootlogger):
        for h in list(rootlogger.handlers):
            if isinstance(h, WSGIOutputHandler):
                h.close()
                rootlogger.removeHandler(h)

    def streaming_required(self, request):
        return request.args.get('stream', False)
