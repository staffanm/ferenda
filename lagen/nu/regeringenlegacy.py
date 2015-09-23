# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# this repo overrides ferenda.sources.legal.se.Regeringen to work
# against old downloaded
import re

from bs4 import BeautifulSoup
from rdflib import URIRef
from rdflib.namespace import SKOS
from six.moves.urllib_parse import urljoin

from ferenda.sources.legal.se import Regeringen, RPUBL
from ferenda.sources.legal.se.direktiv import DirRegeringen
from ferenda.sources.legal.se.sou import SOURegeringen
from ferenda.sources.legal.se.ds import Ds
from ferenda.sources.legal.se.propositioner import PropRegeringen

from . import SameAs


class RegeringenLegacy(Regeringen):
    def download(self, basefile=None):
        return False

    # override just some of the methods to parse the HTML index page
    def extract_head(self, fp, basefile):
        parser = 'lxml'
        soup = BeautifulSoup(fp.read(), parser)
        self._rawbody = soup.body
        return self._rawbody.find(id="content")

    def extract_metadata(self, rawhead, basefile):
        content = rawhead
        title = content.find("h1").string
        identifier = content.find("p", "lead").text
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
                identifier = None

                for infospan in infospans:
                    if re_basefile.search(infospan):
                        # scrub identifier ("Dir. 2008:50" -> "2008:50" etc)
                        rel_basefile = re_basefile.search(infospan).group()
                        identifier = infospan

                if not rel_basefile:
                    self.log.warning(
                        "%s: Couldn't find rel_basefile (elementid #%s) among %r" % (doc.basefile, elementid, infospans))
                    continue

                attribs = {"rpubl:arsutgava": basefile.split(":")[0],
                           "rpubl:lopnummer": basefile.split(":")[1]}
                if elementid == "legStep1":
                    attribs["rdf:type"] = RPUBL.Kommittedirektiv
                elif elementid == "legStep2":
                    attribs["rdf:type"] = RPUBL.Utredningsbetankande
                    if identifier.startswith("SOU"):
                        altlabel = "SOU"
                    elif identifier.startswith(("Ds", "DS")):
                        altlabel = "Ds"
                    else:
                        self.log.warning(
                            "Cannot find out what type of document the linked %s is (#%s)" % (identifier, elementid))
                    attribs["rpubl:utrSerie"] = self.lookup_resource(altlabel, SKOS.altLabel)
                elif elementid == "legStep3":
                    attribs["rdf:type"] = RPUBL.Proposition
                uri = self.minter.space.coin_uri(self.attributes_to_resource(attribs, for_self=False))
                utgarFran.append(uri)

        # find related pages
        related = content.find("h2", text="Relaterat")
        seealso = []
        if related:
            for link in related.findParent("div").find_all("a"):
                r = urljoin("http://www.regeringen.se/", link["href"])
                seealso.append(URIRef(r))


        ret = {'dcterms:title': title,
               'dcterms:identifier': identifier,
               'dcterms:issued': utgiven,
               'rpubl:utgarFran': utgarFran,
               'rpubl:departement': ansvarig,
               "rdfs:seeAlso": seealso
        }
        if sammanfattning:
            ret['dcterms:abstract'] = sammanfattning

        return ret
    
    
    def find_pdf_links(self, soup, basefile):
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
        selected = self.select_pdfs(pdffiles)
        
        self.log.debug("selected %s out of %d pdf files" % (", ".join(selected), len(pdffiles)))
        return [p.replace(".pdf", "") for p in selected]

class DirRegeringenLegacy(RegeringenLegacy, SameAs, DirRegeringen):
    alias = "dirregeringen.legacy"


class SOURegeringenLegacy(RegeringenLegacy, SameAs, SOURegeringen):
    alias = "souregeringen.legacy"


class DsRegeringenLegacy(RegeringenLegacy, SameAs, Ds):
    alias = "dsregeringen.legacy"


class PropRegeringenLegacy(RegeringenLegacy, SameAs, PropRegeringen):
    alias = "propregeringen.legacy"



