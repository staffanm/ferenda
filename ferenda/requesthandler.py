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
from copy import deepcopy

from lxml import etree
from rdflib import Graph
from cached_property import cached_property
from werkzeug.routing import Rule, BaseConverter, Map
from werkzeug.datastructures import Headers
from werkzeug.wrappers import Request, Response
from werkzeug.wsgi import wrap_file
from werkzeug.exceptions import NotAcceptable, Forbidden
from werkzeug.test import EnvironBuilder

from ferenda import util
from ferenda.errors import RequestHandlerError
from ferenda.thirdparty.htmldiff import htmldiff

class UnderscoreConverter(BaseConverter):
    def to_url(self, value):
        return value.replace(" ", "_")
    def to_python(self, value):
        return value.replace("_", " ")

class BasefileRule(Rule):
    # subclass that takes extra care to handle urls ending in
    # /data[.suffix]
    def match(self, path, method=None):
        m = re.search("/data(|.\w+)$", path)
        if m:
            assert m.start() #  shoudn't be zero
            path = path[:m.start()]
            if m.group(1):
                path += m.group(1)
        if 'extended' in self._converters: 
            # this is SO hacky, but in order to match, we remove the
            # troublesome <extended> part of the URI rule regex before
            # calling the superclass, then restore the regex
            # afterwards
            real_regex = self._regex
            self._regex = re.compile(self._regex.pattern.replace("/(?P<extended>(?:data))", ""))
        res = super(BasefileRule, self).match(path, method)
        if res and m:
            if 'extended' in self._converters:
                self._regex = real_regex
            res['extended'] = 'data'
            # if 'suffix' in self._converters and m.groups(1):
            #     res['suffix'] = m.groups(1)[1:]
        # if <extended or <suffix> converters are defined, fill that data
        return res

            

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

    # FIXME: This shouldn't be used as the data should be fetched from the routing rules
    # , but since it's called from path() which may be called in a
    # non-wsgi context, we might not
    def dataset_params_from_uri(self, uri):
        assert False, "No!"

    @property
    def rules(self):
        # things to handle
        # /res/repo/mybasefile  # that may or may not contain slashes like "prop/1998/99:14"
        # /res/repo/mybasefile.suffix
        # /res/repo/mybasefile/data
        # /res/repo/mybasefile/data.suffix
        # /dataset/repo
        # /dataset/repo.suffix
        # /dataset/repo/feed # with or without parameters like "?rdf_type=type/forordning"
        #   -- werkzeug.routing does not process this query string
        # /dataset/repo/feed.suffix # with or without parameters
        context = self.rule_context
        rules = []
        for root in self.doc_roots:
            context["root"] = root
            for template in self.doc_rules:
                rules.append(BasefileRule(template % context, endpoint=self.handle_doc))
        for root in self.dataset_roots:
            context["root"] = root
            for template in self.dataset_rules:
                rules.append(Rule(template % context, endpoint=self.handle_dataset))
        return rules

    @property
    def rule_context(self):
        return {"converter": "path"} 

    @property
    def doc_roots(self):
        return ["/res/%s" % self.repo.alias]

    @property
    def doc_rules(self):
        return ["%(root)s/<%(converter)s:basefile>",
                "%(root)s/<%(converter)s:basefile>.<suffix>",
                "%(root)s/<%(converter)s:basefile>/<any(data):extended>",
                "%(root)s/<%(converter)s:basefile>/<any(data):extended>.<suffix>"]

    @property
    def dataset_roots(self):
        return ["/dataset/%s" % self.repo.alias]

    @property
    def dataset_rules(self):
        return ["%(root)s",
                "%(root)s.<suffix>",
                "%(root)s/<any(feed):feed>",
                "%(root)s/<any(feed):feed>.<suffix>"]
    
    @property
    def rule_converters(self):
        return ()

    def handle_doc(self, request, **params):
        # request.url is the reconstructed URL used in the request,
        # request.base_url is the same without any query string
        assert 'basefile' in params ,"%s couldn't resolve %s to a basefile" % (
            self.repo.alias, request.base_url)
        params.update(request.args.to_dict())
        # params = self.params_from_uri(request.url)
        # params['basefile'] = self.repo.basefile_from_uri(request.url)
        if 'attachment' in params and 'suffix' not in params:
            params['suffix'] = params['attachment'].split(".")[-1]
        contenttype = self.contenttype(request, params.get('suffix', None))
        path, data = self.lookup_resource(request.headers, params['basefile'], params, contenttype, params.get('suffix', None))
        return self.prep_response(request, path, data, contenttype, params)

    def handle_dataset(self, request, **params):
        assert len(request.args) <= 1, "Can't handle dataset requests with multiple selectors"
        for (k, v) in request.args.items():
            params["param"] = k
            params["value"] = v
        contenttype = self.contenttype(request, params.get("suffix", None))
        path, data = self.lookup_dataset(request.headers, params, contenttype, params.get("suffix", None))
        return self.prep_response(request, path, data, contenttype, params)

#    def supports(self, environ):
#        """Returns True iff this particular handler supports this particular request."""
#        segments = environ['PATH_INFO'].split("/", 3)
#        # with PATH_INFO like /dataset/base.rdf, we still want the
#        # alias to check to be "base", not "base.rdf"
#        if len(segments) <= 2:
#            return False
#        reponame = segments[2]
#        # this segment might contain suffix or parameters -- remove
#        # them before comparison
#        m = re.search('[^\.\?]*$', reponame)
#        if m and m.start() > 0:
#            reponame = reponame[:m.start()-1]
#        return reponame == self.repo.alias
#
#    def supports_uri(self, uri):
#        return self.supports({'PATH_INFO': urlparse(uri).path})
#
    def path(self, uri):
        """Returns the physical path that the provided URI respolves
        to. Returns None if this requesthandler does not support the
        given URI, or the URI doesnt resolve to a static file.
        
        """
        suffix = None
        parsedurl = urlparse(uri)
        args = dict(parse_qsl(parsedurl.query))
        map = Map(self.rules, converters=self.rule_converters)
        endpoint, params = map.bind(server_name=parsedurl.netloc.split(":")[0],
                                    path_info=parsedurl.path).match()
        if endpoint == self.handle_dataset:
            # FIXME: This duplicates logic from handle_dataset
            assert len(args) <= 1, "Can't handle dataset requests with multiple selectors"
            for (k, v) in args.items():
                params["param"] = k
                params["value"] = v
            # at this point, use werkzeug.test.Client or
            # EnvironmentBuilder to create a fake environ and then a
            # fake Request object
            if ".atom" in uri:
                suffix = "atom"
                path = "/index.atom"
                headers = {}
            else:
                headers = {"Accept": "text/html"}
                path = "/index.html"
            environ = EnvironBuilder(path=path, headers=headers).get_environ()
            contenttype = self.contenttype(Request(environ), suffix)
            pathfunc = self.get_dataset_pathfunc(environ, params, contenttype, suffix)
            if pathfunc:
                return pathfunc()
            else:
                return None
        elif endpoint == self.handle_doc:
            # params = self.params_from_uri(uri)
            # if params:
            params.update(args)

            if 'basefile' not in params:
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

        if not suffix:
            headers = {'Accept': 'text/html'}
        else:
            headers = {}
        environ = EnvironBuilder(path=urlparse(uri).path, headers=headers).get_environ()
        contenttype = self.contenttype(Request(environ), suffix)
        pathfunc = self.get_pathfunc(environ, params['basefile'], params, contenttype, suffix)
        if pathfunc:
            return pathfunc(params['basefile'])

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

    def contenttype(self, request, suffix):
        preferred = request.accept_mimetypes.best_match(["text/html"])
        accept = request.headers.get("Accept")
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
            if (not suffix and preferred == "text/html"):
                contenttype = preferred
        return contenttype

    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        """Given the parameters, return a function that will, given a
        basefile, produce the proper path to that basefile. If the
        parameters indicate a version of the resource that does not
        exist as a static file on disk (like ".../basefile/data.rdf"),
        returns None

        """
        if "extended" in params:
            # by definition, this means that we don't have a static file on disk
            return None
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
                # check if this is a robot we need to ban (we try to
                # ban them through robots.txt but not all are well
                # behaved)
                if getattr(self.repo.config, 'imagerobots', None):
                    if re.search(self.repo.config.imagerobots, environ.get("User-Agent")):
                        raise Forbidden()
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
                        logfile = self.repo.config._parent.datadir + os.sep + "ua.log"
                        with open(logfile, "a") as fp:
                            fp.write("%s\t%s\t%s\n" % (outfile, environ.get("User-Agent"), environ.get("Referer")))
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
        elif "version" in params:
            method = partial(repo.store.generated_path, version=params["version"])
        elif "diff" in params and params.get("from") != "None":
            return None
        elif contenttype in self._mimemap:
            method = getattr(repo.store, self._mimemap[contenttype])
        elif suffix in self._suffixmap:
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
        
    # FIXME: basefile and suffix is now part of the params dict 
    def lookup_resource(self, environ, basefile, params, contenttype, suffix):
        pathfunc = self.get_pathfunc(environ, basefile, params, contenttype, suffix)
        if not pathfunc:
            # no static file exists, we need to call code to produce data
            if contenttype in self._rdfformats or suffix in self._rdfsuffixes:
                g = Graph()
                g.parse(self.repo.store.distilled_path(basefile))
                if 'extended' in params:
                    if os.path.exists(self.repo.store.annotation_path(basefile)):
                        annotation_graph = self.repo.annotation_file_to_graph(
                            self.repo.store.annotation_path(basefile))
                        g += annotation_graph
                path = None
            if contenttype in self._rdfformats:
                data = g.serialize(format=self._rdfformats[contenttype])
            elif suffix in self._rdfsuffixes:
                data = g.serialize(format=rdfsuffixes[suffix])
            elif 'diff' in params and params.get('from') != "None":
                data = self.diff_versions(basefile, params.get('from'), params.get('to'))
            else:
                data = None
        path = None
        if pathfunc:
            path = pathfunc(basefile)
            data = None
        return path, data

    def diff_versions(self, basefile, from_version, to_version):
        def cleantree(tree, savednodes=None):
            for xpath, save in (("//div[@class='docversions']", False),
                                ("//div[@role='tablist']", True)):
                for node in tree.xpath(xpath):
                    parent = node.getparent()
                    if save and savednodes is not None:
                        savednodes[parent.get("about")] = node
                    parent.remove(node)
            return tree
        # 1 load the from_version, cleaning away some parts that we won't diff
        from_tree = cleantree(etree.parse(
            self.repo.store.generated_path(basefile, version=from_version)))

        # 2 load the to_version, making a deep copy to be used for the
        # final template, then cleaning awy the same parts as for the
        # from_version, but storing these parts for later use.
        to_tree = etree.parse(
            self.repo.store.generated_path(basefile, version=to_version))
        template_tree = deepcopy(to_tree)
        savednodes = {}
        to_tree = cleantree(to_tree, savednodes=savednodes)

        # 3 extract doc areas to be diffed (maybe cleantree should do this?)
        from_area = from_tree.find("//article")
        to_area = to_tree.find("//article")

        # 4 diff the content areas
        diffstr = '<article class="col-sm-9">' + htmldiff(from_area, to_area, include_hrefs=False) + '</article>'
        diff_tree = etree.HTML(diffstr)[0][0]
        # 5 re-insert the stored-away parts
        for parent in to_area.xpath("//div[@class='row']"):
            if parent.get("about") in savednodes:
                # the saved nodes always appear last amongst its
                # siblings, so we can always just append to the parent
                parent.append(savednodes[parent.get("about")])
        
        # 6 insert resunt into doc area for 1
        area = template_tree.find("//article")
        area.getparent().replace(area, diff_tree)
        return etree.tostring(template_tree)

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


    def prep_response(self, request, path, data, contenttype, params):
        if path and os.path.exists(path):
            status = 200
            # FIXME: These are not terribly well designed flow control
            # mechanisms
            if path.endswith("page_error.png"):
                status = 500
            elif path.endswith(".404"):
                status = 404
            fp = wrap_file(request.environ, open(path, 'rb'))
            headers = Headers({"Content-length": os.path.getsize(path)})
        elif data:
            fp = wrap_file(request.environ, BytesIO(data))
            status = 200
            headers = Headers({"Content-length": len(data)})
        else:
            msg = "No acceptable media could be found for requested type(s) %s" % request.headers.get("Accept")
            if path:
                # then os.path.exists(path) must be false
                msg += " (%s does not exist)" % path
            raise NotAcceptable(msg)
        return Response(fp, status, headers, mimetype=contenttype, direct_passthrough=True)
