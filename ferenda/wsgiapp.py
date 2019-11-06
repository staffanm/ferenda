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
        return self.wsgi_app(environ, start_response)

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
        start_response(self._str(status), [
            (self._str("X-WSGI-app"), self._str("ferenda")),
            (self._str("Content-Type"), self._str(contenttype)),
            (self._str("Content-Length"), self._str("%s" % length)),
        ])
        
        if isinstance(data, Iterable) and not isinstance(data, bytes):
            # logging.getLogger("wsgi").info("returning data as-is")
            return data
        else:
            # logging.getLogger("wsgi").info("returning data as-iterable")
            return iter([data])

    #
    # ENDPOINTS
    # 


    def handle_search(self, request, **values):
        return Response("<h1>Hello search: " + request.args.get("q") +" </h1>", mimetype="text/html")

    def handle_api(self, request, **values):
        return Reponse("Hello API")


    #
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
