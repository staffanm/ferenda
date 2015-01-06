# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import os
import json

import six
from six import text_type as str
from lxml import etree
from lxml.builder import ElementMaker
from rdflib import URIRef, Literal, BNode, Graph, Namespace, RDF, RDFS
from rdflib.namespace import FOAF, SKOS
BIBO = Namespace("http://purl.org/ontology/bibo/")
import pkg_resources

from layeredconfig import LayeredConfig, Defaults

from ferenda import DocumentRepository
from ferenda import util, errors


class Resources(object):
    """Creates and manages various assets/resources needed for web serving.

    This class is not yet part of the public API -- clients should use
    manager.makeresources for now.

    """
    
    def __init__(self, repos, resourcedir, **kwargs):
        # FIXME: document what kwargs could be (particularly 'combineresources')
        self.repos = repos
        self.resourcedir = resourcedir
        defaults = DocumentRepository().get_default_options()
        defaults.update(kwargs)
        self.config = LayeredConfig(Defaults(defaults))
        from ferenda.manager import setup_logger
        self.log = setup_logger()


    def make(self,
             css=True,
             js=True,
             img=True,
             xml=True,
             api=None):
        res = {}
        if api is None:
            api = not self.config.staticsite
        if css:
            res['css'] = self.make_css()
        if js:
            res['js'] = self.make_js()
        if img:
            res['img'] = self.make_img()
        if xml:
            res['xml'] = self.make_resources_xml(res.get('css',[]), res.get('js',[]))
        if api:
            res['json'] = self.make_api_files()

        # finally, normalize paths according to os.path.sep
        # conventions
        if os.sep == "\\":
            for part in res:
                result = []
                for x in res[part]:
                    if x.startswith("http://") or x.startswith("https://"):
                        result.append(x)
                    else:
                        result.append(x.replace('/', os.sep))
                res[part] = result
        return res

    def make_css(self):
        import cssmin
        combinefile = None
        if self.config.combineresources:
            combinefile  = os.sep.join([self.resourcedir, 'css', 'combined.css'])
        return self._make_files('cssfiles', self.resourcedir+os.sep+'css', combinefile, cssmin.cssmin)

    def make_js(self):
        # slimit provides better perf, but isn't py3 compatible
        # import slimit
        # js = slimit.minify(
        #     jsbuffer.getvalue(), mangle=True, mangle_toplevel=True)
        import jsmin
        combinefile = None
        if self.config.combineresources:
            combinefile  = os.sep.join([self.resourcedir, 'js', 'combined.js'])
        return self._make_files('jsfiles', self.resourcedir+os.sep+'js', combinefile, jsmin.jsmin)

    def make_img(self):
        return self._make_files('imgfiles', self.resourcedir + os.sep + 'img')
            
    def make_resources_xml(self, cssfiles, jsfiles):
        E = ElementMaker() # namespace = None, nsmap={None: ...}
        root = E.configuration(
            E.sitename(self.config.sitename),
            E.sitedescription(self.config.sitedescription),
            E.url(self.config.url),
            E.tabs(
                E.nav(
                    E.a({'class': 'navbutton',
                         'href': '#menu'},
                        E.img({'src': 'rsrc/img/navmenu.png'})),
                    E.ul(*self._links('tabs'))
                    )
                ),
            E.footerlinks(
                E.nav(
                    E.ul(*self._links('footer'))
                )
            ),
            E.tocbutton(
                E.a({'class': 'tocbutton',
                     'href': '#menu'},
                    E.img({'src': 'rsrc/img/navmenu-small-black.png'})
                )
            ), 
            E.stylesheets(*self._li_wrap(cssfiles, 'link', 'href', rel="stylesheet")),
            E.javascripts(*self._li_wrap(jsfiles, 'script', 'src', text=" "))
            )

        if not self.config.staticsite:
            root.append(
                E.search(
                    E.form({'action': self.config.searchendpoint,
                            'type': 'search',
                            'name': 'q'},
                           E.input({'type': 'search',
                                    'name': 'q'}),
                           E.a({'href': '#search',
                                'class': 'searchbutton'},
                               E.img({'src': 'rsrc/img/search.png'})
                           )
                       )
                )
            )
                                    
        outfile = self.resourcedir + os.sep + "resources.xml"
        util.writefile(outfile, etree.tostring(root, encoding="utf-8", pretty_print=True).decode("utf-8"))
        self.log.info("Wrote %s" % outfile)
        return [self._filepath_to_urlpath(outfile, 1)]


    # FIXME: When creating <script> elements, must take care not to
    # create self-closing tags (like by creating a single space text
    # node)
    def _li_wrap(self, items, container, attribute, text=None, **kwargs):
        elements = []
        for item in items:
            kwargs[attribute] = item
            e = etree.Element(container, **kwargs)
            e.text = text
            elements.append(e)
        return elements

    def _links(self, methodname):
        E = ElementMaker()
        elements = []
        for repo in self.repos:
            for item in getattr(repo, methodname)():
                (label, url) = item
                alias = repo.alias
                self.log.debug(
                    "Adding %(methodname)s %(label)s (%(url)s) from docrepo %(alias)s" % locals())
                elements.append(E.li(
                    E.a({'href': url},
                        label)))
        return elements

    
    def _make_files(self, option, filedir, combinefile=None, combinefunc=None):
        urls = []
        buf = six.BytesIO()
        processed = set()
        # eg. self.config.cssfiles
        if getattr(self.config, option): # it's possible to set eg
                                         # cssfiles=None when creating
                                         # the Resources object
            for f in getattr(self.config, option):
                urls.append(self._process_file(f, buf, filedir, "ferenda.ini"))
                processed.add(f)
        for repo in self.repos:
            # FIXME: create a more generic way of optionally
            # signalling to a repo that "Hey, now it's time to create
            # your resources if you can"
            if repo.__class__.__name__ == "SFS" and option == "imgfiles":
                self.log.info("calling into SFS._makeimages()")
                LayeredConfig.set(repo.config, 'imgfiles', repo._makeimages())
            for f in getattr(repo.config, option):
                if f in processed:
                    continue
                urls.append(self._process_file(f, buf, filedir, repo.alias))
                processed.add(f)
        urls = list(filter(None, urls))
        if combinefile:
            txt = buf.getvalue().decode('utf-8')
            util.writefile(combinefile, combinefunc(txt))
            return [self._filepath_to_urlpath(combinefile, 2)]
        else:
            return urls

    def _process_file(self, filename, buf, destdir, origin=""):
        """
        Helper function to concatenate or copy CSS/JS (optionally
        processing them with e.g. Scss) or other files to correct place
        under the web root directory.

        :param filename: The name (relative to the ferenda package) of the file
        :param buf: A buffer into which the contents of the file is written (if combineresources == True)
        :param destdir: The directory into which the file will be copied (unless combineresources == True)
        :param origin: The source of the configuration that specifies this files
        :returns: The URL path of the resulting file, relative to the web root (or None if combineresources == True)
        :rtype: str
        """
        # disabled until pyScss is usable on py3 again
        # mapping = {'.scss': {'transform': _transform_scss,
        #                     'suffix': '.css'}
        #            }
        # FIXME: extend this through a load-path mechanism?
        if os.path.exists(filename):
            self.log.debug("Process file found %s as a file relative to %s" %
                      (filename, os.getcwd()))
            fp = open(filename, "rb")
        elif pkg_resources.resource_exists('ferenda', filename):
            self.log.debug("Found %s as a resource" % filename)
            fp = pkg_resources.resource_stream('ferenda', filename)
        elif filename.startswith("http://") or filename.startswith("https://"):
            if self.config.combineresources:
                raise errors.ConfigurationError(
                    "makeresources: Can't use combineresources=True in combination with external js/css URLs (%s)" % filename)
            self.log.debug("Using external url %s" % filename)
            return filename
        else:
            self.log.warning(
                "file %(filename)s (specified in %(origin)s) doesn't exist" % locals())
            return None

        (base, ext) = os.path.splitext(filename)
        # disabled until pyScss is usable on py3 again
        # if ext in mapping:
        #     outfile = base + mapping[ext]['suffix']
        #     mapping[ext]['transform'](filename, outfile)
        #     filename = outfile
        if self.config.combineresources:
            self.log.debug("combining %s into buffer" % filename)
            buf.write(fp.read())
            fp.close()
            return None
        else:
            self.log.debug("writing %s out to %s" % (filename, destdir))
            outfile = destdir + os.sep + os.path.basename(filename)
            util.ensure_dir(outfile)
            with open(outfile, "wb") as fp2:
                fp2.write(fp.read())
            fp.close()
            return self._filepath_to_urlpath(outfile, 2)

    def make_api_files(self):
        # this should create the following files under resourcedir
        # api/context.json (aliased to /json-ld/context.json if legacyapi)
        # api/terms.json (aliased to /var/terms.json if legacyapi)
        # api/common.json (aliased to /var/common.json if legacyapi)
        # MAYBE api/ui/  - copied from ferenda/res/ui
        files = []
        context = os.sep.join([self.resourcedir, "api", "context.json"])
        if self.config.legacyapi:
            self.log.info("Creating API files for legacyapi")
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
            contextdict = self._get_json_context()
            json.dump({"@context": contextdict}, fp, indent=4, sort_keys=True)
        files.append(self._filepath_to_urlpath(context, 2))

        common = os.sep.join([self.resourcedir, "api", "common.json"])
        terms = os.sep.join([self.resourcedir, "api", "terms.json"])

        for (filename, func, urlpath) in ((common, self._get_common_graph, commonpath),
                                          (terms,  self._get_term_graph,   termspath)):
            g = func(self.config.url + urlpath[1:])
            d = json.loads(g.serialize(format="json-ld", context=contextdict,
                                       indent=4).decode("utf-8"))
            # d might not contain a @context (if contextdict == {}, ie
            # no repos are given)
            if '@context' in d:
                d['@context'] = contextpath
            if self.config.legacyapi:
                d = self._convert_legacy_jsonld(d, self.config.url + urlpath[1:])
            with open(filename, "w") as fp:
                    json.dump(d, fp, indent=4, sort_keys=True)
            files.append(self._filepath_to_urlpath(filename, 2))

        if self.config.legacyapi:
            # copy ui explorer app to <url>/rsrc/ui/ -- this does not get
            # included in files
            util.ensure_dir(os.sep.join([self.resourcedir,"ui","dummy.txt"]))
            try:
                for f in pkg_resources.resource_listdir("ferenda", "res/ui"):
                    src = pkg_resources.resource_stream("ferenda", "res/ui/" + f)
                    with open(os.sep.join([self.resourcedir,"ui", f]), "wb") as dest:
                        dest.write(src.read())
            except OSError as e: # happens on travis-ci
                x = pkg_resources.get_provider("ferenda")
                print("Got error '%s'. Provider %s, .module_path %s" % (str(e), x, x.module_path))
                print("Does %s/res/ui exist? %s (wd %s, os.listdir: %r)" % (x.module_path, os.path.exists(x.module_path + "/res/ui"), os.getcwd(), os.listdir(".")))
                try:
                    fp = pkg_resources.resource_stream('ferenda', "res/ui/index.html")
                    print("Got hold of res/ui/index.html through .resource_stream")
                except Exception as sub_e:
                    print("Couldn't get a res stream either: %s" % sub_e)
                raise e # or pass
        return files
        
    def _convert_legacy_jsonld(self, indata, rooturi):
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

        # the property containing the id/uri for the
        # record may be under @id or iri, depending on
        # whether self.config.legacyapi was in effect for
        # _get_json_context()
        if self.config.legacyapi:
            idfld = 'iri'
        else:
            idfld = '@id'

        # indata might be a mapping containing a list of mappings
        # under @graph, or it might just be the actual list.
        wantedlist = None
        if isinstance(indata, list):
            wantedlist = indata
        else:
            for topkey, topval in indata.items():
                if topkey == "@graph":
                    wantedlist = topval
                    break

        if not wantedlist:
            self.log.warning("Couldn't find list of mappings in %s, topics will be empty" % indata)
        else:
            shortened = {}
            for subject in sorted(wantedlist, key=lambda x: x["iri"]):
                if subject[idfld] == rooturi:
                    for key,value in subject.items():
                        if key in  (idfld, 'foaf:topic'):
                            continue
                        out[key] = value
                else:
                    for key in subject:
                        if isinstance(subject[key], list):
                            # make sure multiple values are sorted for
                            # the same reason as below
                            subject[key].sort()

                    # FIXME: We want to use just the urileaf for
                    # legacyapi clients (ie Standard instead of
                    # bibo:Standard) but to be proper json-ld, this
                    # requires that we define contexts for this. Which
                    # we don't (yet)
                    if ("iri" in subject and
                        ":" in subject["iri"] and
                        "://" not in subject["iri"]):
                        short = subject["iri"].split(":",1)[1]
                        if short in shortened:
                            self.log.warning("Cannot shorten IRI %s -> %s, already defined (%s)" % (subject["iri"], short, shortened[short]))
                            del subject["iri"] # skips adding this to topics
                        else:
                            shortened[short] = subject["iri"]
                            subject["iri"] = short
                    if "iri" in subject and subject["iri"]:
                        topics.append(subject)
                    

        # make sure the triples are in a predictable order, so we can
        # compare on the JSON level for testing
        out['topic'] = sorted(topics, key=lambda x: x[idfld])
        out['iri']  = rooturi
        if '@context' in indata:
            out['@context'] = indata['@context']
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

        # foaf and rdfs must always be defined prefixes
        data["foaf"] = "http://xmlns.com/foaf/0.1/"
        data["rdfs"] = "http://www.w3.org/2000/01/rdf-schema#"

        # the legacy api client expects some terms to be available using
        # shortened forms (eg 'label' instead of 'rdfs:label'), so we must
        # define them in our context
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

    def _get_term_graph(self, graphuri):
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
        for repo in self.repos:
            for prefix, uri in repo.ontologies.store.namespaces():
                if prefix:
                    g.bind(prefix, uri)
            # foaf: must always be bound
            g.bind("foaf", "http://xmlns.com/foaf/0.1/")
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

    def _get_common_graph(self, graphuri):
        # create a graph with foaf:names for all entities (publishers,
        # publication series etc) that our data mentions.
        root = URIRef(graphuri)
        g = Graph()
        g.bind("skos", SKOS)
        g.bind("foaf", FOAF)
        g.add((root, RDF.type, FOAF.Document))
        for repo in self.repos:
            for (s,p,o) in repo.commondata: # should work like
                                            # repo.ontologies, but read
                                            # one file per repo
                                            # ("res/extra/rfc.ttl",
                                            #  "res/extra/propregeringen.ttl" in
                                            # a controlled way)
                if p in (FOAF.name, SKOS.prefLabel, SKOS.altLabel, BIBO.identifier):
                    g.add((root, FOAF.topic, s))
                    g.add((s,p,o))
                    # try to find a type
                    g.add((s, RDF.type, repo.commondata.value(s, RDF.type)))
        return g

    def _filepath_to_urlpath(self, path, keep_segments=2):
        """
        :param path: the full or relative filepath to transform into a urlpath
        :param keep_segments: the number of directory segments to keep (the ending filename is always kept)
        """
        # data/repo/rsrc/js/main.js, 3 -> repo/rsrc/js/main.js
        # /var/folders/tmp4q6b1g/rsrc/resources.xml, 1 -> rsrc/resources.xml
        # C:\docume~1\owner\locals~1\temp\tmpgbyuk7\rsrc\css\test.css, 2 - rsrc/css/test.css
        path = path.replace(os.sep, "/")
        urlpath = "/".join(path.split("/")[-(keep_segments + 1):])
        # print("_filepath_to_urlpath (%s): %s -> %s" % (keep_segments, path, urlpath))
        return urlpath




