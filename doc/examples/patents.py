# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import tempfile
from ferenda import util

# mock methods
def download_from_api(): pass
def transform_patent_xml_to_xhtml(doc): pass
def screenscrape(): pass
def analyze_tagsoup(doc): pass
def ocr_and_structure(doc): pass
def do_the_work(basefile): pass

# begin subrepos
from ferenda import DocumentRepository, CompositeRepository
from ferenda.decorators import managedparsing

class XMLPatents(DocumentRepository):
    alias = "patxml"

    def download(self, basefile = None):
        download_from_api()

    @managedparsing
    def parse(self,doc):
        return self.transform_patent_xml_to_xhtml(doc)

class HTMLPatents(DocumentRepository):
    alias = "pathtml"
  
    def download(self, basefile=None):
        screenscrape()

    @managedparsing
    def parse(self,doc):
        return self.analyze_tagsoup(doc)

class ScannedPatents(DocumentRepository):
    alias = "patscan"

    # Assume that we, when we scanned the documents, placed them in their
    # correct place under data/patscan/downloaded

    def download(self, basefile=None): pass

    @managedparsing
    def parse(self,doc):
        x = self.ocr_and_structure(doc)
        return True

# end subrepos

    # stuff needed to get the above (and a call to parse()) to
    # actually work
    def ocr_and_structure(self, doc):
        return True # A-OK!
    def parseneeded(self, basefile):
        return True
    required_predicates = []
# begin composite
class CompositePatents(CompositeRepository):
    alias = "pat"
    # Specify the classes in order of preference for parsed documents. 
    # Only if XMLPatents does not have a specific patent will HTMLPatents
    # get the chance to provide it through it's parse method
    subrepos = XMLPatents, HTMLPatents, ScannedPatents

    def generate(self, basefile, otherrepos=[]):
        # Optional code to transform parsed XHTML1.1+RDFa documents
        # into browser-ready HTML5, regardless of wheter these are
        # derived from structured XML, tagsoup HTML or scanned
        # TIFFs. If your parse() method can make these parsed
        # documents sufficiently alike and generic, you might not need
        # to implement this method at all.
        self.do_the_work(basefile)
# end composite
    def do_the_work(self, basefile): pass

    
CompositePatents.subrepos = XMLPatents, HTMLPatents, ScannedPatents
# NB: The below code, which executes at import time, creates a temp
# dir and does everything there. However, this'll get run by
# composite-repository.sh, which already runs with cwd set to a temp
# dir, so this is unneccesary.

# datadir = tempfile.mkdtemp()
# d = CompositePatents(datadir=datadir)
# print("Doing things in %s" % datadir)
d = CompositePatents()
# make sure the files and methods we need actually exists
# util.writefile(datadir+"/patxml/downloaded/5723765.html", "hello world")
util.writefile("data/patxml/downloaded/5723765.html", "hello world")
setattr(XMLPatents, 'transform_patent_xml_to_xhtml', lambda x, y: True)
d.parse("5723765")
d.generate("5723765")
return_value = True
