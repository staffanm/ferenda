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
            Rule(self.config.searchendpoint, endpoint="search")
        ]
        if self.config.legacyapi:
            rules.append(Rule("/-/publ", endpoint="api"))
        for repo in self.repos:
            # a typical repo might provide two rules:
            # * Rule("/doc/<repo>/<basefile>", endpoint=repo.alias + ".doc")
            # * Rule("/dataset/<repo>?param1=x", endpoint=repo.alias + ".ds")
            # 
            # although werkzeug.routing.RuleTemplate seems like it could do that generically?
            rules.extend(repo.requesthandler.rules)
            # at this point, we could maybe write a apache:mod_rewrite
            # or nginx compatible config based on our rules?
        self.routingmap = Map(rules)
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
            rootlogger = self.setup_streaming_logger(writer)
            endpoint(request, start_response, **values)
            return [] #  an empty iterable -- we've already used the writer object to send our response
        else:
            res = endpoint(request, **values)
            if not isinstance(res, Response):
                res = Response(res) # set mimetype?
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


    def _search_run_query(self, queryparams, boost_types=None):
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
        for x in ('q', 'p'):
            if x in qpcopy:
                del qpcopy[x]
        res, pager = idx.query(query, pagenum=pagenum, boost_types=boost_types, **qpcopy)
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
                develurl=self.config.develurl)
        depth = len(doc.uri.split("/")) - 3
        tree = transformer.transform(xhtml, depth,
                                     uritransform=urltransform)
        return etree.tostring(tree, encoding="utf-8")


    def handle_api(self, request, **values):
        return Reponse("Hello API")


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
        return self.return_response(msg, start_response,
                                    status="500 Internal Server Error",
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

    def streaming_required(self, request):
        return request.args.get('stream', False)
