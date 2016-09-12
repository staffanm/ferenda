# a RequestHandler is part of a docrepo and responsible for
# determining if the docrepo can respond to a particular request, and
# for determining the physical path of the file corresponding to that
# request.

from wsgiref.util import request_uri
import os
from io import BytesIO

from rdflib import Graph

from ferenda.thirdparty import httpheader

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
    

    def __init__(self, repo):
        self.repo = repo
        self.mimemap = {'text/html': repo.store.generated_path,
                        'application/xhtml+xml': repo.store.parsed_path,
                        'application/rdf+xml': repo.store.distilled_path}
        self.suffixmap = {'xhtml': repo.store.parsed_path,
                          'rdf': repo.store.distilled_path}

    def supports(self, environ):
        """Returns True iff this particular handler supports this particular request."""
        segments = environ['PATH_INFO'].split("/", 3)
        # with PATH_INFO like /dataset/base.rdf, we still want the
        # alias to check to be "base", not "base.rdf"
        return len(segments) > 2 and segments[2].rsplit(".")[0] == self.repo.alias

    def handle(self, environ):
        """provides a response to a particular request by returning a a tuple
        *(fp, length, memtype)*, where *fp* is an open file of the
        document to be returned.

        """
        path_info = environ['PATH_INFO'].encode("latin-1").decode("utf-8")
        segments = path_info.split("/", 3)

        # shld we decode this like path_info above
        uri = request_uri(environ).encode("latin-1").decode("utf-8")
        if 'develurl' in self.repo.config:
            uri = uri.replace(self.repo.config.develurl, self.repo.config.url)
        if "?" in uri:
            uri, querystring = uri.rsplit("?", 1)
        else:
            querystring = None
        basefile = self.repo.basefile_from_uri(uri)
        
        suffix = None
        if segments[1] == "dataset":
            tmpuri = uri
            if "." in uri.split("/")[-1]:
                tmpuri = tmpuri.rsplit(".")[0]
            if querystring:
                tmpuri += "?" + querystring
            params = self.repo.dataset_params_from_uri(tmpuri)
        else:
            params = self.repo.basefile_params_from_basefile(basefile)
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
        accept = environ.get('HTTP_ACCEPT', 'text/html')
        self.repo.log.info("%s: OK trying to handle this, uri=%s" % (self.repo.alias, uri))
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
        if accept in self.mimemap:
            contenttype = accept
        elif suffix in self._mimesuffixes:
            contenttype = self._mimesuffixes[suffix]
        elif accept in self._rdfformats:
            contenttype = accept
        elif suffix in self._rdfsuffixes:
            contenttype = self._revformats[self._rdfsuffixes[suffix]]
        else:
            if ((not suffix) and
                    preferred and
                    preferred[0].media_type == "text/html"):
                contenttype = preferred[0].media_type
                # pathfunc = repo.store.generated_path
        return contenttype

    def lookup_resource(self, environ, basefile, params, contenttype, suffix):
        # try to lookup pathfunc from contenttype (or possibly suffix, or maybe params)
        if contenttype in self.mimemap and not basefile.endswith("/data"):
            pathfunc = self.mimemap[contenttype]
        elif suffix in self.suffixmap and not basefile.endswith("/data"):
            pathfunc = self.suffixmap[suffix]
        else:
            pathfunc = None
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


