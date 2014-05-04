# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import sys
if sys.version_info[:2] == (3,2): # remove when py32 support ends
    import uprefix
    uprefix.register_hook()
    from future.builtins import *
    uprefix.unregister_hook()
else:
    from future.builtins import *

import os

from ferenda import util
from ferenda import DocumentRepository, Describer, PDFReader
from ferenda.decorators import managedparsing
from ferenda.elements import Body


class PDFDocumentRepository(DocumentRepository):

    """Base class for handling repositories of PDF documents. Parsing
    of these documents are a bit more complicated than HTML or text
    documents, particularly with the handling of external resources
    such as CSS and image files."""
    storage_policy = "dir"
    downloaded_suffix = ".pdf"

    def get_default_options(self):
        opts = super(PDFDocumentRepository, self).get_default_options()
        opts['pdfimages'] = True
        return opts

    @managedparsing
    def parse(self, doc):
        reader = self.pdfreader_from_basefile(doc.basefile)
        self.parse_from_pdfreader(reader, doc)
        # return doc
        return True

    def pdfreader_from_basefile(self, basefile):
        pdffile = self.store.downloaded_path(basefile)
        # Convoluted way of getting the directory of the intermediate
        # xml + png files that PDFReader will create

        intermediate_dir = os.path.dirname(self.store.intermediate_path(basefile))
        pdf = PDFReader()

        if self.config.compress == "bz2":
            keep_xml = "bz2"
        else:
            keep_xml = True
        pdf.read(pdffile, intermediate_dir,
                 images=self.config.pdfimages,
                 keep_xml=keep_xml)
        return pdf

    def parse_from_pdfreader(self, pdfreader, doc):
        doc.body = Body([pdfreader])

        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())

        return doc

    def create_external_resources(self, doc):
        cssfile = self.store.parsed_path(doc.basefile, attachment="index.css")
        with open(cssfile, "w") as fp:
            # Create CSS header with fontspecs
            for pdf in doc.body:
                assert isinstance(pdf, PDFReader)
                for spec in list(pdf.fontspec.values()):
                    fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                             (spec['id'], spec['size'], spec['family'], spec['color']))

            # 2 Copy all created png files to their correct locations
            totcnt = 0
            pdfbase = os.path.splitext(os.path.basename(pdf.filename))[0]
            for pdf in doc.body:
                cnt = 0
                for page in pdf:
                    totcnt += 1
                    cnt += 1
                    if page.background:
                        src = self.store.intermediate_path(
                            doc.basefile, attachment=os.path.basename(page.background))
                        dest = self.store.parsed_path(
                            doc.basefile, attachment=os.path.basename(page.background))
                        if util.copy_if_different(src, dest):
                            self.log.debug("Copied %s to %s" % (src, dest))

                        fp.write("#page%03d { background: url('%s');}\n" %
                                 (cnt, os.path.basename(dest)))
