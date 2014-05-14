# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from rdflib import Namespace

from ferenda import DocumentRepository, PDFDocumentRepository

class ECMA(PDFDocumentRepository):
    alias = "ecma"
    # stnindex.htm contains groupings into categories, which might be preferrable 
    start_url = "http://www.ecma-international.org/publications/standards/Standard.htm" 
    document_url_template = "http://www.ecma-international.org/publications/standards/Ecma-%(basefile)s.htm"
    document_url_regex = "http://www.ecma-international.org/publications/standards/Ecma-(?P<basefile>\d+).htm"
    basefile_regex = "ECMA-(?P<basefile>\d+)"
    rdf_type = Namespace(util.ns['bibo']).Standard
    
