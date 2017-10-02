# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import OrderedDict
import os
import re
import json

from rdflib import URIRef
from rdflib.namespace import DCTERMS
from lxml import etree

from . import SwedishLegalStore, SwedishLegalSource, SwedishLegalHandler
from ferenda import util
from ferenda import CompositeRepository
from ferenda.errors import DocumentRemovedError, RequestHandlerError, PDFFileIsEmpty
from ferenda.pdfreader import StreamingPDFReader


class FixedLayoutHandler(SwedishLegalHandler):
    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        if basefile and suffix == "png":
            # OK, this is a request for a particular page. Map this to
            # correct repo, dir and attachment and set those params
            pi = environ['PATH_INFO']
            pageno = pi[pi.index("/sid")+4:-(len(suffix)+1)]
            if pageno.isdigit():
                pageno = int(pageno)
            if isinstance(self.repo, CompositeRepository):
                for subrepo in self.repo.subrepos:
                    repo = self.repo.get_instance(subrepo)
                    if os.path.exists(repo.store.downloaded_path(basefile)):
                        break
                else:
                    # force the first available subrepo to get the file
                    self.repo.download(basefile)
                    for subrepo in self.repo.subrepos:
                        repo = self.repo.get_instance(subrepo)
                        if os.path.exists(repo.store.downloaded_path(basefile)):
                            break
                    else:
                        raise RequestHandlerError("%s: No subrepo has downloaded this basefile" % basefile)
                
            else:
                repo = self.repo
            params['repo'] = repo.alias
            params['dir'] = "downloaded"
            pagemapping_path = repo.store.path(basefile, 'intermediate','.pagemapping.json')
            with open(pagemapping_path) as fp:
                pagemap = json.load(fp)
            # invert the map (only keep the first -- hmm, maybe pagemap isn't ordered?)
            invertedmap = {}
            for k, v in pagemap.items():
                if v not in invertedmap:
                    invertedmap[v] = k
            attachment, pp = invertedmap[pageno].split("#page=")
            params['attachment'] = attachment
            params['page'] = str(int(pp) - 1)  # pp is 1-based, but RequestHandler.get_pathfunc expects 0-based
            params['format'] = 'png'
        return super(FixedLayoutHandler, self).get_pathfunc(environ, basefile, params, contenttype, suffix)
    

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
            yielded = set()
            d = os.path.sep.join((basedir, "downloaded"))
            if not os.path.exists(d):
                return
            for x in util.list_dirs(d, self.doctypes.keys()):
                suffix = "/index" + os.path.splitext(x)[1]
                if not x.endswith(suffix):
                    continue  # this means we've recieved some
                              # attachment, not the main file
                pathfrag = x[len(d) + 1:-len(suffix)]
                basefile = self.pathfrag_to_basefile(pathfrag)
                if basefile not in yielded:
                    yielded.add(basefile)
                    yield basefile
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
    documentstore_class = FixedLayoutStore
    requesthandler_class = FixedLayoutHandler

    @classmethod
    def get_default_options(cls):
        opts = super(FixedLayoutSource, cls).get_default_options()
        opts['imgfiles'] = ['img/spinner.gif']
        return opts

    def downloaded_to_intermediate(self, basefile):
        # force just the conversion part of the PDF handling
        downloaded_path = self.store.downloaded_path(basefile)
        intermediate_path = self.store.intermediate_path(basefile)
        intermediate_dir = os.path.dirname(intermediate_path)
        ocr_lang = None
        convert_to_pdf = not downloaded_path.endswith(".pdf")
        keep_xml = "bz2" if self.config.compress == "bz2" else True
        reader = StreamingPDFReader()
        try:
            return reader.convert(filename=downloaded_path,
                                  workdir=intermediate_dir,
                                  images=self.config.pdfimages,
                                  convert_to_pdf=convert_to_pdf,
                                  keep_xml=keep_xml,
                                  ocr_lang=ocr_lang)
        except PDFFileIsEmpty as e:
            self.log.warning("%s: %s was empty, attempting OCR" % (basefile, downloaded_path))
            ocr_lang = "swe" # reasonable guess
            return reader.convert(filename=downloaded_path,
                                  workdir=intermediate_dir,
                                  images=self.config.pdfimages,
                                  convert_to_pdf=convert_to_pdf,
                                  keep_xml=keep_xml,
                                  ocr_lang=ocr_lang)
            
    def extract_head(self, fp, basefile):
        # at this point, fp points to the PDF file itself, which is
        # hard to extract metadata from. We just let extract_metadata
        # return anything we can infer from basefile
        pass

    def extract_metadata(self, rawhead, basefile):
        return self.metadata_from_basefile(basefile)
    
    def extract_body(self, fp, basefile):
        # If we can asssume that the fp is a hOCR HTML file and not a
        # PDF2XML file, use alternate parser. FIXME: There ought to be
        # a cleaner way than guessing based on filename
        parser = "ocr" if ".hocr." in util.name_from_fp(fp) else "xml"
        reader = StreamingPDFReader().read(fp, parser=parser)
        baseuri = self.canonical_uri(basefile)
        for page in reader:
            page.src = "%s/sid%s.png" % (baseuri, page.number)
        if reader.is_empty():
            raise DocumentRemovedError(dummyfile=self.store.parsed_path(basefile))
        else:
            return reader

    def _extract_plaintext(self, resource, resources):
        about = resource.get("about")
        if about and "#sid" in about:
            # select all text content contained in the first 2 <p>
            # tags following the pagebreak -- this should typically be
            # enough to show a helpful snippet in the autocomplete box
            nodes = resource.xpath("following::h:p[position() < 2]//text()",
                                   namespaces={'h': 'http://www.w3.org/1999/xhtml'})
            plaintext = util.normalize_space(" ".join(nodes))
            if not plaintext:
                plaintext = "(Sid %s saknar text)" % about.split("#sid")[1]
            return plaintext
        else:
            return super(FixedLayoutSource, self)._extract_plaintext(resource, resources)

    def _relate_fulltext_resources(self, body):
        res = super(FixedLayoutSource, self)._relate_fulltext_resources(body)
        # also: add every page (the pagebreak element)
        for r in body.findall(".//*[@class='sidbrytning']"):
            res.append(r)
        return res

    def _relate_fulltext_value_label(self, resourceuri, rooturi, desc):
        if "#sid" not in resourceuri:
            return super(FixedLayoutSource, self)._relate_fulltext_value_label(resourceuri, rooturi, desc)
        else:
            pageno = resourceuri.split("#sid")[1]
            return "%s s. %s" % (desc.graph.value(URIRef(rooturi), DCTERMS.identifier),
                                 pageno)
