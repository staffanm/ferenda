# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

# this repo overrides ferenda.sources.legal.se.Regeringen to work
# against old downloaded
import re
import codecs
# from urllib.parse import urljoin

from rdflib import URIRef
from rdflib.namespace import SKOS

from ferenda.sources.legal.se import Regeringen, RPUBL
from ferenda.sources.legal.se.direktiv import DirRegeringen
from ferenda.sources.legal.se.sou import SOURegeringen
from ferenda.sources.legal.se.ds import Ds
from ferenda.sources.legal.se.propositioner import PropRegeringen
from ferenda.compat import urljoin

from . import SameAs


class RegeringenLegacy(Regeringen):

    source_encoding = "iso-8859-1"

    def download(self, basefile=None):
        return False

    def downloaded_to_intermediate(self, basefile):
        return codecs.open(self.store.downloaded_path(basefile), encoding=self.source_encoding)
    
    # override just some of the methods to parse the HTML index page

    def extract_metadata(self, rawhead, basefile):
        content = rawhead
        title = content.find("h1").string
        identifier_node = content.find("p", "lead")
        if identifier_node:
            identifier = identifier_node.text
        else:
            identifier = ""  # infer_metadata calls infer_identifier
                             # if this is falsy, which will be good
                             # enough. No need to warn.
        definitions = content.find("dl", "definitions")
        if definitions:
            for dt in definitions.find_all("dt"):
                key = dt.get_text(strip=True)
                value = dt.find_next_sibling("dd").get_text(strip=True)
                if key == "Utgiven:":
                    utgiven = self.parse_swedish_date(value)
                elif key == "Avsändare:":
                    ansvarig = value 

        sammanfattning = None
        if content.find("h2", text="Sammanfattning"):
            sums = content.find("h2", text="Sammanfattning").find_next_siblings("p")
            # "\n\n" doesn't seem to survive being stuffed in a rdfa
            # content attribute. Replace with simple space.
            sammanfattning = " ".join([x.get_text(strip=True) for x in sums])
        
        # find related documents
        re_basefile = re.compile(r'\d{4}(|/\d{2,4}):\d+')
        # legStep1=Kommittedirektiv, 2=Utredning, 3=lagrådsremiss,
        # 4=proposition. Assume that relationships between documents
        # are reciprocal (ie if the page for a Kommittedirektiv
        # references a Proposition, the page for that Proposition
        # references the Kommittedirektiv.
        elements = {self.KOMMITTEDIREKTIV: [],
                    self.DS: ["legStep1"],
                    self.PROPOSITION: ["legStep1", "legStep2"],
                    self.SOU: ["legStep1"]}[self.document_type]
        utgarFran = []
        for elementid in elements:
            box = content.find(id=elementid)
            for listitem in box.find_all("li"):
                if not listitem.find("span", "info"):
                    continue
                infospans = [x.text.strip(
                ) for x in listitem.find_all("span", "info")]

                rel_basefile = None
                rel_identifier = None

                for infospan in infospans:
                    if re_basefile.search(infospan):
                        # scrub rel_identifier ("Dir. 2008:50" -> "2008:50" etc)
                        rel_basefile = re_basefile.search(infospan).group()
                        rel_identifier = infospan

                if not rel_basefile:
                    # this often means that a non-standard document
                    # type is used as preparatory work for this
                    # document (eg department memos not published in
                    # Ds, like "S2013/8074/PBB" -- seems to be common
                    # in Socialdepartementet and Finansdepartementet)
                    self.log.warning(
                        "%s: Couldn't find rel_basefile (elementid #%s) among %r" % (basefile, elementid, infospans))
                    continue

                attribs = {"rpubl:arsutgava": basefile.split(":")[0],
                           "rpubl:lopnummer": basefile.split(":")[1]}
                if elementid == "legStep1":
                    attribs["rdf:type"] = RPUBL.Kommittedirektiv
                elif elementid == "legStep2":
                    attribs["rdf:type"] = RPUBL.Utredningsbetankande
                    if rel_identifier.startswith("SOU"):
                        altlabel = "SOU"
                    elif rel_identifier.startswith(("Ds", "DS")):
                        altlabel = "Ds"
                    else:
                        self.log.warning(
                            "%s: Cannot find out what type of document the linked %s is (#%s)" % (basefile, rel_identifier, elementid))
                        continue
                    attribs["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
                elif elementid == "legStep3":
                    attribs["rdf:type"] = RPUBL.Proposition
                uri = self.minter.space.coin_uri(self.attributes_to_resource(attribs))
                utgarFran.append(uri)

        # find related pages
        related = content.find("h2", text="Relaterat")
        seealso = []
        if related:
            for link in related.findParent("div").find_all("a"):
                r = urljoin("http://www.regeringen.se/", link["href"])
                seealso.append(URIRef(r))

        a = self.metadata_from_basefile(basefile)
        a.update({'dcterms:title': title,
                  'dcterms:identifier': identifier,
                  'dcterms:issued': utgiven,
                  'rpubl:utgarFran': utgarFran,
                  'rpubl:departement': ansvarig
        })
        if seealso:
            a["rdfs:seeAlso"] = seealso
        if sammanfattning:
            a['dcterms:abstract'] = sammanfattning
        return a
    
    
    def find_pdf_links(self, soup, basefile, labels=False):
        pdffiles = []
        docsection = soup.find('div', 'doc')
        if docsection:
            for li in docsection.find_all("li", "pdf"):
                link = li.find('a')
                m = re.match(r'/download/(\w+\.pdf).*', link['href'], re.IGNORECASE)
                if not m:
                    continue
                pdfbasefile = m.group(1)
                pdffiles.append((pdfbasefile, link.string))
        selected = self.select_pdfs(pdffiles, labels)
        if not labels:
            self.log.debug("selected %s out of %d pdf files" % (", ".join(selected), len(pdffiles)))
        return selected

    def source_url(self, basefile):
        # as the old site is gone, there is no possible URL we can
        # return here.
        return None

class DirRegeringenLegacy(RegeringenLegacy, SameAs, DirRegeringen):
    alias = "dirregeringen.legacy"


class SOURegeringenLegacy(RegeringenLegacy, SameAs, SOURegeringen):
    alias = "souregeringen.legacy"


class DsRegeringenLegacy(RegeringenLegacy, SameAs, Ds):
    alias = "dsregeringen.legacy"


class PropRegeringenLegacy(RegeringenLegacy, SameAs, PropRegeringen):
    alias = "propregeringen.legacy"



