# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# This is a set of subclasses to regular Wiki and scm.mw classes to
# customize behaviour.

# system
import unicodedata
import os
import re

# 3rdparty
from lxml import etree
from rdflib import Graph, Namespace, RDF
from cached_property import cached_property

# mine
from ferenda import util
from ferenda import DocumentStore
from ferenda.decorators import action
from ferenda.sources.legal.se import SwedishLegalSource, SwedishCitationParser
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.sources.general import wiki
from ferenda.thirdparty.coin import URIMinter
from . import LNKeyword 

class LNMediaWikiStore(wiki.MediaWikiStore):

    # the pathfrag mangling in MediaWikiStore is not suitable for the
    # content of the Lagen.nu wiki, which in practice has a highly
    # heterogenous usage of : and /, which makes general roundtripping
    # from basefile -> pathfrag -> basefile impossible . Therefore we
    # fall back to another behaviour which should be roundtrippable
    def basefile_to_pathfrag(self, basefile):
        return basefile.replace(":", os.sep+"%3E").replace(" ", "_")

    def pathfrag_to_basefile(self, pathfrag):
        return unicodedata.normalize("NFC", pathfrag.replace(os.sep+"%3E", ":").replace("_", " "))

class LNMediaWiki(wiki.MediaWiki):
    """Managing commentary on legal sources (Lagen.nu-version of MediaWiki)
    """
    namespaces = SwedishLegalSource.namespaces
    documentstore_class = LNMediaWikiStore
    download_archive = False

    from ferenda.sources.legal.se.legalref import LegalRef
    keyword_class = LNKeyword



    @cached_property
    def parser(self):
        p = LegalRef(LegalRef.LAGRUM, LegalRef.KORTLAGRUM,
                     LegalRef.FORARBETEN, LegalRef.RATTSFALL)
        # self.commondata need to include extra/sfs.ttl
        # somehow. This is probably not the best way.
        with self.resourceloader.open("extra/sfs.ttl") as fp:
            self.commondata.parse(data=fp.read(), format="turtle")
        # actually, to mint URIs for rattsfall we need the
        # skos:altLabel for the rpubl:Rattsfallspublikation -- so we
        # need everything
        with self.resourceloader.open("extra/swedishlegalsource.ttl") as fp:
            self.commondata.parse(data=fp.read(), format="turtle")
        return SwedishCitationParser(p,
                                     self.minter,
                                     self.commondata,
                                     allow_relative=True)

    lang = "sv"
    # alias = "lnwiki"
    
    def __init__(self, config=None, **kwargs):
        super(LNMediaWiki, self).__init__(config, **kwargs)
        from . import SFS
        if self.config._parent and hasattr(self.config._parent, "sfs"):
            self.sfsrepo = SFS(self.config._parent.sfs)
        else:
            self.sfsrepo = SFS()

    # Taken from ferenda.sources.legal.se.SwedishLegalSource which
    # this repo does not derive from
    @cached_property
    def minter(self):
        # print("%s (%s) loading minter" % (self.alias, id(self)))
        filename = self.resourceloader.filename
        spacefile = filename("uri/swedishlegalsource.space.ttl")
        slugsfile = filename("uri/swedishlegalsource.slugs.ttl")
        self.log.debug("Loading URISpace from %s" % spacefile)
        # print("Loading URISpace from %s" % spacefile)
        # print("Loading Slugs from %s" % slugsfile)
        cfg = Graph().parse(spacefile,
                            format="turtle").parse(slugsfile,
                                                   format="turtle")
        COIN = Namespace("http://purl.org/court/def/2009/coin#")
        # select correct URI for the URISpace definition by
        # finding a single coin:URISpace object
        spaceuri = cfg.value(predicate=RDF.type, object=COIN.URISpace)
        return URIMinter(cfg, spaceuri)

    def get_wikitext(self, soup, doc):
        if doc.basefile == "Lagen.nu:Huvudsida":
            wikitext = soup.find("text").text
            wikitext = wikitext.split("= Index =")[1].strip()
            wikitext = re.sub("\n\n", "\n", wikitext, flags=re.MULTILINE)
            return wikitext
        else:
            return super(LNMediaWiki, self).get_wikitext(soup, doc)

    def get_wikisettings(self):
        settings = LNSettings(lang=self.lang)
        # NOTE: The settings object (the make_url method) only needs
        # access to the canonical_uri method.
        settings.make_sfs_url = self.sfsrepo.canonical_uri
        settings.make_keyword_url = self.keywordrepo.canonical_uri
        return settings

    def get_wikisemantics(self, parser, settings):
        return LNSemantics(parser, settings)

    def canonical_uri(self, basefile):
        if basefile.startswith("SFS/") or basefile.startswith("SFS:"):
            # "SFS/1998:204" -> "1998:204"
            basefile = basefile[4:].replace("/", ":")
            return self.sfsrepo.canonical_uri(basefile)
        else:
            if basefile.startswith("Kategori/"):
                basefile = basefile.replace("/", ":", 1)
            return super(LNMediaWiki, self).canonical_uri(basefile)
        
    def postprocess(self, doc, xhtmltree):
        # if SFS mode:
        # create a div for root content
        # find all headers, create div for everything there
        if doc.basefile.startswith("SFS/") or doc.basefile.startswith("SFS:"):
            self.postprocess_commentary(doc, xhtmltree)
            toplevel_property = False
            allow_relative = True
        else:
            toplevel_property = True
            allow_relative = False
        body = super(LNMediaWiki, self).postprocess(doc, xhtmltree,
                                                     toplevel_property=toplevel_property)
        self.parser.parse_recursive(body, predicate=None)
        return body

    def postprocess_commentary(self, doc, xhtmltree):
        uri = doc.uri
        body = xhtmltree.getchildren()[0]
        newbody = etree.Element("body")

        curruri = uri
        self.parser._currenturl = curruri
        currdiv = etree.SubElement(newbody, "div")
        currdiv.set("about", curruri)
        currdiv.set("property", "dcterms:description")
        currdiv.set("datatype", "rdf:XMLLiteral")
        containerdiv = etree.SubElement(currdiv, "div")

        for child in body.getchildren():
            if child.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                # remove that <span> element that Semantics._h_el adds for us
                assert child[0].tag == "span", "Header subelement was %s not span" % child[0].tag
                child.text = child[0].text
                child.remove(child[0])
                if child.text:
                    if isinstance(child.text, bytes):
                        txt = child.text.decode("utf-8")
                    else:
                        txt = child.text
                    # we probably SHOULDN'T keep track of the current
                    # subdocument URI. Instead, demand that all
                    # references are document-global (ie "4 kap. 2 §",
                    # not just "2 §", even though we might have
                    # processed "4 kap." earlier). This ensures that
                    # we don't have to create (possibly empty)
                    # comments for chapters, and frees us of keeping
                    # track whether a statute has "real" chapters or
                    # if the chapters are just "dividers" (like for
                    # SFS 1915:218, 1960:729 et al)
                    # 
                    # self.parser._currenturl = curruri
                    nodes = self.parser.parse_string(txt, None)
                    curruri = nodes[0].uri
                # body.remove(child)
                newbody.append(child) 
                currdiv = etree.SubElement(newbody, "div")
                currdiv.set("about", curruri)
                currdiv.set("property", "dcterms:description")
                currdiv.set("datatype", "rdf:XMLLiteral")
                # create a containerdiv under currdiv for reasons
                containerdiv = etree.SubElement(currdiv, "div")
            else:
                # body.remove(child)
                currdiv[0].append(child)
        xhtmltree.remove(body)
        xhtmltree.append(newbody)

    @action
    def update(self, article):
        """Update all generated pages that are dependent on/include the given wiki article."""
        # self.config.force = True
        # self.parse(article)
        # self.relate(article)
        # if article.startswith("SFS/"):
        #     sfsrepo = instantiate_sfs(...)  # sets config.force = True?
        #     sfsrepo.generate(article.split("/"))
        # else:
        #     kwrepo = instantiate_kw(...)
        #     kwrepo.generate(article)

    def frontpage_content(self, primary=False):
        if primary:
            page = "Lagen.nu:Huvudsida"
            if not os.path.exists(self.store.parsed_path(page)) or self.config.refresh:
                self.log.info("%s doesn't exist (or refreshing), downloading and parsing" % page)
                self.download(page)
                self.parse(page)
            res = util.readfile(self.store.parsed_path(page))
            res = res.replace(page, self.config.sitename)
            return res
        else:
            return super(LNMediaWiki, self).frontpage_content()
                            

class LNSemantics(wiki.WikiSemantics):

    def internal_link(self, ast):
        el = super(LNSemantics, self).internal_link(ast)
        return el

    def external_link(self, ast):
        el = super(LNSemantics, self).external_link(ast)
        if el.get("href", "").startswith("https://lagen.nu/om/"):
            newlink = el.get("href").replace(".html", "")
            if newlink.endswith("/om/"):
                newlink += "index"
            el.set("href", newlink)
        return el

    def heading(self, ast):
        el = super(LNSemantics, self).heading(ast)
        # <h2><span class="mw-headline" id="[[:Kategori:Familjerätt|Familjerätt]]">
        if el[0].text.startswith("[[:Kategori"):
            # [[:Kategori:Familjerätt|Familjerätt]] -> https://lagen.nu/concept/Familjerätt
            # 
            # FIXME: there should be a way of getting mw to do this
            # for us (by calling settings.make_url like internal_link
            # does
            basefile = el[0].text.split("|")[1][:-2]
            href = self.settings.make_keyword_url(basefile)
            link = etree.Element("a", **{'href': href,
                                         'id': basefile})
            link.text = basefile
            el[0] = link
        return el
    

class LNSettings(wiki.WikiSettings):
    def __init__(self, lang="en"):
        super(LNSettings, self).__init__(lang)
        from ferenda.thirdparty.mw.settings import Namespace as MWNamespace
        template_ns = MWNamespace({"prefix": "template",
                                   "ident": 10,
                                   "name": {"en": "Template",
                                            "de": "Vorlage",
                                            "sv": "Mall"}})
        self.namespaces.add(MWNamespace({"prefix": "category",
                                         "ident": 14,
                                         "name": {"en": "Category",
                                                  "de": "Kategorie",
                                                  "sv": "Kategori"}}))
        self.namespaces.add(template_ns)
        self.namespaces.add(MWNamespace({"prefix": "user",
                                         "ident": 2,
                                         "name": {"en": "User",
                                                  "de": "Benutzer",
                                                  "sv": "Användare"}}))
        self.msgcat["toc"]["sv"] = "Innehåll"
        self.templates = {("template", "TranslatedAct"):
                          "\n<small>[{{{href}}} An unofficial translation of "
                          "{{{actname}}} is available from "
                          "{{{source}}}]</small>\n",
                          ("template", "DISPLAYTITLE"): ""}

    def make_url(self, name, **kwargs):
        # uri = super(LNSettings, self).make_url(name, **kwargs)
        if name[1].startswith("SFS/"):
            basefile = name[1][4:]
            # sometimes it seems mwparser returns "SFS/1957:390 Lagen
            # (1957:390) om fiskearrenden" instead of just
            # "SFS/1957:390". However, we must handle "SFS/1845:50
            # s.1" correctly
            if " " in basefile: 
                root, extra = basefile.split(" ",1)
                if not re.match(r"s\.\d+$", extra):
                    basefile = root
            uri = self.make_sfs_url(basefile)
        else:
            if name[0].prefix == "user":
                uri = "https://lagen.nu/wiki/%s" % self.expand_page_name(*name)
            else:
                uri = self.make_keyword_url(name[1])
        return uri
