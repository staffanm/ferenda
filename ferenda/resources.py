import os
import sys

import six
from lxml.builder import ElementMaker
from rdflib import URIRef, Namespace, Literal, BNode, Graph, RDF, RDFS, OWL
from rdflib.namespace import FOAF, SKOS
from rdflib.plugin import register, Parser, Serializer
register('json-ld', Parser, 'ferenda.thirdparty.rdflib_jsonld.parser',
         'JsonLDParser')
register('json-ld', Serializer, 'ferenda.thirdparty.rdflib_jsonld.serializer',
         'JsonLDSerializer')

from ferenda import LayeredConfig
from ferenda import util


class Resources(object):
    def __init__(self, repos, resourcedir, **kwargs):
        self.repos = repos
        defaults = {'resourcedir': resourcedir}
        defaults.update(kwargs)
        self.config = LayeredConfig(defaults)
        # possibly setup logger?

    def make(self,
             css=True,
             js=True,
             img=True,
             xml=True,
             api=True):
        if css:
            res['css'] = self.make_css()
        if js:
            res['js'] = self.make_js()
        if img:
            res['img'] = self.make_img()
        if xml:
            res['xml'] = self.make_resources_xml()
        if api:
            res['api'] = self.make_api_files()

    def make_css(self):
        from .thirdparty import cssmin
        combinefile = None
        if self.config.combine:
            combinefile  = os.sep.join([self.resourcedir, 'css', 'combined.css'])
            
        return self._make_files('cssfiles', combinefile, cssmin.cssmin)

    def make_js(self):
        # slimit provides better perf, but isn't py3 compatible
        # import slimit
        # js = slimit.minify(
        #     jsbuffer.getvalue(), mangle=True, mangle_toplevel=True)
        import jsmin
        combinefile = None
        if self.config.combine:
            combinefile  = os.sep.join([self.resourcedir, 'js', 'combined.js'])
        return self._make_files('cssfiles', combinefile, jsmin.jsmin)

    def make_img(self):
        # FIXME: implement this
        return []

    def make_resources_xml(self):
        E = ElementMaker() # namespace = None, nsmap={None: ...}
        E.configuration(
            E.sitename(config.sitename),
            E.sitedescription(config.sitedescription),
            E.url(config.url),
            E.tabs(
                E.nav(
                    E.a({'class': 'navbutton',
                         'href': '#menu'},
                        E.img({'src': 'rsrc/img/navmenu.png'})),
                    E.ul(self._sitetabs())
                    )
                ),
            E.footerlinks(
                E.nav(
                    E.ul(self._footerlinks())
                )
            ),
            E.tocbutton(
                E.a({'class': 'tocbutton',
                     'href': '#menu'},
                    E.img({'src': 'rsrc/img/navmenu-small-black.png'})
                )
            ), 
            # if staticsite:
            E.search(
                E.form({'action': self.config.searchendpoint,
                        'type': 'search',
                        'name': 'q'},
                       E.a({'href': '#search',
                            'class': 'searchbutton'},
                           E.img({'src': 'rsrc/img/search.png'})
                           )
                       )
                ),
            E.stylesheets(self._li_wrap(cssfiles, 'link', 'href')),
            E.javascripts(self._li_wrap(jsfiles, 'script', 'src'))
            )

    def _li_wrap(self, items, container, attribute):
        pass
        
    def _make_files(self, option, combinefile=None, combinefunc=None):
        combine = combinefile is not None
        urls = []
        buf = six.BytesIO()
        processed = set()
        # eg. self.config.cssfiles
        for f in getattr(self.config, option):
            urls.append(self._process_file(f, buf, dir, "ferenda.ini", combine))
            processed.add(f)
        for repo in self.repos:
            for f in getattr(repo.config, option):
                if f in processed:
                    continue
            urls.append(self._process_file(f, buf, dir, inst.alias, combine))
            processed.add(f)
        urls = list(filter(None, urls))
        if combinefile:
            txt = buf.getvalue.decode('utf-8')
            util.writefile(combinefile, combinefunc(txt))
            return self._filepath_to_urlpath(combinefile, 2)
        else:
            return urls

    def _process_file(filename, buf, destdir, origin="", combine=False):
        """
        Helper function to concatenate or copy CSS/JS (optionally
        processing them with e.g. Scss) or other files to correct place
        under the web root directory.

        :param filename: The name (relative to the ferenda package) of the file
        :param buf: A buffer into which the contents of the file is written (if combine == True)
        :param destdir: The directory into which the file will be copied (unless combine == True)
        :param origin: The source of the configuration that specifies this files
        :param combine: Whether to combine all files into a single one
        :returns: The URL path of the resulting file, relative to the web root (or None if combine == True)
        :rtype: str
        """
        # disabled until pyScss is usable on py3 again
        # mapping = {'.scss': {'transform': _transform_scss,
        #                     'suffix': '.css'}
        #            }
        log = setup_logger()
        # FIXME: extend this through a load-path mechanism?
        if os.path.exists(filename):
            log.debug("Process file found %s as a file relative to %s" %
                      (filename, os.getcwd()))
            fp = open(filename, "rb")
        elif pkg_resources.resource_exists('ferenda', filename):
            log.debug("Found %s as a resource" % filename)
            fp = pkg_resources.resource_stream('ferenda', filename)
        elif filename.startswith("http://") or filename.startswith("https://"):
            if combine:
                raise errors.ConfigurationError(
                    "makeresources: Can't use combine=True in combination with external js/css URLs (%s)" % filename)
            log.debug("Using external url %s" % filename)
            return filename
        else:
            log.warning(
                "file %(filename)s (specified in %(origin)s) doesn't exist" % locals())
            return None

        (base, ext) = os.path.splitext(filename)
        # disabled until pyScss is usable on py3 again
        # if ext in mapping:
        #     outfile = base + mapping[ext]['suffix']
        #     mapping[ext]['transform'](filename, outfile)
        #     filename = outfile
        if combine:
            log.debug("combining %s into buffer" % filename)
            buf.write(fp.read())
            fp.close()
            return None
        else:
            log.debug("writing %s out to %s" % (filename, destdir))
            outfile = destdir + os.sep + os.path.basename(filename)
            util.ensure_dir(outfile)
            with open(outfile, "wb") as fp2:
                fp2.write(fp.read())
            fp.close()
            return _filepath_to_urlpath(outfile, 2)

    def make_api_files(self):
        # this should create the following files under resourcedir
        # api/context.json (aliased to /json-ld/context.json if legacyapi)
        # api/terms.json (aliased to /var/terms.json if legacyapi)
        # api/common.json (aliased to /var/common.json if legacyapi)
        # MAYBE api/ui/  - copied from ferenda/res/ui
        legacyapi = True
        files = []
        context = os.sep.join([resourcedir, "api", "context.json"])
        if legacyapi:
            contextpath = "/json-ld/context.json"
            termspath   = "/var/terms"
            commonpath  = "/var/common"
        else:
            # FIXME: create correct URL path
            contextpath = "/rsrc/api/context.json"
            termspath   = "/rsrc/api/terms.json"
            commonpath  = "/rsrc/api/common.json"
        util.ensure_dir(context)
        with open(context, "w") as fp:
            contextdict = _get_json_context(repos)
            json.dump({"@context": contextdict}, fp, indent=4, sort_keys=True)
        files.append(_filepath_to_urlpath(context, 2))

        common = os.sep.join([resourcedir, "api", "common.json"])
        terms = os.sep.join([resourcedir, "api", "terms.json"])

        for (filename, func, urlpath) in ((common, _get_common_graph, commonpath),
                                          (terms,  _get_term_graph,   termspath)):
            g = func(repos, uri + urlpath[1:])
            d = json.loads(g.serialize(format="json-ld", context=contextdict,
                                       indent=4).decode("utf-8"))
            d['@context'] = contextpath
            if legacyapi:
                d = _convert_legacy_jsonld(d, uri + urlpath[1:])
            with open(filename, "w") as fp:
                    json.dump(d, fp, indent=4, sort_keys=True)
            files.append(_filepath_to_urlpath(filename, 2))
        return {'json': files}
        
    def _convert_legacy_jsonld(indata, rooturi):
        # the json structure should be a top node containing only
        # @context, iri (localhost:8000/var/terms), type (foaf:Document)
        # and topic - a list of dicts, where each dict looks like:
        #
        # {"iri" : "referatserie",
        #  "comment" : "Anger vilken referatserie som referatet eventuellt tillhör.",
        #  "label" : "Referatserie",
        #  "type" : "DatatypeProperty"}
        out  = {}
        topics = []
        for topkey, topval in indata.items():
            if topkey == "@graph":
                for subject in topval:
                    if subject['iri'] == rooturi:
                        for key,value in subject.items():
                            if key in  ('iri', 'foaf:topic'):
                                continue
                            out[key] = value
                    else:
                        for key in subject:
                            if isinstance(subject[key], list):
                                # make sure multiple values are sorted for
                                # the same reason as below
                                subject[key].sort()
                        topics.append(subject)
            else:
                out[topkey] = topval
        # make sure the triples are in a predictable order, so we can
        # compare on the JSON level for testing
        out['topic'] = sorted(topics, key=lambda x: x['iri'])
        out['iri']  = rooturi
        return out

    def _get_json_context(self):
        data = {}
        # step 1: define all prefixes
        for repo in self.repos:
            for (prefix, ns) in repo.ns.items():
                if prefix in data:
                    assert data[prefix] == str(ns), "Conflicting URIs for prefix %s" % prefix
                else:
                    data[prefix] = str(ns)

        # the legacy api client expects some terms to be available using
        # shortened forms (eg 'label' instead of 'rdfs:label'), so we must
        # define this in the context
        if self.config.legacyapi:
            data['iri'] = "@id"
            data['type'] = "@type"
            data['label'] = 'rdfs:label'
            data['name'] = 'foaf:name'
            data['altLabel'] = 'skos:altLabel'
            # data["@language"] = "en" # how to set this? majority vote of
                                       # repos / documents? note that it's
                                       # only a default.
        return data

    def _get_term_graph(repos, graphuri):
        # produce a rdf graph of the terms (classes and properties) in
        # the vocabs we're using. This should preferably entail
        # loading the vocabularies (stored as RDF/OWL documents), and
        # expressing all the things that are owl:*Property, owl:Class,
        # rdf:Property and rdf:Class. As an intermediate step, we
        # could have preprocessed rdf graphs (stored in
        # res/vocab/dcterms.ttl, res/vocab/bibo.ttl etc) derived from the
        # vocabularies and pull them in like we pull in namespaces in
        # self.ns The rdf graph should be rooted in an url (eg
        # http://localhost:8080/var/terms, and then have each term as
        # a foaf:topic. Each term should be described with its
        # rdf:type, rdfs:label (most important!) and possibly
        # rdfs:comment
        root = URIRef(graphuri)
        g = Graph()
        g.add((root, RDF.type, FOAF.Document))
        for repo in repos:
            for prefix, uri in repo.ontologies.store.namespaces():
                if prefix:
                    g.bind(prefix, uri)
            for (s,p,o) in repo.ontologies:
                if isinstance(s, BNode):
                    continue
                if p in (RDF.type, RDFS.label, RDFS.comment):
                    g.add((root, FOAF.topic, s)) # unless we've already added it?
                    if isinstance(o, Literal): # remove language typing info
                        o = Literal(str(o))
                    g.add((s,p,o)) # control duplicates somehow
                    # print(g.serialize(format="json-ld", context=context, indent=4).decode())
        return g

    def _get_common_graph(repos, graphuri):
        # create a graph with foaf:names for all entities (publishers,
        # publication series etc) that our data mentions.
        root = URIRef(graphuri)
        g = Graph()
        g.bind("skos", SKOS)
        g.bind("foaf", FOAF)
        g.add((root, RDF.type, FOAF.Document))
        for repo in repos:
            for (s,p,o) in repo.commondata: # should work like
                                            # repo.ontologies, but read
                                            # one file per repo
                                            # ("res/extra/rfc.ttl",
                                            #  "res/extra/propregeringen.ttl" in
                                            # a controlled way)
                if p in (FOAF.name, SKOS.prefLabel, SKOS.altLabel):
                    g.add((root, FOAF.topic, s))
                    g.add((s,p,o))
                    # try to find a type
                    g.add((s, RDF.type, repo.commondata.value(s, RDF.type)))
        return g

