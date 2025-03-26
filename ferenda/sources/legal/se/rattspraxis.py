
# From python stdlib
import re
import os
import json
from datetime import datetime, timedelta

# 3rd party modules
import lxml.html
import requests
from rdflib import Literal, URIRef
from rdflib.namespace import SKOS, XSD, DCTERMS, FOAF
from bs4 import BeautifulSoup

# My own stuff
from ferenda import FSMParser, DocumentEntry, DocumentStore
from ferenda import decorators, util
from ferenda.elements import Body, Paragraph
from ferenda.errors import DownloadError
from . import RPUBL, SwedishLegalSource
from .fixedlayoutsource import FixedLayoutSource
from .swedishlegalsource import UnorderedSection
from .elements import *


class RattspraxisStore(DocumentStore):
    def basefile_to_pathfrag(self, basefile):
        return basefile.replace("/", os.sep)

    def pathfrag_to_basefile(self, pathfrag):
        return pathfrag.replace(os.sep, "/")

class Rattspraxis(SwedishLegalSource):

    """Hanterar beslut från domstolsverkets tjänst Sök Rättspraxis"""
    alias = "rattspraxis"
    start_url = "https://rattspraxis.etjanst.domstol.se/api/v1/sok"
    rdf_type = (RPUBL.Rattsfallsreferat, RPUBL.Rattsfallsnotis)
    documentstore_class = RattspraxisStore
    pagesize = 100 # you can set it to a higher number but it won't help, the API only returns 20 items per page
    downloaded_suffix = ".json"
    storage_policy = "dir"
    @decorators.action
    @decorators.recordlastdownload
    def download(self, basefile=None, url=None):
        if basefile:
            raise NotImplementedError("Can't download single documents from Rättspraxis")

        self.session = requests.session()

        for basefile, url in self.download_get_basefiles(self.start_url):
            self.download_single(basefile, url)

    @decorators.downloadmax
    def download_get_basefiles(self, start_url):
        payload = {
            "antalPerSida": self.pagesize,
            "asc": False,
            "filter": {
                "avgorandeTypLista": [],
                "intervall": {
                    "fromDatum": None,
                    "toDatum": None
                },
                "rattsomradeLista": [],
                "sfsNummerLista": [],
                "sokordLista": []
            },
            "sidIndex": 0,
            "sokfras": {
                "andLista": [],
                "notLista": [],
                "orLista": []
            },
            "sortorder": "avgorandedatum"
        }
        if ('lastdownload' in self.config and
                self.config.lastdownload and
                not self.config.refresh):
            startdate = self.config.lastdownload - timedelta(days=30)
            payload["filter"] = {
                "avgorandeTypLista": [],
                "intervall": {
                    "fromDatum":  datetime.strftime(startdate, "%Y-%m-%d"),
                    "toDatum": None
                },
                "rattsomradeLista": [],
                "domstolKodLista": [],
                "sfsNummerLista": [],
                "sokordLista": []
            }

        done = False
        pagecount = 0
        self.log.debug("Starting at %s" % start_url)
        while not done:
            payload["sidIndex"] = pagecount
            resp = self.session.post(start_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            self.log.info(f"Page {pagecount+1} of {(1+data['total']) // self.pagesize}) ({data['total']} total)")
            for item in data['publiceringLista']:
                assert len(item['malNummerLista']) > 0 or len(item['referatNummerLista']) == 1, f"Expected one or more of malNummerLista, alternatively only one of referatNummerLista ({len(item['malNummerLista'])}, {len(item['referatNummerLista'])})"
                if item['referatNummerLista']:
                    "NJA 2004 s. 1" => "NJA/2004/s1"
                    "NJA 2004 not 1" => "NJA/2004/not1"
                    "AD 2004 nr 1"
                    "HFD 2004 ref. 1"


                    basefile = f"{item['domstol']['domstolKod']}/{item['referatNummerLista'][0].replace(" ", "").replace(":", "_")}"
                else:
                    basefile = f"{item['domstol']['domstolKod']}/{item['malNummerLista'][0].replace(" ", "")}"
                uri = f"https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar/grupp/{item['gruppKorrelationsnummer']}"
                # yield basefile, {'uri': uri}
                yield basefile, uri
            if data['total'] > (pagecount + 1) * self.pagesize:
                pagecount += 1
            else:
                self.log.info(f"Done, {data['total']} items, {(pagecount+1) * self.pagesize} is bigger")
                done = True

    def download_single(self, basefile, url):
        if not os.path.exists(self.store.downloaded_path(basefile)) or self.config.refresh:
            ret = super(Rattspraxis, self).download_single(basefile, url)
            data = json.loads(util.readfile(self.store.downloaded_path(basefile)))
            if len(data) != 1:
                self.log.warning(f"{basefile}: {len(data)} items found in response, expected 1")
            for item in data:
                if item['bilagaLista']:
                    for bilaga in item['bilagaLista']:
                        attachmentpath = self.store.downloaded_path(basefile, attachment=bilaga['filnamn'])
                        url = f"https://rattspraxis.etjanst.domstol.se/api/v1/bilagor/{bilaga['fillagringId'].replace("/", "%2F")}"
                        self.download_if_needed(url, basefile, filename=attachmentpath)
            return ret
