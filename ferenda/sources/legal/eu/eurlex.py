# base class that abstracts acess to the EUR-Lex web services and the Cellar repository. Uses CELEX ids for basefiles, but stores them sharded per year
from zeep import Client
from zeep.wsse.username import UsernameToken
from lxml import etree
from io import BytesIO
import requests
import os
from math import ceil

from ferenda import util, decorators
from ferenda import DocumentRepository, DocumentStore

class EURLexStore(DocumentStore):
    def basefile_to_pathfrag(self, basefile):
        if basefile.startswith("."):
            return basefile
        # Shard all files under year, eg "32017R0642" => "2017/32017R0642"
        year = basefile[1:5]
        assert year.isdigit()
        return "%s/%s" % (year, basefile)

    def pathfrag_to_basefile(self, pathfrag):
        if pathfrag.startswith("."):
            return pathfrag
        year, basefile = pathfrag.split("/", 1)
        return basefile
    

class EURLex(DocumentRepository):
    alias = "eurlex"
    start_url = "http://eur-lex.europa.eu/eurlex-ws?wsdl"
    pagesize = 100 # max allowed by the web service
    query_template = "" # sub classes adjust this
    download_iterlinks = False
    lang = "sv"
    llang = "swe"
    documentstore_class = EURLexStore
    download_accept_406 = True
    
    def download_get_first_page(self):
        self.client = Client('http://eur-lex.europa.eu/eurlex-ws?wsdl',
                             wsse=UsernameToken(self.config.username,
                                                self.config.password))
        with self.client.options(raw_response=True):
            result = self.client.service.doQuery(expertQuery=self.query_template,
                                                 page=1,
                                                 pageSize=self.pagesize,   
                                                 searchLanguage=self.lang)
        return result

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        totalhits = None
        done = False
        page = 1
        processedhits = 0
        while not done:
            tree = etree.parse(BytesIO(source.encode("utf-8")))
            if totalhits is None:
                totalhits = int(tree.find(".//{http://eur-lex.europa.eu/search}totalhits").text)
                self.log.info("Total hits: %s" % totalhits)
            for idx, result in enumerate(tree.findall(".//{http://eur-lex.europa.eu/search}result")):
                processedhits += 1
                # cellarid = result.find(".//{http://eur-lex.europa.eu/search}reference").text
                cellarid = result.find(".//{http://eur-lex.europa.eu/search}IDENTIFIER").text
                try:
                    title = result.find(".//{http://eur-lex.europa.eu/search}EXPRESSION_TITLE")[0].text
                except TypeError:
                    continue # if we don't have a title, we probably don't have this resource in the required language
                celex = result.find(".//{http://eur-lex.europa.eu/search}ID_CELEX")[0].text
                self.log.debug("%3s: %s %.55s %s" % (idx + 1, celex, title, cellarid))
                cellarurl = "http://publications.europa.eu/resource/cellar/%s?language=swe" % cellarid
                yield celex, cellarurl
            page += 1
            done = processedhits >= totalhits
            if not done:
                with self.client.options(raw_response=True):
                    result = self.client.service.doQuery(expertQuery=self.query_template,
                                                         page=page,
                                                         pageSize=self.pagesize,   
                                                         searchLanguage=self.lang)
                source = result.text

    def _addheaders(self, filename=None):
        headers = super(EURLex, self)._addheaders(filename)
        headers["Accept"] = "application/xhtml+xml"
        headers["Accept-Language"] = self.llang
        return headers
