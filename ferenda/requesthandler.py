# a RequestHandler is part of a docrepo and responsible for
# determining if the docrepo can respond to a particular request, and
# for determining the physical path of the file corresponding to that
# request.

from wsgiref.util import request_uri
import re
import os
from io import BytesIO
from functools import partial
from urllib.parse import urlparse, unquote, parse_qsl
import mimetypes

from rdflib import Graph
from ferenda.thirdparty import httpheader

from ferenda import util
from ferenda.errors import RequestHandlerError

class RequestHandler(object):
    
    _mimesuffixes = {'xhtml': 'application/xhtml+xml',
                     'rdf': 'application/rdf+xml'}
    _rdfformats = {'application/rdf+xml': 'pretty-xml',
                   'text/turtle': 'turtle',
                   'text/plain': 'nt',
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
            params = self.repo.dataset_params_from_uri(uri)
            environ = {"HTTP_ACCEPT": "text/html"}
            contenttype = self.contenttype(environ, uri, None, params, suffix)
            pathfunc = self.get_dataset_pathfunc(environ, params, contenttype, suffix)
            if pathfunc:
                return pathfunc()
            else:
                return None
        else:
            params = self.repo.basefile_params_from_basefile(uri)
            if params:
                uri = uri.split("?")[0]
            basefile = self.repo.basefile_from_uri(uri)

            if isinstance(params, dict) and 'format' in params:
                suffix = params['format']
            else:
                if isinstance(params, dict) and 'attachment' in params:
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
        if 'develurl' in self.repo.config:
            uri = uri.replace(self.repo.config.develurl, self.repo.config.url)
        return uri
        
    def handle(self, environ):
        """provides a response to a particular request by returning a a tuple
        *(fp, length, memtype)*, where *fp* is an open file of the
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
                tmpuri = tmpuri.rsplit(".")[0]
            if querystring:
                tmpuri += "?" + querystring
            params = self.repo.dataset_params_from_uri(tmpuri)
        else:
            basefile = self.repo.basefile_from_uri(uri)
            if not basefile:
                raise RequestHandlerError("%s couldn't resolve %s to a basefile" % (self.repo.alias, uri))
            if querystring:
                params = dict(parse_qsl(querystring))
            else:
                params = self.repo.basefile_params_from_basefile(basefile)
        if isinstance(params, dict) and 'format' in params:
            suffix = params['format']
        else:
            if isinstance(params, dict) and 'attachment' in params:
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
                                                           available)
        contenttype = None
        if accept in self._mimemap:
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
                                 (param['repo'], self.repo.alias))
        else:
            repo = self.repo

        if "dir" in params:
            method = {'downloaded': repo.store.downloaded_path,
                      'parsed': repo.store.parsed_path}[params["dir"]]
            if "page" in params and "format" in params:
                baseparam = "-size 400x300 -pointsize 12 -gravity center"
                baseattach = None
                try:
                    if "attachment" in params:
                        sourcefile = method(basefile, attachment=params["attachment"])
                    else:
                        sourcefile = method(basefile)
                    assert params["page"].isdigit(), "%s is not a digit" % params["page"]
                    assert params["format"] in ("png", "jpg"), ("%s is not a valid image format" %
                                                                params["format"])
                    baseattach = "page_%s.%s" % (params["page"], params["format"])
                    if "attachment" in params:
                        baseattach = "%s_%s" % (params["attachment"], baseattach)
                    outfile = repo.store.intermediate_path(basefile, attachment=baseattach)
                    if not os.path.exists(outfile):
                        cmdline = "convert %s[%s] %s" % (sourcefile, params["page"], outfile)
                        util.runcmd(cmdline, require_success=True)
                except Exception as e:
                    if not baseattach:
                        baseattach = "page_error.png"
                    errormsg = str(e).replace("\n", "\\n")
                    cmdline = "convert  label:'%s' %s" % (errormsg, outfile)
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
        if contenttype == "text/html":
            if params:
                pseudobasefile = "/".join(params)
            else:
                pseudobasefile = "index"
            return partial(self.repo.store.resourcepath, "toc/%s.html" % pseudobasefile)
        elif contenttype == "text/plain" or suffix == "nt":
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
        if contenttype == "text/html":
            if params:
                pseudobasefile = "/".join(params)
            else:
                pseudobasefile = "index"
            path = self.repo.store.resourcepath("toc/%s.html" % pseudobasefile)
        elif contenttype == "text/plain" or suffix == "nt":
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
            fp = open(path, 'rb')
            return (fp,
                    os.path.getsize(path),
                    200,
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


