# a RequestHandler is part of a docrepo and responsible for
# determining if the docrepo can respond to a particular request, and
# for determining the physical path of the file corresponding to that
# request.

from wsgiref.util import request_uri
import re
import os
import sys
from io import BytesIO
from functools import partial
from urllib.parse import urlparse, unquote, parse_qsl
import mimetypes
import traceback

from rdflib import Graph
from ferenda.thirdparty import httpheader

from ferenda import util
from ferenda.errors import RequestHandlerError

class RequestHandler(object):
    
    _mimesuffixes = {'xhtml': 'application/xhtml+xml',
                     'rdf': 'application/rdf+xml',
                     'atom': 'application/atom+xml'}
    _rdfformats = {'application/rdf+xml': 'pretty-xml',
                   'text/turtle': 'turtle',
                   'application/n-triples': 'nt',
                   'application/json': 'json-ld'}
    _revformats = dict([(v, k) for k, v in _rdfformats.items()])
    _rdfsuffixes = {'rdf': 'pretty-xml',
                    'ttl': 'turtle',
                    'nt': 'nt',
                    'json': 'json-ld'}
    _mimemap = {'text/html': 'generated_path',
                'application/xhtml+xml': 'parsed_path',
                'application/rdf+xml': 'distilled_path'}
    _suffixmap = {'xhtml': 'parsed_path',
                  'rdf': 'distilled_path'}
    

    def __init__(self, repo):
        self.repo = repo

    def dataset_params_from_uri(self, uri):
        """Given a parametrized dataset URI, return the parameter and value
        used (or an empty tuple, if it is a dataset URI handled by
        this repo, but without any parameters).

        >>> d = DocumentRepository()
        >>> d.alias
        'base'
        >>> d.config.url = "http://example.org/"
        >>> d.dataset_params_from_uri("http://example.org/dataset/base?title=a")
        {"param": "title", "value": "a", "feed": False}
        >>> d.dataset_params_from_uri("http://example.org/dataset/base")
        {}

        >>> d.dataset_params_from_uri("http://example.org/dataset/base/feed/title")
        {"param": "title", "feed": True}
        """

        wantedprefix = self.repo.config.url + "dataset/" + self.repo.alias
        if (uri == wantedprefix or
            ("?" in uri and uri.startswith(wantedprefix)) or
            ("/feed" in uri and uri.startswith(wantedprefix))):
            
            path = uri[len(wantedprefix) + 1:]
            params = {}
            if path.startswith("feed"):
                params['feed'] = True
            if "=" in path:
                param, value = path.split("=", 1)
                params['param'] = param
                params['value'] = value
            return params
        # else return None (which is different from {})

    def basefile_params_from_basefile(self, basefile):
        if "?" not in basefile:
            return {}
        else:
            return dict(parse_qsl(basefile.split("?", 1)[1]))

    def supports(self, environ):
        """Returns True iff this particular handler supports this particular request."""
        segments = environ['PATH_INFO'].split("/", 3)
        # with PATH_INFO like /dataset/base.rdf, we still want the
        # alias to check to be "base", not "base.rdf"
        if len(segments) <= 2:
            return False
        reponame = segments[2]
        # this segment might contain suffix or parameters -- remove
        # them before comparison
        m = re.search('[^\.\?]*$', reponame)
        if m and m.start() > 0:
            reponame = reponame[:m.start()-1]
        return reponame == self.repo.alias

    def supports_uri(self, uri):
        return self.supports({'PATH_INFO': urlparse(uri).path})

    def path(self, uri):
        """Returns the physical path that the provided URI respolves
        to. Returns None if this requesthandler does not support the
        given URI, or the URI doesnt resolve to a static file.
        
        """
        suffix = None
        if urlparse(uri).path.startswith("/dataset/"):
            params = self.dataset_params_from_uri(uri)
            if ".atom" in uri:
                suffix = "atom"
                environ = {}
            else:
                environ = {"HTTP_ACCEPT": "text/html"}
            contenttype = self.contenttype(environ, uri, None, params, suffix)
            pathfunc = self.get_dataset_pathfunc(environ, params, contenttype, suffix)
            if pathfunc:
                return pathfunc()
            else:
                return None
        else:
            params = self.basefile_params_from_basefile(uri)
            if params:
                uri = uri.split("?")[0]
            basefile = self.repo.basefile_from_uri(uri)

            if basefile is None:
                return None
            if 'format' in params:
                suffix = params['format']
            else:
                if 'attachment' in params:
                    leaf = params['attachment']
                else:
                    leaf = uri.split("/")[-1]
                if "." in leaf:
                    suffix = leaf.rsplit(".", 1)[1]
        environ = {}
        if not suffix:
            environ['HTTP_ACCEPT'] = "text/html"
        contenttype = self.contenttype(environ, uri, basefile, params, suffix)
        pathfunc = self.get_pathfunc(environ, basefile, params, contenttype, suffix)
        if pathfunc:
            return pathfunc(basefile)


    def request_uri(self, environ):
        rawuri = request_uri(environ)
        uri = unquote(rawuri.encode("latin-1").decode("utf-8"))
        if getattr(self.repo.config, 'develurl', None):
            # in some circumstances, we might want to set develurl to
            # https://... while the actual uri provided will be
            # http://... (eg. due to TLS-terminating proxies and other
            # things), so we change the protocol of the request to
            # match the protocol as specified by config.develuri
            uriproto = uri.split("://")[0]
            develproto = self.repo.config.develurl.split("://")[0]
            if uriproto != develproto:
                uri = re.sub("^"+uriproto, develproto, uri)
            uri = uri.replace(self.repo.config.develurl, self.repo.config.url)
        if getattr(self.repo.config, 'acceptalldomains', False):
            # eg if the request_uri is http://localhost:8080/docs/1
            # (and config.develurl is not set or doesn't match this),
            # and config.url is https://example.org/, chnage
            # request_uri to https://example.org/docs/1
            uri = self.repo.config.url + uri.split("/", 3)[-1]
        return uri
        
    def handle(self, environ):
        """provides a response to a particular request by returning a a tuple
        *(fp, length, status, mimetype)*, where *fp* is an open file of the
        document to be returned.

        """
        segments = environ['PATH_INFO'].split("/", 3)
        uri = self.request_uri(environ)
        if "?" in uri:
            uri, querystring = uri.rsplit("?", 1)
        else:
            querystring = None
        suffix = None
        if segments[1] == "dataset":
            basefile = None
            tmpuri = uri
            if "." in uri.split("/")[-1]:
                tmpuri = tmpuri.rsplit(".", 1)[0]
            if querystring:
                tmpuri += "?" + querystring
            params = self.dataset_params_from_uri(tmpuri)
        else:
            basefile = self.repo.basefile_from_uri(uri)
            if not basefile:
                raise RequestHandlerError("%s couldn't resolve %s to a basefile" % (self.repo.alias, uri))
            if querystring:
                params = dict(parse_qsl(querystring))
            else:
                params = self.basefile_params_from_basefile(basefile)
        if 'format' in params:
            suffix = params['format']
        else:
            if 'attachment' in params:
                leaf = params['attachment']
            else:
                leaf = uri.split("/")[-1]
            if "." in leaf:
                suffix = leaf.rsplit(".", 1)[1]
        contenttype = self.contenttype(environ, uri, basefile, params, suffix)
        if segments[1] == "dataset":
            path, data = self.lookup_dataset(environ, params, contenttype, suffix)
        else:
            path, data = self.lookup_resource(environ, basefile, params,
                                              contenttype, suffix)
        return self.prep_request(environ, path, data, contenttype)
        

    def contenttype(self, environ, uri, basefile, params, suffix):
        accept = environ.get('HTTP_ACCEPT')
        preferred = None
        if accept:
            # do proper content-negotiation, but make sure
            # application/xhtml+xml ISN'T one of the available options (as
            # modern browsers may prefer it to text/html, and our
            # application/xhtml+xml isn't what they want) -- ie we only
            # serve application/xhtml+xml if a client specifically only
            # asks for that. Yep, that's a big FIXME.
            available = ("text/html")  # add to this?
            preferred = httpheader.acceptable_content_type(accept,
                                                           available,
                                                           ignore_wildcard=False)
        contenttype = None
        if accept != "text/html" and accept in self._mimemap:
            contenttype = accept
        elif suffix in self._mimesuffixes:
            contenttype = self._mimesuffixes[suffix]
        elif accept in self._rdfformats:
            contenttype = accept
        elif suffix in self._rdfsuffixes:
            contenttype = self._revformats[self._rdfsuffixes[suffix]]
        elif suffix and "."+suffix in mimetypes.types_map:
            contenttype = mimetypes.types_map["."+suffix]
        else:
            if ((not suffix) and
                    preferred and
                    preferred[0].media_type == "text/html"):
                contenttype = preferred[0].media_type
                # pathfunc = repo.store.generated_path
        return contenttype

    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        """Given the parameters, return a function that will, given a
        basefile, produce the proper path to that basefile. If the
        parameters indicate a version of the resource that does not
        exist as a static file on disk (like ".../basefile/data.rdf"),
        returns None

        """
        # try to lookup pathfunc from contenttype (or possibly suffix, or maybe params)
        if "repo" in params:
            # this must be a CompositeRepository that has the get_instance method
            for cls in self.repo.subrepos:
                if cls.alias == params['repo']:
                    repo = self.repo.get_instance(cls)
                    break
            else:
                raise ValueError("No '%s' repo is a subrepo of %s" %
                                 (params['repo'], self.repo.alias))
        else:
            repo = self.repo

        if "dir" in params:
            method = {'downloaded': repo.store.downloaded_path,
                      'intermediate': repo.store.intermediate_path,
                      'parsed': repo.store.parsed_path}[params["dir"]]
            if "page" in params and "format" in params:
                baseparam = "-size 400x300 -pointsize 12 -gravity center"
                baseattach = None
                try:
                    if "attachment" in params:
                        sourcefile = method(basefile, attachment=params["attachment"])
                    else:
                        sourcefile = method(basefile)

                    # we might run this on a host to where we haven't
                    # transferred the downloaded files -- try to
                    # re-aquire them now that someone wants to watch
                    # them.
                    if not os.path.exists(sourcefile):
                        repo.download(basefile)

                    assert params["page"].isdigit(), "%s is not a digit" % params["page"]
                    assert params["format"] in ("png", "jpg"), ("%s is not a valid image format" %
                                                                params["format"])
                    baseattach = "page_%s.%s" % (params["page"], params["format"])
                    if "attachment" in params:
                        baseattach = "%s_%s" % (params["attachment"], baseattach)
                    outfile = repo.store.intermediate_path(basefile, attachment=baseattach)
                    if not os.path.exists(outfile):
                        # params['page'] is 0-based, pdftoppm is 1-based
                        cmdline = "pdftoppm -f %s -singlefile -png %s %s" % (int(params["page"])+1, sourcefile, outfile.replace(".png",".tmp"))
                        util.runcmd(cmdline, require_success=True)
                        cmdline = "convert %s -trim %s" % (outfile.replace(".png", ".tmp.png"), outfile)
                        util.runcmd(cmdline, require_success=True)
                        os.unlink(outfile.replace(".png", ".tmp.png"))
                except Exception as e:
                    if not baseattach:
                        baseattach = "page_error.png"
                    outfile = repo.store.intermediate_path(basefile, attachment=baseattach)
                    errormsg = "%s\n%s: %s" % ("".join(traceback.format_tb(sys.exc_info()[2])), e.__class__.__name__, str(e))
                    errormsg = errormsg.replace("\n", "\\n").replace("'", "\\'")
                    cmdline = 'convert  label:"%s" %s' % (errormsg, outfile)
                    util.runcmd(cmdline, require_success=True)
                method = partial(repo.store.intermediate_path, attachment=baseattach)
                return method  # we really don't want to partial()
                               # this method again below
        elif contenttype in self._mimemap and not basefile.endswith("/data"):
            method = getattr(repo.store, self._mimemap[contenttype])
        elif suffix in self._suffixmap and not basefile.endswith("/data"):
            method = getattr(repo.store, self._suffixmap[suffix])
        elif "attachment" in params and mimetypes.guess_extension(contenttype):
            method = repo.store.generated_path
        else:
            # method = repo.store.generated_path
            return None

        if "attachment" in params:
            method = partial(method, attachment=params["attachment"])

        return method

    def get_dataset_pathfunc(self, environ, params, contenttype, suffix):
        suffix = {"text/html": "html",
                  "application/atom+xml": "atom"}.get(contenttype, None)
        if suffix:
            if params:
                if 'feed' in params:
                    if 'param' in params:
                        pseudobasefile = "feed/%s.%s" % (params['value'], suffix)
                    else:
                        pseudobasefile = "feed/main.%s" % suffix
                else:
                    pseudobasefile = "toc/%s/%s.html" % (params['param'], params['value'])
            else:
                pseudobasefile = "toc/index.html"
            return partial(self.repo.store.resourcepath, pseudobasefile)
        elif contenttype == "application/n-triples" or suffix == "nt":
            return partial(self.repo.store.resourcepath, "distilled/dump.nt")
        

    def lookup_resource(self, environ, basefile, params, contenttype, suffix):
        pathfunc = self.get_pathfunc(environ, basefile, params, contenttype, suffix)
        if not pathfunc:
            extended = False
            # no static file exists, we need to call code to produce data
            if basefile.endswith("/data"):
                extended = True
                basefile = basefile[:-5]
            if contenttype in self._rdfformats or suffix in self._rdfsuffixes:
                g = Graph()
                g.parse(self.repo.store.distilled_path(basefile))
                if extended:
                    annotation_graph = self.repo.annotation_file_to_graph(
                        self.repo.store.annotation_path(basefile))
                    g += annotation_graph
                path = None
            if contenttype in self._rdfformats:
                data = g.serialize(format=self._rdfformats[contenttype])
            elif suffix in self._rdfsuffixes:
                data = g.serialize(format=rdfsuffixes[suffix])
            else:
                data = None
        path = None
        if pathfunc:
            path = pathfunc(basefile)
            data = None
        return path, data

    def lookup_dataset(self, environ, params, contenttype, suffix):
        # FIXME: This should also make use of pathfunc
        data = None
        path = None
        suffix = {"text/html": "html",
                  "application/atom+xml": "atom"}.get(contenttype, None)
        if suffix:
            if params:
                if 'feed' in params:
                    if 'param' in params:
                        pseudobasefile = "feed/%s.%s" % (params['value'], suffix)
                    else:
                        pseudobasefile = "feed/main.%s" % suffix
                else:
                    pseudobasefile = "toc/%s/%s.html" % (params['param'], params['value'])
            else:
                pseudobasefile = "toc/index.html"
            path = self.repo.store.resourcepath(pseudobasefile)
        elif contenttype == "application/n-triples" or suffix == "nt":
            path = self.repo.store.resourcepath("distilled/dump.nt")
        elif contenttype in self._rdfformats or suffix in self._rdfsuffixes:
            g = Graph()
            g.parse(self.repo.store.resourcepath("distilled/dump.nt"),
                    format="nt")
            if contenttype in self._rdfformats:
                format = self._rdfformats[contenttype]
            else:
                format = self._rdfsuffixes[suffix]
            data = g.serialize(format=format)
        return path, data


    def prep_request(self, environ, path, data, contenttype):
        if path and os.path.exists(path):
            status = 200
            # FIXME: These are not terribly well designed flow control
            # mechanisms
            if path.endswith("page_error.png"):
                status = 500
            elif path.endswith(".404"):
                status = 404
            fp = open(path, 'rb')
            return (fp,
                    os.path.getsize(path),
                    status,
                    contenttype)
        elif data:
            return (BytesIO(data),
                    len(data),
                    200,
                    contenttype)
        else:
            msg = "<h1>406</h1>No acceptable media found for <tt>%s</tt>" % environ.get('HTTP_ACCEPT', 'text/html')
            return(BytesIO(msg.encode('utf-8')),
                   len(msg.encode('utf-8')),
                   406,
                   "text/html")


