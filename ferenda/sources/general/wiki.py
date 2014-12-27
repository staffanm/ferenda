# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# system
from tempfile import mktemp
import random
import re
import os
from six import text_type as str
from six import binary_type as bytes

# 3rdparty
from lxml import etree
from rdflib import Namespace, URIRef, Literal
import requests

# mine
from ferenda import DocumentRepository, DocumentStore
from ferenda import util
from ferenda.sources.general import Keyword
# from keywords import Keyword

try:
    from ferenda.thirdparty.mw import Parser, Semantics, Settings, Preprocessor
except ImportError as e:
    import sys
    if sys.version_info < (2, 7):
        raise RuntimeError("ferenda.sources.general.Wiki is not supported under python 2.6: %s" % str(e))
    else:
        raise e # dunno
        
import unicodedata

class MediaWikiStore(DocumentStore):
    def basefile_to_pathfrag(self, basefile):
        return basefile.replace(":", os.sep).replace(" ", "_")

    def pathfrag_to_basefile(self, pathfrag):
        # This unicode normalization turns "a" + U+0308 (COMBINING
        # DIAERESIS) into a honest 'ä'. This is an issue on mac file
        # systems. FIXME: should this be a part of
        # DocumentStore.pathfrag_to_basefile?
        return unicodedata.normalize("NFC", pathfrag.replace("_", " ").replace(os.sep, ":"))


class MediaWiki(DocumentRepository):

    """Downloads content from a Mediawiki system and converts it to annotations on other documents.

    For efficient downloads, this docrepo requires that there exists a
    XML dump (created by `dumpBackup.php
    <http://www.mediawiki.org/wiki/Manual:DumpBackup.php>`_) of the
    mediawiki contents that can be fetched over HTTP/HTTPS. Configure
    the location of this dump using the ``mediawikiexport``
    parameter::

        [mediawiki]
        class = ferenda.sources.general.MediaWiki
        mediawikiexport = http://localhost/wiki/allpages-dump.xml

    .. note::

       This docrepo relies on the smc.mw module, which doesn't work on
       python 2.6, only 2.7 and newer.

    """

    alias = "mediawiki"
    downloaded_suffix = ".xml"
    documentstore_class = MediaWikiStore
    rdf_type = Namespace(util.ns['skos']).Concept
    keyword_class = Keyword
    namespaces = ['rdf', 'skos', 'prov', 'dcterms']

    def __init__(self, config=None, **kwargs):
        super(MediaWiki, self).__init__(config, **kwargs)
        if self.config._parent and hasattr(self.config._parent, 'keyword'):
            self.keywordrepo = self.keyword_class(self.config._parent.keyword)
        else:
            self.keywordrepo = self.keyword_class()
    
    def get_default_options(self):
        opts = super(MediaWiki, self).get_default_options()
        # The API endpoint URLs change with MW language
        opts['mediawikiexport'] = 'http://localhost/wiki/Special:Export/%s(basefile)'
        opts['mediawikidump'] = 'http://localhost/wiki/allpages-dump.xml'
        opts['mediawikinamespaces'] = ['Category']
            # process pages in this namespace (as well as pages in the
            # default namespace)
        return opts

    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)
        if self.config.mediawikidump:
            xmldumppath = self.store.path('dump', 'downloaded', '.xml')
            try:
                resp = requests.get(self.config.mediawikidump)
                self.log.info("Loaded XML dump from %s" % self.config.mediawikidump)
                with self.store._open(xmldumppath, mode="wb") as fp:
                    fp.write(resp.content)
            except Exception:
                # try to loa
                pass 
            # xml = etree.parse(resp.content)
            xml = etree.parse(xmldumppath)
        else:
            raise ConfigurationError("config.mediawikidump not set")

        MW_NS = "{%s}" % xml.getroot().nsmap[None]
        wikinamespaces = []
        # FIXME: Find out the proper value of MW_NS
        for ns_el in xml.findall("//" + MW_NS + "namespace"):
            wikinamespaces.append(ns_el.text)

        # Get list of existing basefiles - if any of those
        # does not appear in the XML dump, remove them afterwards
        basefiles = list(self.store.list_basefiles_for("parse"))
        total = written = 0
        for page_el in xml.findall(MW_NS + "page"):
            basefile = page_el.find(MW_NS + "title").text
            if basefile == "Huvudsida":
                continue
            if ":" in basefile and basefile.split(":")[0] in wikinamespaces:
                (namespace, localtitle) = basefile.split(":", 1)
                if namespace not in self.config.mediawikinamespaces:
                    continue
            writefile = False
            p = self.store.downloaded_path(basefile)
            newcontent = etree.tostring(page_el, encoding="utf-8")
            if not os.path.exists(p):
                writefile = True
            else:
                oldcontent = util.readfile(p, "rb")
                if newcontent != oldcontent:
                    writefile = True
            if writefile:
                util.ensure_dir(p)
                with open(p, "wb") as fp:
                    fp.write(newcontent)
                    self.log.info("%s: extracting from XML dump" % basefile)
                written += 1
            
            if basefile in basefiles:
                del basefiles[basefiles.index(basefile)]
            total += 1

        if 'dump' in basefiles:  # never remove
            del basefiles[basefiles.index('dump')]
        for b in basefiles:
            self.log.info("%s: removing stale document" % b)
            util.robust_remove(self.store.downloaded_path(b))
        self.log.info("Examined %s documents, wrote %s of them" % (total, written))

    def download_single(self, basefile):
        # download a single term, for speed
        url = self.config.mediawikiexport % {'basefile': basefile}
        self.download_if_needed(url, basefile)

    re_anchors = re.compile('(<a.*?</a>)', re.DOTALL)
    re_anchor = re.compile('<a[^>]*>(.*)</a>', re.DOTALL)
    re_tags = re.compile('(</?[^>]*>)', re.DOTALL)


    # NOTE: What is this thing, really? Is it a wiki document by
    # itself, or is it metadata about a concept identified by a
    # keyword / label?
    def parse_metadata_from_soup(self, soup, doc):
        super(MediaWiki, self).parse_metadata_from_soup(soup, doc)
        # remove dcterms:identifier because it's pointless
        doc.meta.remove((URIRef(doc.uri),
                         self.ns['dcterms'].identifier,
                         Literal(doc.basefile)))
    
    def parse_document_from_soup(self, soup, doc):
        
        wikitext = soup.find("text").text
        parser = self.get_wikiparser()
        settings = self.get_wikisettings()
        semantics = self.get_wikisemantics(parser, settings)
        preprocessor = self.get_wikipreprocessor(settings)
        
        # the main responsibility of the preprocessor is to expand templates
        wikitext = preprocessor.expand(doc.basefile, wikitext)

        xhtml = parser.parse(wikitext, "document",
                             filename=doc.basefile,
                             semantics=semantics,
                             trace=False)
        doc.body = self.postprocess(doc, xhtml)
        return None

    def canonical_uri(self, basefile):
        # by default, a wiki page is expected to describe a
        # concept/keyword -- so we use our associated Keyword repo to
        # find its uri.
        return self.keywordrepo.canonical_uri(basefile)

    def get_wikiparser(self):
        return Parser(parseinfo=False, whitespace='', nameguard=False)

    def get_wikisemantics(self, parser, settings):
        return WikiSemantics(parser, settings)
        
    def get_wikisettings(self):
        return WikiSettings(lang=self.lang)

    def get_wikipreprocessor(self, settings):
        return WikiPreprocessor(settings)

    def postprocess(self, doc, xhtmltree, toplevel_property=True):
        body = xhtmltree.getchildren()[0]
        # render_xhtml_tree will add @about
        if toplevel_property:
            # shouldn't add these in postprocess_commentary mode
            body.set("property", "dcterms:description")
            body.set("datatype", "rdf:XMLLiteral")
            containerdiv = etree.Element("div")
            for child in body:
                body.remove(child)
                containerdiv.append(child)
            body.append(containerdiv)
        # find any links that indicate that this concept has the
        # dcterms:subject of something (typically indicated by
        # Category tags)
        for subjectlink in xhtmltree.findall(".//a[@rel='dcterms:subject']"):
            # add metadata
            doc.meta.add((URIRef(doc.uri),
                          self.ns['dcterms'].subject,
                          URIRef(subjectlink.get("href"))))
            # remove from tree
            parent = subjectlink.getparent()
            parent.remove(subjectlink)
            # if the containing element is empty, remove as well
            if not (len(parent) or
                    parent.text or
                    parent.tail):
                parent.getparent().remove(parent)

        # convert xhtmltree to a ferenda.Elements tree
        root = self.elements_from_node(xhtmltree)
        return root[0]

    def elements_from_node(self, node):
        
        from ferenda.elements.html import _tagmap
        assert node.tag in _tagmap
        element = _tagmap[node.tag](**node.attrib)
        if node.text and node.text.strip():
            element.append(str(node.text))
        for child in node:
            if isinstance(child, str):
                element.append(str(child))
            else:
                subelement = self.elements_from_node(child)
                if subelement is not None:
                    element.append(subelement)
                if child.tail and child.tail.strip():
                    element.append(str(child.tail))
        return element

    @classmethod
    def generate_all_setup(cls, config):
        # This is not a document repository that produces its own
        # pages -- rather, it creates description metadata (through
        # download/parse/relate) that other repos (primarily Keyword)
        # can use. THerefore, we return False in this setup method to
        # signify that no work needs to be done
        return False

    def toc(self, otherrepos=[]):
        # and no toc either
        return 

    def news(self, otherrepos=[]):
        # nor newsfeeds
        return

    def tabs(self):
        return []

    def frontpage_content(self, primary=False):
        return

#    # differ from the default relate_triples in that it uses a different
#    # context for every basefile and clears this beforehand.
#    # Note that a basefile can contain statements
#    # about multiple and changing subjects, so it's not trivial to erase all
#    # statements that stem from a basefile w/o a dedicated context.
#    def relate_triples(self, basefile):
#        context = self.dataset_uri() + "#" + basefile.replace(" ", "_")
#        ts = self._get_triplestore()
#        with util.logtime(self.log.debug,
#                          "%(basefile)s: Added %(rdffile)s to context %(context)s (%(elapsed).3f sec)",
#                          {'basefile': basefile,
#                           'context': context,
#                           'rdffile': self.store.distilled_path(basefile),
#                           'triplestore': self.config.storelocation}):
#            data = open(self.store.distilled_path(basefile)).read()
#            ts.clear(context=context)
#            ts.add_serialized(data, format="xml", context=context)


class WikiSemantics(Semantics):

    def document(self, ast):
        html = super(WikiSemantics, self).document(ast)
        # remove the newly-created toc. If postprocess_toc was a
        # Semantics method we could just override this in this
        # superclass, now we'll have to rip it out after the fact.
        toc = html.find(".//div[@id='toc']")
        if toc is not None:
            toc.getparent().remove(toc)
        return html
            
    
    def internal_link(self, ast):
        el = super(WikiSemantics, self).internal_link(ast)
        target = "".join(ast.target).strip()
        name = self.settings.canonical_page_name(target)
        if name[0].prefix == 'category':
            el.set("rel", "dcterms:subject")
        return el


class WikiSettings(Settings):
    def make_url(self, name, **kwargs):
        uri = super(WikiSettings, self).make_url(name, **kwargs)
        return uri


class WikiPreprocessor(Preprocessor):
    def get_template(self, namespace, pagename):
        # FIXME: This is a special hack for supporting
        # {{DISPLAYTITLE}} (not a proper template? Check if smc.mw is
        # supposed to have support for wgAllowDisplayTitle
        if pagename.startswith("DISPLAYTITLE:"):
            pagename = "DISPLAYTITLE"
        if namespace.prefix != "template":
            return None
        tmpl = self.settings.templates.get((namespace.prefix, pagename), None)
        return tmpl
