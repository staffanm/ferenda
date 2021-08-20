import re
from datetime import datetime, date

import requests
import bs4
import json
import sys
import os.path

from ferenda import DocumentRepository, TextReader, DocumentEntry
from ferenda import util
from ferenda.decorators import downloadmax
from ferenda.decorators import managedparsing
from ferenda import Describer

from rdflib import Namespace, URIRef, BNode
import urllib.parse
import pandas as pd

from .betankande_parse import ParseBetankande

class Betankande(DocumentRepository):
    alias = "betankande"
    start_url_pattern = "https://www.riksdagen.se/api/search/GetDocumentsAndLawSearch?doktyp=bet&p=%(page)s"
    document_url_template = "https://www.riksdagen.se/sv/dokument-lagar/arende/betankande/%(basefile)s"
    downloaded_suffix = ".html"
    storage_policy="dir"
    
    @property
    def start_baseurl(self):
        proto, url = self.start_url_pattern.split("://")
        domain = url.split("/")[0]
        return "%s://%s" % (proto, domain)        
    
    def download_start(self):
        baseurl = self.start_baseurl
        
        pages = 1
        page = 1
        while page <= pages:
            page += 1
            r = requests.get(self.start_url_pattern % {"page": page})
            content = json.loads(r.content)
            if pages == 1:
                pages = content["sidor"]
            d = bs4.BeautifulSoup(content["html"], features="lxml")
            for url in (a.attrs["href"] for h2 in d.find_all("h2") for a in h2.find_all("a")):
                yield baseurl + url

    
    def download(self):
        self.log.debug("download: Start at %s" %  self.start_url_pattern)

        dmax = getattr(self.config, "downloadmax", sys.maxsize)
        if not isinstance(dmax, (int, type(None))):
            dmax = int(dmax)
        self.config.downloadmax = dmax
        
        for basefile, url in self.download_get_basefiles(self.download_start()):
            self.download_single(basefile, url)
    
    @downloadmax
    def download_get_basefiles(self, source):
        for url in source:
            yield url.split("_")[-1], url


    def download_single(self, basefile, url=None, orig_url=None):

        updated = False
        created = False

        filename = self.store.downloaded_path(basefile)
        created = not os.path.exists(filename) or os.path.getsize(filename) == 0
        
        if self.download_if_needed(url, basefile, archive=self.download_archive):
            if created:
                self.log.info("%s: download OK from %s" % (basefile, url))
            else:
                self.log.info(
                    "%s: download OK (new version) from %s" % (basefile, url))
            with open(filename) as f:
                content = f.read()
            for m in re.finditer(r"/api/vote/get/[-0-9a-f]*", content):
                voteurl = self.start_baseurl + m.string[m.start():m.end()]
                votefilename = self.store.downloaded_path(basefile, attachment=voteurl.split("/")[-1])
                self.download_if_needed(voteurl, basefile, filename=votefilename, archive=self.download_archive)
            for m in re.finditer(r"""/sv/dokument-lagar/dokument/proposition/[^"']*""", content):
                propurl = self.start_baseurl + m.string[m.start():m.end()]
                propfilename = self.store.downloaded_path(basefile, attachment=propurl.split("/")[-1])
                self.download_if_needed(propurl, basefile, filename=propfilename, archive=self.download_archive)
            for m in re.finditer(r"""/sv/dokument-lagar/dokument/motion/[^"']*""", content):
                moturl = self.start_baseurl + m.string[m.start():m.end()]
                motfilename = self.store.downloaded_path(basefile, attachment=moturl.split("/")[-1])
                self.download_if_needed(moturl, basefile, filename=motfilename, archive=self.download_archive)
            bet_id = re.findall(r'<span class="big">.* ([0-9][0-9][0-9][0-9]/[0-9][0-9]:.*)</span>', content)[0]
            lawlisturl = 'https://svenskforfattningssamling.se/sok/?q="' + urllib.parse.quote_plus(bet_id) + '"&op=S%C3%B6k'
            lawlistfilename = self.store.downloaded_path(basefile, attachment="lawlist")
            self.download_if_needed(lawlisturl, basefile, filename=lawlistfilename, archive=self.download_archive)
            updated = True
        else:
            self.log.debug("%s: exists and is unchanged" % basefile)
            
        entry = DocumentEntry(self.store.documententry_path(basefile))
        now = datetime.now()
        if orig_url is None:
            orig_url = url
        entry.orig_url = orig_url
        if created:
            entry.orig_created = now
        if updated:
            entry.orig_updated = now
        entry.orig_checked = now
        entry.save()

        return updated

    namespaces = ('rdf',  # always needed
                  'dcterms',  # title, identifier, etc
                  'bibo', # Standard and DocumentPart classes, chapter prop
                  'xsd',  # datatypes
                  ('parliament', 'http://lagen.nu/vocab/parliament#')
                  )

    @managedparsing
    def parse(self, doc):
        dirname = os.path.dirname(self.store.downloaded_path(doc.basefile))
        bet = ParseBetankande(dirname)

        desc = Describer(doc.meta, doc.uri)
        desc.rdftype(URIRef('https://www.riksdagen.se/sv/dokument-lagar/arende/betankande'))
        desc.value(self.ns['dcterms'].title, util.normalize_space(bet["title"]))
        desc.value(self.ns['dcterms'].identifier, "Betänkande " + bet["id"])

        for url in bet["Riksdagsskrivelse"]["links"].values():
            desc.rel(self.ns["parliament"].communication, URIRef(url))
        for url in bet["Protokoll med beslut"]["links"].values():
            desc.rel(self.ns["parliament"].record, URIRef(url))

        for title, proposal in bet["Förslagspunkter och beslut i kammaren"].items():
            with desc.rel(self.ns["dcterms"].hasPart, BNode(urllib.parse.quote(title))):
                desc.value(self.ns['dcterms'].title, title)
                for key, value in proposal.items():
                    if key in ("votering", "resultat"): continue
                    with desc.rel({True: self.ns["parliament"].approve,
                                   False: self.ns["parliament"].reject,
                                   "partial": self.ns["parliament"].partial}[value],
                                  URIRef(key)):
                        if key in bet["proposal-actions"]:
                            for propactkey, propactvalue in bet["proposal-actions"][key].items():
                                desc.rel({True: self.ns["parliament"].approve,
                                          False: self.ns["parliament"].reject,
                                          "partial": self.ns["parliament"].partial}[propactvalue],
                                         URIRef(propactkey))

                    # Collapse a votation that approved a proposal
                    # that in turn approves or rejects something into
                    # the votation approving or rejecting that
                    # directly.
                    if value is True and key in bet["proposal-actions"]:
                        for propactkey, propactvalue in bet["proposal-actions"][key].items():
                            desc.rel({True: self.ns["parliament"].approve,
                                      False: self.ns["parliament"].reject,
                                      "partial": self.ns["parliament"].partial}[propactvalue],
                                     URIRef(propactkey))
                        
                if proposal["votering"] == "acklamation":
                    desc.rel(self.ns["dcterms"].creator, URIRef(self.ns["parliament"].acclamation))
                else:
                    with desc.rel(self.ns["dcterms"].creator, proposal["votering"]):
                        with open(os.path.join(dirname, proposal["votering"].split("/")[-1])) as f:
                            vote_content = f.read()
                        vote_doc = bs4.BeautifulSoup(json.loads(vote_content)["html"], features="lxml")
                        headings = [th.get_text() for th in vote_doc.find(class_="vottabell").find(class_="vottabellrubik").find_all("th")]
                        rows = [[td.get_text().strip() for td in tr.find_all("td")] for tr in vote_doc.find(class_="vottabell").find("tbody").find_all("tr")]
                        votes = pd.DataFrame(rows, columns=headings)

                        for idx, row in votes[["Parti", "Röst"]].value_counts().rename("count").reset_index().iterrows():
                            with desc.rel(self.ns["dcterms"].hasPart, BNode(urllib.parse.quote("%(Parti)s-%(Röst)s" % row))):
                                desc.rel(self.ns["parliament"].party, URIRef("http://rinfo.lagrummet.se/org/riksdag/member/%(Parti)s" % row))
                                desc.rel(self.ns["parliament"].vote,
                                           {"Ja": URIRef(self.ns["parliament"].yes),
                                            "Nej": URIRef(self.ns["parliament"].no),
                                            "Avstår": URIRef(self.ns["parliament"].blank),
                                            "Frånvarande": URIRef(self.ns["parliament"].absent)
                                           }[row["Röst"]])
                                desc.value(self.ns["parliament"]["count"], row["count"])
                    
                desc.rel(self.ns["dcterms"].valid,
                           {True: URIRef(self.ns["parliament"].yes),
                            False: URIRef(self.ns["parliament"].no),
                            "partial": URIRef(self.ns["parliament"].partial)
                           }[proposal["resultat"]])

        #print(doc.meta.serialize(format="trig").decode("utf-8"))
        #print()
        #print()
