# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from collections import OrderedDict
import os
import re
import json
from io import BytesIO

from rdflib import URIRef
from rdflib.namespace import DCTERMS
from lxml import etree

from . import SwedishLegalStore, SwedishLegalSource, SwedishLegalHandler
from .elements import Sidbrytning
from ferenda import util
from ferenda import CompositeRepository, PDFReader
from ferenda.errors import DocumentRemovedError, RequestHandlerError, PDFFileIsEmpty
from ferenda.pdfreader import StreamingPDFReader
from ferenda.elements import Body



class FixedLayoutHandler(SwedishLegalHandler):
    def get_pathfunc(self, environ, basefile, params, contenttype, suffix):
        if basefile and suffix == "png":
            # OK, this is a request for a particular page. Map this to
            # correct repo, dir and attachment and set those params
            #pi = environ['PATH_INFO']
            #pageno = pi[pi.index("/sid")+4:-(len(suffix)+1)]
            pageno = params['pageno']
            if pageno.isdigit():
                pageno = int(pageno)
            if isinstance(self.repo, CompositeRepository):
                for subrepo in self.repo.subrepos:
                    repo = self.repo.get_instance(subrepo)
                    if (os.path.exists(repo.store.downloaded_path(basefile)) and
                        os.path.exists(repo.store.path(basefile, 'intermediate','.pagemapping.json'))):
                        break
                else:
                    # force the first available subrepo to get the file
                    # FIXME: It'd be great if we could force the
                    # subrepo who has the pagemapping file to
                    # download, but the CompositeRepository API
                    # doesn't allow that
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
            for candidatedir in ('downloaded', 'intermediate'):
                if os.path.exists(repo.store.path(basefile, candidatedir, '.dummy', attachment=attachment)):
                    params['dir'] = candidatedir
                    break
            else:
                raise RequestHandlerError("%s: Cannot find %s in any %s directory" % (basefile, attachment, repo.alias))
            params['page'] = str(int(pp) - 1)  # pp is 1-based, but RequestHandler.get_pathfunc expects 0-based
            params['format'] = 'png'
        return super(FixedLayoutHandler, self).get_pathfunc(environ, basefile, params, contenttype, suffix)
    

class FixedLayoutStore(SwedishLegalStore):
    """Handles storage of fixed-layout documents (either PDF or
    word processing docs that are converted to PDF). A single repo may
    have heterogenous usage of file formats, and this class will store
    each document with an appropriate file suffix.

    """

    doctypes = OrderedDict([
        (".pdf", b'%PDF'),
        (".rtf", b'{\\rt'),
        (".docx", b'PK\x03\x04'),
        (".doc", b'\xd0\xcf\x11\xe0'),
        (".wpd", b'\xffWPC')
    ])

    @property
    def downloaded_suffixes(self):
        return list(self.doctypes.keys())

    def guess_type(self, fp, basefile):
        assert False, "This seems to never be called?"
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
        opts['ocr'] = True
        return opts

    def downloaded_to_intermediate(self, basefile, attachment=None):
        # force just the conversion part of the PDF handling
        downloaded_path = self.store.downloaded_path(basefile, attachment=attachment)
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
            if self.config.ocr:
                self.log.warning("%s: %s was empty, attempting OCR" % (basefile, downloaded_path))
                ocr_lang = "swe" # reasonable guess
                return reader.convert(filename=downloaded_path,
                                      workdir=intermediate_dir,
                                      images=self.config.pdfimages,
                                      convert_to_pdf=convert_to_pdf,
                                      keep_xml=keep_xml,
                                      ocr_lang=ocr_lang)
            else:
                self.log.warning("%s: %s was empty, returning placeholder" % (basefile, downloaded_path))
                fp = BytesIO(b"""<pdf2xml>
                <page number="1" position="absolute" top="0" left="0" height="1029" width="701">
	        <fontspec id="0" size="12" family="TimesNewRomanPSMT" color="#000000"/>
                <text top="67" left="77" width="287" height="26" font="0">[Avg&#246;randetext saknas]</text>
                </page>
                </pdf2xml>""")
                fp.name = "dummy.xml"
                return fp
            
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
#        for r in body.findall(".//*[@class='sidbrytning']"):
            # each entry in the resource list may be a (resource,
            # extrametadata) tuple. The extrametadata is assumed to be
            # appended to by the caller as dictated by facets, then
            # passed as kwargs to FulltextIndex.update.
#            res.append((r, {"role": "autocomplete"}))
        return res

    def _relate_fulltext_value_comment(self, resourceuri, rooturi, desc):
        if "#sid" not in resourceuri:
            return super(FixedLayoutSource, self)._relate_fulltext_value_comment(resourceuri, rooturi, desc)
        else:
            pageno = resourceuri.split("#sid")[1]
            return "%s s. %s" % (desc.graph.value(URIRef(rooturi), DCTERMS.identifier),
                                 pageno)

    # FIXME: This is copied verbatim from PDFDocumentRepository

    
    def create_external_resources(self, doc):
        resources = []

        # there are two types of doc.body objects

        # 1. PDFReader objects, ie raw PDF objects, structured by page
        #    and with a top-level fontspec object
        # 2. elements.Body objects that are structured by logical
        #    elements (chapters, sections etc) and where individual
        #    Sidbrytning objects can be anywhere in the tree.
        if not hasattr(doc.body, 'fontspec'):
            # document wasn't derived from a PDF file, probably from HTML instead
            return resources
        cssfile = self.store.parsed_path(doc.basefile, attachment="index.css")
        urltransform = self.get_url_transform_func([self], os.path.dirname(cssfile),
                                                   develurl=self.config.develurl)
        resources.append(cssfile)
        util.ensure_dir(cssfile)
        with open(cssfile, "w") as fp:
            # Create CSS header with fontspecs
            for spec in list(doc.body.fontspec.values()):
                fp.write(".fontspec%s {font: %spx %s; color: %s;}\n" %
                         (spec['id'], spec['size'], spec['family'],
                          spec.get('color', 'black')))

            # 2 Copy all created png files to their correct locations
            if isinstance(doc.body, PDFReader):
                pageenumerator = enumerate(doc.body)
            else:
                sidbrytningar = []
                def collect(node, state):
                    if isinstance(node, Sidbrytning):
                        state.append(node)
                    return state
                self.visit_node(doc.body, collect, sidbrytningar)
                pageenumerator = enumerate(sidbrytningar)
            # assert isinstance(doc.body, PDFReader), "doc.body is %s, not PDFReader -- still need to access fontspecs etc" % type(doc.body)
            
            for cnt, page in pageenumerator:
                if page.background:
                    src = self.store.intermediate_path(
                        doc.basefile, attachment=os.path.basename(page.background))
                    dest = self.store.parsed_path(
                        doc.basefile, attachment=os.path.basename(page.background))
                    resources.append(dest)
                    if util.copy_if_different(src, dest):
                        self.log.debug("Copied %s to %s" % (src, dest))
                    desturi = "%s?dir=parsed&attachment=%s" % (doc.uri, os.path.basename(dest))
                    desturi = urltransform(desturi)
                    background = " background: url('%s') no-repeat grey;" % desturi
                else:
                    background = ""
                    
                fp.write("#%s {width: %spx; height: %spx;%s}\n" %
                         (page.id, page.width, page.height, background))
        return resources


    def _relate_fulltext_value_label(self, resourceuri, rooturi, desc):
        if "#sid" not in resourceuri:
            return super(FixedLayoutSource, self)._relate_fulltext_value_label(resourceuri, rooturi, desc)
        else:
            pageno = resourceuri.split("#sid")[1]
            return "s. %s" % pageno
