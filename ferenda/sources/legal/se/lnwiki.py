# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# This is a set of subclasses to regular Wiki and scm.mw classes to
# customize behaviour.

# system
import unicodedata
import os

# 3rdparty
from lxml import etree

# mine
from ferenda import DocumentStore
from ferenda.sources.legal.se import SwedishLegalSource, SwedishCitationParser, SFS, LNKeyword
from ferenda.sources.general.wiki import MediaWiki, MediaWikiStore, WikiSemantics, WikiSettings

class LNMediaWikiStore(MediaWikiStore):

    # the pathfrag mangling in MediaWikiStore is not suitable for the
    # content of the Lagen.nu wiki, which in practice has a highly
    # heterogenous usage of : and /, which makes general roundtripping
    # from basefile -> pathfrag -> basefile impossible . Therefore we
    # fall back to another behaviour which should be roundtrippable
    def basefile_to_pathfrag(self, basefile):
        return basefile.replace(":", os.sep+"%3E").replace(" ", "_")

    def pathfrag_to_basefile(self, pathfrag):
        return unicodedata.normalize("NFC", pathfrag.replace(os.sep+"%3E", ":").replace("_", " "))

class LNMediaWiki(MediaWiki):
    """Managing commentary on legal sources (Lagen.nu-version of MediaWiki)
    """
    namespaces = SwedishLegalSource.namespaces
    documentstore_class = LNMediaWikiStore

    from ferenda.sources.legal.se.legalref import LegalRef
    keyword_class = LNKeyword

    p = LegalRef(LegalRef.LAGRUM, LegalRef.KORTLAGRUM,
                 LegalRef.FORARBETEN, LegalRef.RATTSFALL)
    lang = "sv"
    
    def __init__(self, config=None, **kwargs):
        super(LNMediaWiki, self).__init__(config, **kwargs)
        if self.config._parent and hasattr(self.config._parent, "sfs"):
            self.sfsrepo = SFS(self.config._parent.sfs)
        else:
            self.sfsrepo = SFS()

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
        citparser = SwedishCitationParser(self.p, self.config.url,
                                          allow_relative=allow_relative)
        citparser.parse_recursive(body, predicate=None)
        return body

    def postprocess_commentary(self, doc, xhtmltree):
        uri = doc.uri
        body = xhtmltree.getchildren()[0]
        newbody = etree.Element("body")

        curruri = uri
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
                    nodes = self.p.parse(txt, curruri)
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
        

class LNSemantics(WikiSemantics):
    def internal_link(self, ast):
        el = super(LNSemantics, self).internal_link(ast)
        return el


class LNSettings(WikiSettings):
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
            uri = self.make_sfs_url(name[1][4:])
        else:
            if name[0].prefix == "user":
                uri = "https://lagen.nu/wiki/%s" % self.expand_page_name(*name)
            else:
                uri = self.make_keyword_url(name[1])
        return uri
        
