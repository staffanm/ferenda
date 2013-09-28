# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# begin subrepos
from ferenda import DocumentRepository, CompositeRepository

class XMLPatents(DocumentRepository):
    alias = "patxml"

    def download(self, basefile = None):
        download_from_api()

    def parse(self,doc):
        transform_patent_xml_to_xhtml(doc)

class HTMLPatents(DocumentRepository):
    alias = "pathtml"
  
    def download(self, basefile=None):
        screenscrape()

    def parse(self,doc):
        analyze_tagsoup(doc)

class ScannedPatents(DocumentRepository):
    alias = "patscan"

    # Assume that we, when we scanned the documents, placed them in their
    # correct place under data/patscan/downloaded

    def download(self, basefile=None): pass

    def parse(self,doc):
        ocr_and_structure(doc)
# end subrepos

# begin composite
class CompositePatents(CompositeRepository):
    alias = "pat"
    # Specify the classes in order of preference for parsed documents. 
    # Only if XMLPatents does not have a specific patent will HTMLPatents
    # get the chance to provide it through it's parse method
    subrepos = XMLPatents, HTMLPatents, ScannedPatents

    def generate(self, basefile):
        # Optional code to transform parsed XHTML1.1+RDFa documents, regardless
        # of wheter these are derived from structured XML, tagsoup HTML
        # or scanned TIFFs. If your parse() method can make these parsed 
        # documents sufficiently alike and generic, you might not need to
        # implement this method at all.
        do_the_work()
# end composite

d = CompositePatents()
return_value = True
