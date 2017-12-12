# base class that abstracts acess to the EUR-Lex web services and the Cellar repository. Uses CELEX ids for basefiles, but stores them sharded per year
# from zeep import Client
# from zeep.wsse.username import UsernameToken
from lxml import etree
from io import BytesIO
import requests
import os
import re
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
    languages = ["swe", "eng"]
    documentstore_class = EURLexStore
    download_accept_406 = True

    @classmethod
    def get_default_options(cls):
        opts = super(EURLex, cls).get_default_options()
        opts['languages'] = ['eng']
        return opts
    
    def download_get_first_page(self):
        envelope = """<soap-env:Envelope xmlns:soap-env="http://www.w3.org/2003/05/soap-envelope">
  <soap-env:Header>
    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <wsse:UsernameToken>
        <wsse:Username>%s</wsse:Username>
        <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">%s</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soap-env:Header>
  <soap-env:Body>
    <sear:searchRequest xmlns:sear="http://eur-lex.europa.eu/search">
      <sear:expertQuery>%s</sear:expertQuery>
      <sear:page>1</sear:page>
      <sear:pageSize>%s</sear:pageSize>
      <sear:searchLanguage>%s</sear:searchLanguage>
    </sear:searchRequest>
  </soap-env:Body>
</soap-env:Envelope>
""" % (self.config.username, self.config.password, self.query_template, self.pagesize, self.lang)
        headers = {'Content-Type': 'application/soap+xml; charset=utf-8; action="http://eur-lex.europa.eu/EURLexWebService/doQuery"',
                   'SOAPAction': '"http://eur-lex.europa.eu/EURLexWebService/doQuery"'}
        result = requests.post('http://eur-lex.europa.eu/EURLexWebService',
                               data=envelope,
                               headers=headers)
#        self.client = Client('http://eur-lex.europa.eu/eurlex-ws?wsdl',
#                             wsse=UsernameToken(self.config.username,
#                                                self.config.password))
#        with self.client.options(raw_response=True):
#            # if self.config.lastdownload: "AND DD >= 01/01/2017 <= 31/12/2017"
#            result = self.client.service.doQuery(expertQuery=self.query_template, # + " AND DD >= 01/01/1999",
#                                                 page=1,
#                                                 pageSize=self.pagesize,   
#                                                 searchLanguage=self.lang)
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
                cellarid = result.find(".//{http://eur-lex.europa.eu/search}reference").text
                cellarid = re.split("[:_]", cellarid)[2]
                # cellarid = result.find(".//{http://eur-lex.europa.eu/search}IDENTIFIER").text
                try:
                    title = result.find(".//{http://eur-lex.europa.eu/search}EXPRESSION_TITLE")[0].text
                except TypeError:
                    continue # if we don't have a title, we probably don't have this resource in the required language
                celex = result.find(".//{http://eur-lex.europa.eu/search}ID_CELEX")[0].text
                self.log.debug("%3s: %s %.55s %s" % (idx + 1, celex, title, cellarid))
                cellarurl = "http://publications.europa.eu/resource/cellar/%s?language=swe" % cellarid

                # find available languages for this document and yield if it contains our wanted languages
                languages = []
                for workexp in result.findall(".//{http://eur-lex.europa.eu/search}WORK_HAS_EXPRESSION"):
                    try:
                        lang = workexp.find(".//{http://eur-lex.europa.eu/search}OP-CODE").text
                        assert len(lang) == 3, "%s doesn't look like a language tag" % lang
                        languages.append(lang.lower())
                    except IndexError:
                        # the WORK_HAS_EXPRESSION node didn't look like we expected, oh well
                        pass

                for lang in self.config.languages:
                    if lang in languages:
                        cellarurl = "http://publications.europa.eu/resource/cellar/%s?language=%s" % (cellarid, lang)
                        yield celex, cellarurl
                        break
                else:
                    self.log.warning("%s: none of the wanted languages %s was in available languages %s" % (self.config.languages, languages))
            page += 1
            done = processedhits >= totalhits
            if not done:
                with self.client.options(raw_response=True):
                    result = self.client.service.doQuery(expertQuery=self.query_template,
                                                         page=page,
                                                         pageSize=self.pagesize,   
                                                         searchLanguage=self.lang)
                source = result.text

    def _addheaders(self, url, filename=None):
        headers = super(EURLex, self)._addheaders(filename)
        headers["Accept"] = "application/xhtml+xml"
        key, lang = url.split("?")[1].split("=")
        assert key == "language"
        headers["Accept-Language"] = lang
        return headers
