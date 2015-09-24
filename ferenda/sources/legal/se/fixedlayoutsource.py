import os
import itertools

from . import SwedishLegalStore, SwedishLegalSource

from ferenda import util
from ferenda.compat import OrderedDict
from ferenda.pdfreader import StreamingPDFReader
from .swedishlegalsource import offtryck_parser, offtryck_gluefunc


class FixedLayoutStore(SwedishLegalStore):
    """Handles storage of fixed-layout documents (either PDF or
    word processing docs that are converted to PDF). A single repo may
    have heterogenous usage of file formats, and this class will store
    each document with an appropriate file suffix.

    """

    downloaded_suffix = ".pdf"  # this is the default
    doctypes = OrderedDict([(".wpd", b'\xffWPC'),
                            (".doc", b'\xd0\xcf\x11\xe0'),
                            (".docx", b'PK\x03\x04'),
                            (".rtf", b'{\\rt'),
                            (".pdf", b'%PDF')])

    def downloaded_path(self, basefile, version=None, attachment=None,
                        suffix=None):
        if not suffix:
            for s in self.doctypes:
                if os.path.exists(self.path(basefile, "downloaded", s)):
                    suffix = s
                    break
            else:
                suffix = self.downloaded_suffix
        return self.path(basefile, "downloaded", suffix, version, attachment)

    def list_basefiles_for(self, action, basedir=None):
        if not basedir:
            basedir = self.datadir
        if action == "parse":
            d = os.path.sep.join((basedir, "downloaded"))
            if not os.path.exists(d):
                return
            iterators = (util.list_dirs(d, x) for x in self.doctypes)
            for x in sorted(itertools.chain(*iterators)):
                suffix = "/index" + os.path.splitext(x)[1]
                pathfrag = x[len(d) + 1:-len(suffix)]
                yield self.pathfrag_to_basefile(pathfrag)
        else:
            for x in super(FixedLayoutStore,
                           self).list_basefiles_for(action, basedir):
                yield x

    def guess_type(self, fp, basefile):
        start = fp.tell()
        sig = fp.read(4)
        fp.seek(start)
        for s in self.doctypes:
            if sig == self.doctypes[s]:
                return s
        else:
            self.log.error("%s: document file stream has magic number %r "
                           "-- don't know what that is" % (basefile, sig))
            # FIXME: Raise something instead?


class FixedLayoutSource(SwedishLegalSource):
    """This is basically like PDFDocumentRepository, but handles other
    word processing formats along with PDF files (everything is
    converted to/handled as PDF internally) """

    downloaded_suffix = ".pdf"
    
    def downloaded_to_intermediate(self, basefile):
        # force just the conversion part of the PDF handling
        downloaded_path = self.store.downloaded_path(basefile)
        intermediate_path = self.store.intermediate_path(basefile)
        intermediate_dir = os.path.dirname(intermediate_path)
        ocr_lang = None
        convert_to_pdf = not downloaded_path.endswith(".pdf"),
        keep_xml = "bz2" if self.config.compress == "bz2" else True
        reader = StreamingPDFReader()
        return reader.convert(filename=self.store.downloaded_path(basefile),
                              workdir=intermediate_dir,
                              images=self.config.pdfimages,
                              convert_to_pdf=convert_to_pdf,
                              keep_xml=keep_xml,
                              ocr_lang=ocr_lang)

    def parse_metadata(self, file, basefile):
        # at this point, we only have the PDF file itself, which is
        # hard to extract metadata from. We just return anything we
        # can infer from basefile
        resource = self.polish_metadata(self.metadata_from_basefile(basefile))
        self.infer_metadata(resource, basefile)
        return resource
    
    def extract_body(self, fp, basefile):
        return StreamingPDFReader().read(fp)

    def get_parser(self, basefile, sanitized):
        return offtryck_parser(basefile, preset=self.alias,
                               identifier=self.infer_identifier(
                                   basefile)).parse

    def tokenize(self, pdfreader):
        return pdfreader.textboxes(offtryck_gluefunc)
