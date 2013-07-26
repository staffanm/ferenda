from . import DocumentRepository


class PDFDocumentRepository(DocumentRepository):
    """Base class for handling repositories of PDF documents. Parsing
    of these documents are a bit more complicated than HTML or text
    documents, particularly with the handling of external resources
    such as CSS and image files."""
    storage_policy = "dir"
    downloaded_suffix = ".pdf"

#    This implementation is specific for swedish legal material. Move
#    to swedishlegalsource and make storage_policy aware.
#
#    @classmethod
#    def basefile_from_path(cls,path):
#        # data/dirsou/downloaded/2006/84/index.pdf -> 2006:84
#        seg = path.split(os.sep)
#        seg = seg[seg.index(cls.alias)+2:-1]
#        seg = [x.replace("-","/") for x in seg]
#        assert 2 <= len(seg) <= 3, "list of segments is too long or too short"
#        # print "path: %s, seg: %r, basefile: %s" % (path,seg,":".join(seg))
#        return ":".join(seg)
    def parse_basefile(self, basefile):
        reader = self.pdfreader_from_basefile(basefile)
        doc = self.parse_from_pdfreader(reader, basefile)
        return doc

    def pdfreader_from_basefile(self, basefile):
        pdffile = self.downloaded_path(basefile)
        # Convoluted way of getting the directory of the intermediate
        # xml + png files that PDFReader will create
        intermediate_dir = os.path.dirname(
            self.generic_path(basefile, 'intermediate', '.foo'))
        self.setup_logger('pdfreader', self.config.loglevel)
        pdf = PDFReader()
        pdf.read(pdffile, intermediate_dir)
        return pdf

    def parse_from_pdfreader(self, pdfreader, basefile):
        doc = self.make_document()
        doc.uri = self.canonical_uri(basefile)
        doc.body = [pdfreader]

        d = Describer(doc.meta, doc.uri)
        d.rdftype(self.rdf_type)
        d.value(self.ns['prov']['wasGeneratedBy'], self.qualified_class_name())

        return doc

    def create_external_resources(self, doc):
        cssfile = self.generic_path(basefile, 'parsed', '.css')
        with open(cssfile, "w") as fp:
            # Create CSS header with fontspecs
            for pdf in doc.body:
                assert isinstance(pdf, PDFReader)
                for spec in list(pdf.fontspec.values()):
                    fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                             (spec['id'], spec['size'], spec['family'], spec['color']))

            # 2 Copy all created png files to their correct locations
            totcnt = 0
            src_base = os.path.splitext(
                pdf.filename)[0].replace("/downloaded/", "/intermediate/")
            dest_base = self.generic_path(
                basefile + "#background", "parsed", "")
            for pdf in doc.body:
                cnt = 0
                for page in pdf:
                    totcnt += 1
                    cnt += 1
                    src = "%s%03d.png" % (src_base, page.number)
                    dest = "%s%04d.png" % (dest_base, totcnt)  # 4 digits, compound docs can be over 1K pages
                    if util.copy_if_different(src, dest):
                        self.log.debug("Copied %s to %s" % (src, dest))

                    fp.write("#page%03d { background: url('%s');}\n" %
                             (cnt, os.path.basename(dest)))

    def list_external_resources(self, doc):
        parsed = self.parsed_path(doc.basefile)
        resource_dir = os.path.dirname(parsed)
        for f in [os.path.join(resource_dir, x) for x in os.listdir(resource_dir)
                  if os.path.join(resource_dir, x) != parsed]:
            yield f
