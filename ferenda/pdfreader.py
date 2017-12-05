# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from bz2 import BZ2File
from glob import glob
from io import BytesIO
import itertools
import logging
import os
import re
import shutil
import tempfile
import warnings
import unicodedata

from lxml import etree
from lxml.builder import ElementMaker
from layeredconfig import LayeredConfig, Defaults
from cached_property import cached_property

from ferenda import util, errors
from ferenda.fsmparser import Peekable
from ferenda.elements import serialize
from ferenda.elements import UnicodeElement, CompoundElement, OrdinalElement

E = ElementMaker(namespace="http://www.w3.org/1999/xhtml",
                 nsmap={None: "http://www.w3.org/1999/xhtml"})

class PDFReader(CompoundElement):

    """Parses PDF files and makes the content available as a object
    hierarchy. Calling the :py:meth:`~ferenda.PDFReader.read` method
    returns a :py:class:`ferenda.pdfreader.PDFFile` object, which is a
    list of :py:class:`ferenda.pdfreader.Page` objects, which each is
    a list of :py:class:`ferenda.pdfreader.Textbox` objects, which
    each is a list of :py:class:`ferenda.pdfreader.Textelement`
    objects.

    .. note::

       This class depends on the command line tool pdftohtml from
       `poppler <http://poppler.freedesktop.org/>`_.

       The class can also handle any other type of document (such as
       Word/OOXML/WordPerfect/RTF) that OpenOffice or LibreOffice
       handles by first converting it to PDF using the ``soffice``
       command line tool (which then must be in your ``$PATH``).

       If the PDF contains only scanned pages (without any OCR
       information), the pages can be run through the ``tesseract``
       command line tool (which, again, needs to be in your
       ``$PATH``). You need to provide the main language of the
       document as the ``ocr_lang`` parameter, and you need to have
       installed the tesseract language files for that language.

    """

    ################################################################
    # properties and methods relating to the initialization of the
    # PDFReader object

    detect_footnotes = True

    def __init__(self,
                 pages=None,
                 filename=None,
                 workdir=None,
                 images=True,
                 convert_to_pdf=False,
                 keep_xml=True,
                 ocr_lang=None,
                 fontspec=None,
                 textdecoder=None):
        """Initializes a PDFReader object from an existing PDF file. After
        initialization, the PDFReader contains a list of
        :py:class:`~ferenda.pdfreader.Page` objects.


        :param pages: Internal parameter. You should not specify
                      this. Specify all other parameters using
                      keywords.
        :param filename: The full path to the PDF file (or, if
                        ``convert_to_pdf`` is set, any other document
                        file)
        :param workdir: A directory where intermediate files
                        (particularly background PNG files) are
                        stored. If not provided, a temporary directory
                        will be created and be available as the
                        ``workdir`` property of the object.
        :param convert_to_pdf: If filename is any other type of
                               document other than PDF, attempt to
                               first convert it to PDF using the
                               ``soffice`` command line tool (from
                               OpenOffice/LibreOffice).
        :type  convert_to_pdf: bool
        :param keep_xml: If False, remove the intermediate XML
                         representation of the PDF that gets created
                         in ``workdir``. If true, keep it around to
                         speed up subsequent parsing operations. If
                         set to the special value ``"bz2"``, keep it
                         but compress it with :py:mod:`bz2`.
        :type  keep_xml: bool
        :param ocr_lang: If provided, PDFReader will extract scanned
                         images from the PDF file, and run an OCR
                         program on it, using the ``ocr_lang``
                         language heuristics. (Note that this is not
                         neccessarily an IETF language tag like "sv"
                         or "en-GB", but rather whatever the
                         underlying ``tesseract`` program uses).
        :param ocr_lang: str

        """
        self.log = logging.getLogger('pdfreader')
        if pages:  # special-case: The object has been initialized as a
                  # regular list (by deserialize), we have no need to
                  # parse and create pages.
            return
        if not filename:
            return  # another specialcase: create an empty object so
            # that we can call the ._tesseract in other
            # scenarios
        self.fontspec = fontspec or {}
        self.filename = filename
        self.workdir = workdir
        if self.workdir is None:
            self.workdir = tempfile.mkdtemp()
        if textdecoder is None:
            self._textdecoder = BaseTextDecoder()
        else:
            self._textdecoder = textdecoder


        # FIXME: For testing, we'd like to avoid this conversation if
        # we already have the real_convertedfile that we'll end up
        # with, in order to not convert to PDF needlessly
        if convert_to_pdf:
            newfilename = workdir + os.sep + \
                os.path.splitext(os.path.basename(filename))[0] + ".pdf"
            if not os.path.exists(newfilename):
                util.ensure_dir(newfilename)
                cmdline = "soffice --headless --convert-to pdf --outdir '%s' %s" % (
                    workdir, filename)
                self.log.debug("%s: Converting to PDF: %s" % (filename, cmdline))
                (ret, stdout, stderr) = util.runcmd(
                    cmdline, require_success=True)
                filename = newfilename

        assert os.path.exists(filename), "PDF %s not found" % filename
        basename = os.path.basename(filename)
        stem = os.path.splitext(basename)[0]

        if ocr_lang:
            suffix = ".hocr.html"
            converter = self._tesseract
            converter_extra = {'lang': ocr_lang}
            parser = self._parse_hocr
        else:
            suffix = ".xml"
            converter = self._pdftohtml
            converter_extra = {'images': images}
            parser = self._parse_xml
        convertedfile = os.sep.join([workdir, stem + suffix])
        if keep_xml == "bz2":
            real_convertedfile = convertedfile + ".bz2"
        else:
            real_convertedfile = convertedfile
        tmpfilename = os.sep.join([workdir, basename])
        # copying the filename to the workdir is only needed if we use
        # PDFReader._pdftohtml

        if not util.outfile_is_newer([filename], real_convertedfile):
            util.copy_if_different(filename, tmpfilename)
            # this is the expensive operation
            res = converter(tmpfilename, workdir, **converter_extra)
            # print("contents of workdir %s after conversion: %r" % (workdir, os.listdir(workdir)))
            if keep_xml == "bz2":
                with open(convertedfile, mode="rb") as rfp:
                    # BZ2File supports the with statement in py27+,
                    # but we support py2.6
                    wfp = BZ2File(real_convertedfile, "wb")
                    wfp.write(rfp.read())
                    wfp.close()
                os.unlink(convertedfile)
            else:  # keep_xml = True
                pass
        else:
            # print("outfile_is_newer returned True: real_convertedfile: %s (%s)" % (real_convertedfile, os.path.exists(real_convertedfile)))
            pass
        if not os.path.exists(real_convertedfile):
            print("%s don't exist -- parsing will fail!" % real_convertedfile)
            print("%s has the following files: %s" % (workdir, os.listdir(workdir)))
        # it's important that we open the file as a bytestream since
        # we might do byte-level manipulation in _parse_xml.
        if keep_xml == "bz2":
            fp = BZ2File(real_convertedfile)
        else:
            fp = open(real_convertedfile, "rb")
        res = parser(fp)
        fp.close()
        if keep_xml == False:
            os.unlink(convertedfile)
        return res

    def _tesseract(self, pdffile, workdir, lang, hocr=True):
        root = os.path.splitext(os.path.basename(pdffile))[0]

        # step 0: copy the pdf into a temp dir (which is probably on
        # local disk, saving us some network traffic if the pdf file
        # is huge and on a NFS mount somewhere)
        tmpdir = tempfile.mkdtemp()
        tmppdffile = os.sep.join([tmpdir, os.path.basename(pdffile)])
        util.copy_if_different(pdffile, tmppdffile)

        # step 1: find the number of pages
        cmd = "pdfinfo %s" % tmppdffile
        (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
        m = re.search(r"Pages:\s+(\d+)", stdout)
        number_of_pages = int(m.group(1))
        self.log.debug("%(root)s.pdf has %(number_of_pages)s pages" % locals())
        # step 2: extract the images (should be one per page), 10
        # pages at a time (pdfimages flakes out on larger loads)
        to_int = int
        for idx, i in enumerate(range(int(number_of_pages / 10) + 1)):
            frompage = (i * 10) + 1
            topage = min((i + 1) * 10, number_of_pages)
            if frompage > topage:
                continue
            cmd = "pdfimages -all -p -f %(frompage)s -l %(topage)s %(tmppdffile)s %(tmpdir)s/%(root)s" % locals(
            )
            self.log.debug("- running " + cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
            # step 2.1: convert and combine the recently extracted
            # images (which can be ppm, jpg, ccitt or whatever) into a
            # new tif (so that we add 10 pages at a time to the tif,
            # as imagemagick can create a number of pretty large files
            # for each page, so converting 200 images will fill 10 G
            # of your temp space -- which we'd like to avoid)
            cmd = "convert %(tmpdir)s/%(root)s-* -compress Zip %(tmpdir)s/%(root)s_tmp%(idx)04d.tif" % locals(
            )
            self.log.debug("- running " + cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
            # step 2.2: Remove extracted image files now that they're in the .tif
            for f in glob("%(tmpdir)s/%(root)s-*" % locals()):
                os.unlink(f)

        # Step 3: Combine all the 10-page tifs into a giant tif using tiffcp
        cmd = "tiffcp -c zip %(tmpdir)s/%(root)s_tmp*.tif %(tmpdir)s/%(root)s.tif" % locals()
        self.log.debug("- running " + cmd)
        (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
        # Step 3: OCR the giant tif file to create a .hocr.html file
        # Note that -psm 1 (automatic page segmentation with
        # orientation and script detection) requires the installation
        # of tesseract-ocr-3.01.osd.tar.gz
        usehocr = "hocr" if hocr else ""
        suffix = ".hocr" if hocr else ""
        cmd = "tesseract %(tmpdir)s/%(root)s.tif %(tmpdir)s/%(root)s%(suffix)s -l %(lang)s -psm 1 %(usehocr)s" % locals(
        )
        self.log.debug("running " + cmd)
        (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)

        # Step 4: Later versions of tesseract adds a automatic .hocr
        # suffix, while earlier versions add a automatic .html. Other
        # parts of the code expects the .html suffix, so we check to
        # see if we have new-tesseract behaviour and compensate.
        if os.path.exists("%(tmpdir)s/%(root)s%(suffix)s.hocr" % locals()):
            util.robust_rename("%(tmpdir)s/%(root)s%(suffix)s.hocr" % locals(),
                               "%(tmpdir)s/%(root)s%(suffix)s.html" % locals())
        
        # Step 5: Move our hOCR file to the workdir, then cleanup
        util.robust_rename("%(tmpdir)s/%(root)s%(suffix)s.html" % locals(),
                           "%(workdir)s/%(root)s%(suffix)s.html" % locals())
        shutil.rmtree(tmpdir)        

    def _pdftohtml(self, tmppdffile, workdir, images):
        root = os.path.splitext(os.path.basename(tmppdffile))[0]
        try:
            if images:
                # two pass coding: First use -c (complex) to extract
                # background pictures, then use -xml to get easy-to-parse
                # text with bounding boxes.
                cmd = "pdftohtml -nodrm -c %s" % tmppdffile
                self.log.debug("Converting with images: %s" % cmd)
                (returncode, stdout, stderr) = util.runcmd(cmd,
                                                           require_success=True)
                # print("1: ran %s (%s), stdout %r, stderr %r" % (cmd, returncode, stdout, stderr))
                # print("contents of %s is now %r" % (workdir, os.listdir(workdir)))
                # we won't need the html files, or the blank PNG files
                for f in os.listdir(workdir):
                    if f.startswith(root) and f.endswith(".html"):
                        os.unlink(workdir + os.sep + f)
                    elif f.startswith(root) and f.endswith(".png"):
                        # this checks the number of unique colors in the
                        # bitmap. If there's only one color, we don't need
                        # the file
                        (returncode, stdout, stderr) = util.runcmd(
                            'convert %s -format "%%k" info:' % (workdir + os.sep + f))
                        if stdout.strip() == "1":
                            os.unlink(workdir + os.sep + f)
                        else:
                            self.log.debug("Keeping non-blank image %s" % f)

            # imgflag = "-i" if not images else ""

            # Change in how we treat images: As we've extracted
            # background pictures above, we don't really need to
            # extract each individual image again. Also, for some PDFs
            # (FFFS 2011:34, an image is generated for most every
            # non-text dot, resulting in thousands of images per
            # page. So always ignore images.
            imgflag = "-i"
            
            # Without -fontfullname, all fonts are just reported as
            # having family="Times"...
            # Without -hidden, some scanned-and-OCR:ed files turn up
            # empty
            cmd = "pdftohtml -nodrm -xml -fontfullname -hidden %s %s" % (imgflag, tmppdffile)
            self.log.debug("Converting: %s" % cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd,
                                                       require_success=True)

            # print("2: ran %s (%s), stdout %r, stderr %r" % (cmd, returncode, stdout, stderr))
            # print("contents of %s is now %r" % (workdir, os.listdir(workdir)))
            xmlfile = os.path.splitext(tmppdffile)[0] + ".xml"
            # if pdftohtml fails (if it's an old version that doesn't
            # support the fullfontname flag) it still uses returncode
            # 0! Only way to know if it failed is to inspect stderr
            # and look for if the xml file wasn't created.
            if stderr and not os.path.exists(xmlfile):
                raise errors.ExternalCommandError(stderr)
            fontinfofile = "%s.fontinfo" % xmlfile
            maxlen = os.statvfs(os.path.dirname(fontinfofile)).f_namemax
            if maxlen < len(os.path.basename(fontinfofile)):
                fontinfofile = os.path.dirname(fontinfofile) + os.sep + os.path.basename(fontinfofile)[:maxlen]
            cmd = "pdffonts %s > %s" % (tmppdffile, fontinfofile)
            self.log.debug("Getting font info: %s" % cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd,
                                                       require_success=True)
            # print("3: ran %s (%s), stdout %r, stderr %r" % (cmd, returncode, stdout, stderr))
            # print("contents of %s is now %r" % (workdir, os.listdir(workdir)))
        finally:
            os.unlink(tmppdffile)
            assert not os.path.exists(tmppdffile), "tmppdffile still there:" + tmppdffile

    dims = r"bbox (?P<left>\d+) (?P<top>\d+) (?P<right>\d+) (?P<bottom>\d+)"
    re_dimensions = re.compile(dims).search

    def _parse_hocr(self, fp, dummy=None):
        if dummy:
            warnings.warn("filenames passed to _parse_hocr are now ignored", DeprecationWarning)
        def dimensions(s):
            m = self.re_dimensions(s)
            return dict([(k, round(int(v) / px_per_point)) for (k, v)
                         in m.groupdict().items()])
        
        tree = etree.parse(fp)
        for pageelement in tree.findall(
                "//{http://www.w3.org/1999/xhtml}div[@class='ocr_page']"):
            pageheight_in_inch = 11.69  # A4 page -- FIXME: use real page dimensions
            pointsize = 1 / 72
            pageheight_in_points = pageheight_in_inch / pointsize
            bbox = self.re_dimensions(pageelement.get('title'))
            px_per_point = (int(bbox.group("bottom")) - int(bbox.group("top"))) / pageheight_in_points
            dim = dimensions(pageelement.get('title'))
            page = Page(number=int(pageelement.get('id')[5:]),
                        width=dim['right'] - dim['left'],
                        height=dim['bottom'] - dim['top'],
                        src=None,
                        background=None)

            # we discard elements at the ocr_carea (content area?)
            # level, we're only concerned with paragraph-level
            # elements, which we use ocr_line for (to be consistent
            # with _parse_xml). However, if those element are wrapped
            # in ocr_par elements, then tesseract has indictated
            # paragraph-level segmentation which we make use of.
            for boxelement in pageelement.findall(
                    ".//{http://www.w3.org/1999/xhtml}span[@class='ocr_line']"):
                boxdim = dimensions(boxelement.get('title'))
                textelements = []
                par = boxelement.find("..[@class='ocr_par']")
                if par is not None:
                    parid = par.get('id')
                else:
                    parid = None
                for element in boxelement.findall(
                        ".//{http://www.w3.org/1999/xhtml}span[@class='ocrx_word']"):
                    dim = dimensions(element.get("title"))
                    t = "".join(element.itertext()) + element.tail
                    if not t.strip():
                        continue  # strip empty things
                    t = t.replace("\n", " ")

                    if element.getchildren():  # probably a <em> or <strong> element
                        tag = {'{http://www.w3.org/1999/xhtml}em': 'i',
                               '{http://www.w3.org/1999/xhtml}strong': 'b'}[element.getchildren()[0].tag]
                    else:
                        tag = None
                    text = Textelement(t,
                                       tag=tag,
                                       top=dim['top'],
                                       left=dim['left'],
                                       width=dim['right'] - dim['left'],
                                       height=dim['bottom'] - dim['top'])
                    textelements.append(text)

                # try to determine footnotes by checking if first
                # element is numeric and way smaller than the
                # others. in that case, set it's tag to "sup" (for
                # superscript)
                if len(textelements) == 0:
                    continue # the box didn't contain any real text, only lines of whitespace
                
                avgheight = sum([x.height for x in textelements]) // len(textelements)
                if textelements[0].strip().isdigit() and textelements[0].height <= avgheight / 2:
                    textelements[0].tag = "sup"

                # Now that we know all text elements that should be in
                # the Textbox, we can guess the font size.
                fontspec = {'family': "unknown",
                            'size': avgheight}

                # find any previous definition of this fontspec
                fontid = None
                for specid, spec in self.fontspec.items():
                    if (fontspec['size'] == spec['size'] and
                        fontspec['family'] == spec['family']):
                        fontid = specid
                        
                # None was found, create a new
                if not fontid:
                    fontid = str(len(self.fontspec))  # start at 0
                    fontspec['id'] = fontid
                    self.fontspec[fontid] = fontspec

                # finally create the box and add all our elements
                # (should not be more than one?) to it
                kwargs = {'top': boxdim['top'],
                          'left': boxdim['left'],
                          'width': boxdim['right'] - boxdim['left'],
                          'height': boxdim['bottom'] - boxdim['top'],
                          'fontspec': self.fontspec,
                          'fontid': fontid}
                if parid:
                    kwargs['parid'] = parid
                kwargs['pdf'] = self
                box = Textbox(**kwargs)
                for e in textelements:
                    box.append(e)
                page.append(box)
            self.append(page)
        self.log.debug("PDFReader initialized: %d pages" %
                       (len(self)))

    def _parse_xml(self, xmlfp, dummy=None):
        filename = util.name_from_fp(xmlfp)
        # first up, try to locate a fontinfo.txt file
        fontinfo = {}
        fields = []
        fonttypemap = {"Type 1": "Type1",
                       "Type 1C": "Type1C",
                       "Type 1C (OT)": "Type1C(OT)",
                       "Type 3": "Type3",
                       "TrueType (OT)": "TrueType(OT)",
                       "CID Type 0": "CIDType0",
                       "CID Type 0C": "CIDType0C",
                       "CID Type 0C (OT)": "CIDType0C(OT)",
                       "CID TrueType": "CIDTrueType",
                       "CID TrueType (OT)": "CIDTrueType(OT)"}
        fontinfofile = filename.replace(".bz2", "") + ".fontinfo"
        # print("Looking for %s (%s)" % (fontinfofile, os.path.exists(fontinfofile)))
        if os.path.exists(fontinfofile):
            with open(fontinfofile) as fp:
                for line in fp:
                    if not fields:
                        fields = line.split()
                    elif not line.startswith("-----"):
                        # remove all spaces in the "type" column by
                        # knowing possible values
                        for k in fonttypemap:
                            if k in line:
                                line = line.replace(k, fonttypemap[k])
                        # NOW we can finally split on whitespace
                        cols = line.split()
                        if cols[0] not in fontinfo:  # the output from
                                                     # pdffonts might
                                                     # include several
                                                     # fonts with the
                                                     # same family...
                            fontinfo[cols[0]] = dict(zip(fields, cols))
        if dummy:
            warnings.warn("filenames passed to _parse_xml are now ignored", DeprecationWarning)
        def txt(element_text):
            return re.sub(r"[\s\xa0\xc2]+", " ", str(element_text))

        self.log.debug("Loading %s" % filename)
        if "Custom" in [f.get("encoding") for f in fontinfo.values()]:
            # the xmlfp might contain 0x03 (ctrl-C) for text nodes
            # using a custom encoding, where space is really
            # meant. Also a lot of other chars from 0x04 -- 0x19. It's
            # a bug in pdftohtml that such invalid chars are
            # included. Unfortunately, lxml/libxml seem to strip these
            # invalid chars when parsing, before Textbox.decode can
            # access it. So we preprocess the bytestream (in-memory --
            # a custom wrapped codec would be better but more
            # complicated) to change these to xml numeric character
            # references
            newfp = BytesIO()
            buffer = xmlfp.read()
            if not isinstance(buffer, bytes):
                self.log.warning("File %s was opened in text, not binary mode" % util.name_from_fp(xmlfp))
                buffer = bytes(buffer.encode("utf-8"))
            else:
                # convert to a py3 style bytes() object (one that
                # returns ints, not strs, when iterating over it)
                buffer = bytes(buffer)
            for b in buffer:
                # leave some control chars as-is (CR/LF but not TAB)
                if b < 0x20 and b not in (0xa, 0xd):
                    # note: We don't use real xml numeric character
                    # references as "&#3;" as this is just as invalid
                    # as a real 0x03 byte in XML. Instead we
                    # double-escape it.
                    entity = "&amp;#%s;" % b
                    newfp.write(entity.encode())
                else:
                    # newfp.write(six.int2byte(b))
                    newfp.write(bytes((b,)))
            newfp.seek(0)
            xmlfp = newfp
        try:
            root = etree.parse(xmlfp).getroot()
        except etree.XMLSyntaxError as e:
            self.log.debug(
                "pdftohtml created incorrect markup, trying to fix using BeautifulSoup: %s" %
                e)
            xmlfp.seek(0)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(xmlfp, "lxml")
            xmlfp = BytesIO(str(soup).encode("utf-8"))
            xmlfp.name = filename
            # now the root node hierarchy is
            # <html><body><pdf2xml><page>..., not
            # <pdf2xml><page>... So just skip the top two levels
            root = etree.parse(xmlfp).getroot()[0][0]
            self.log.debug("BeautifulSoup workaround successful")

        
        assert root.tag == "pdf2xml", "Unexpected root node from pdftohtml -xml: %s" % root.tag
        # We're experimenting with a auto-detecting decoder, which
        # needs a special API call in order to do the detection. If
        # this turns out to be a good idea we'll rework it into an
        # official subclass of BaseTextDecoder (maybe
        # AnalyzingTextDecoder) and test with isinstance
        if hasattr(self._textdecoder, 'analyze_font'):
            self._analyze_font_encodings(root, fontinfo)
        for pageelement in root:
            lastbox = None
            if pageelement.tag == "outline":
                # FIXME: We should do something with this information
                continue
            elif pageelement.tag is etree.Comment:
                # NOTE: Comments are never created by pdftohtml, but
                # might be present in testcases
                continue
            assert pageelement.tag == "page", "Got <%s>, expected <page>" % page.tag
            page = Page(number=int(pageelement.get('number')),  # alwaysint?
                        width=int(pageelement.get('width')),
                        height=int(pageelement.get('height')),
                        src=None,
                        background=None)
            basename = os.path.splitext(filename)[0]
            if filename.endswith(".bz2"):
                basename = os.path.splitext(basename)[0]
            background = "%s%03d.png" % (
                basename, page.number)
            # Reasons this file might not exist: it was blank and
            # therefore removed, or We're running under RepoTester
            if os.path.exists(background):
                page.background = background
            after_footnote = False
            peekable_pageelements = Peekable(pageelement)
            for element in peekable_pageelements:
                if element.tag is etree.Comment:
                    continue
                if element.tag == 'image':
                    # FIXME: do something clever with these
                    continue
                if element.tag == 'fontspec':
                    self._parse_xml_add_fontspec(element, fontinfo, self.fontspec)
                    continue
                assert element.tag == 'text', "Got <%s>, expected <text>" % element.tag
                # eliminate "empty" textboxes, including "<text><i> </i></text>\n"
                if (((element.text and txt(element.text).strip() == "") or
                     (element.text is None)) and
                    not element.getchildren()):
                    # print "Skipping empty box"
                    continue
                if len(page) > 0:
                    lastbox = page[-1]
                try:
                    nextelement = peekable_pageelements.peek()
                except StopIteration:
                    nextelement = None
                box = self._parse_xml_make_textbox(element, nextelement, after_footnote,
                                                   lastbox, page)
                if box is None: # might consist entirely of empty,
                                # therefore skipped, textelements.
                    continue
                # we need to distinguish between inline footnote
                # markers (should go with preceeding textbox):
                if hasattr(box, 'merge-with-current'):
                    delattr(box, 'merge-with-current')
                    page[-1] = page[-1] + box
                    after_footnote = True
                # and footer footnote markers (should create a new
                # textbox):
                elif len(box) and box[0].tag and (box[0].tag.endswith(("sup", "s"))):
                    page.append(box)
                    after_footnote = True
                elif (after_footnote and
                      abs(page[-1].right - box.left) < 3):
                    page[-1] = page[-1] + box
                    after_footnote = False
                else:
                    page.append(box)
            # done reading the page
            self.append(page)
        self.log.debug("PDFReader initialized: %d pages, %d fontspecs" %
                       (len(self), len(self.fontspec)))
        

    def _parse_xml_make_textbox(self, element, nextelement, after_footnote, lastbox, page):
        textelements = self._parse_xml_make_textelement(element)
        attribs = dict(element.attrib)
        thisfont = self.fontspec[int(element.get('font'))]
        lastfont = lastbox.font if lastbox else None
        if self.detect_footnotes:
            if (len(textelements) and
                textelements[0].isdigit() and
                lastfont and
                lastfont.family == thisfont['family'] and
                lastfont.size > thisfont['size']):
                # this must be a footnote -- alter tag to show that it
                # should be rendered with superscript
                if textelements[0].tag is None:
                    textelements[0].tag = ""
                if isinstance(textelements[0], LinkedTextelement) or textelements[0].tag:
                    textelements[0].tag += "s"
                else:
                    textelements[0].tag = "sup"
            
                # is it in the main text, ie immediately
                # following the last textbox? Then append it to that textbox 
                if abs(lastbox.right - int(attribs['left'])) < 3:
                    # return a Box that the caller will merge with current
                    attribs['fontid'] = attribs.pop('font')
                    attribs['merge-with-current'] = True
                    return Textbox(textelements, **attribs)
                elif min([x.left for x in page]) - int(attribs['left']) < 3:
                    # then create a new textbox and let
                    # the after_footnote magic append more
                    # textboxes to it. Note: we don't use
                    # the small font used in the footnote
                    # marker, but rather peek at the
                    # normal-sized font that immediately
                    # follows. Also the box placement and
                    # height is determined by the
                    # following element
                    if nextelement is not None:
                        attribs['fontspec'] = self.fontspec
                        attribs['fontid'] = int(nextelement.attrib['font'])
                        attribs['top'] = nextelement.attrib['top']
                        attribs['height'] = nextelement.attrib['height']
                        attribs['pdf'] = self
                        del attribs['font']
                        return self._textdecoder(Textbox(textelements, **attribs),
                                                 self.fontspec)
                else:
                    self.log.debug("Text element %s (%s) looks like a footnote "
                                   "marker, but not in main text nor footer "
                                   "area" % (serialize(textelements[0]).strip(), attribs))
        elif (after_footnote and
              lastfont.family == thisfont['family'] and
              lastfont.size == thisfont['size'] and
              lastbox.top == int(attribs['top']) and
              lastbox.height == int(attribs['height']) and
              abs(lastbox.right - int(attribs['left'])) < 3):
            lastbox.append(self._parse_xml_make_textelement(element))
            after_footnote = False
        after_footnote = False
        # all textboxes share the same fontspec dict
        attribs['fontspec'] = self.fontspec
        attribs['fontid'] = int(attribs['font'])
        attribs['pdf'] = self
        del attribs['font']
        return self._textdecoder(Textbox(textelements, **attribs), self.fontspec)

    import string
    ws_trans = {ord("\n"): " ",
                ord("\t"): " ",
                ord("\xa0"): " "}

    def _parse_xml_make_textelement(self, element, **origkwargs):
        # the complication is that a hierarchical sequence of tags
        # should be converted to a list of 
        # 
        # case 1: plain text -> Textelement
        # case 2: tag = a -> LinkedTextelement
        # case 3: tab=b/i -> Textelement, tag=...
        # case 4: tag=b + tag=i -> Textelement, tag=bi

        # complicated cases:
        # TE = Textelement
        # LTE = LinkedTextelement
        # 
        # 1: <text>Here <b>is <i> some <a href="...">text</a></i></b></text>
        #     -> <b>is <i> some <a href="...">text</a></i></b>
        #       -> <i> some <a href="...">text</a></i>, tag="b"
        #         -> <a href="...">text</a>, tag="bi"
        #    -> (TE("Here"), TE("is", tag="b"), TE("some", tag="bi"), LTE("text", tag="bi", uri="..."))
        # 2: <text><b><i><a href="...">1</a></i>/<b></text>
        #    -> LTE("1", tag="bis", uri="..."), footnote=True (shld caller determine that?)
        # 3: <text><b></i>that </i> is </b> complicated</text>, after_footnote=True
        #    -> TextElement("that", tag="bi"), TE("is", tag="i"), TE("complicated"), 
        # 4: <text>2</text>
        #    -> TE("2", tag="sup")
        #
        def cleantag(kwargs):
            # returns the "a" tag from a tag string, if present (w/o
            # altering the original kwargs)
            kwargscopy = dict(kwargs)
            if "a" in kwargs.get("tag", ""):
                kwargscopy["tag"] = kwargscopy["tag"].replace("a", "")
            if kwargscopy["tag"] == "":
                kwargscopy["tag"] = None
            return kwargscopy

        def normspace(txt):
            # like util.normalize_space, but preserves a single leading/trailing space
            if not isinstance(txt, str): # under py2, element.text can
                                         # sometimes be a bytestring?
                txt = txt.decode()
            txt = txt.translate(self.ws_trans)
            startspace = " " if txt.startswith(" ") else ""
            endspace = " " if txt.endswith(" ") and len(txt) > 1 else ""
            return startspace + util.normalize_space(txt) + endspace
        
        res = []
        cls = origcls = Textelement
        origtag = None
        kwargs = dict(origkwargs)
        if 'tag' not in kwargs:
            kwargs['tag'] = ""
        if element.tag == "a":
            cls = LinkedTextelement
            kwargs['uri'] = element.get("href")
            kwargs['tag'] = (kwargs.get('tag', '') + element.tag)
        elif element.tag in ("b", "i"):
            if "a" in kwargs.get('tag', ''):
                cls = LinkedTextelement
            kwargs['tag'] += element.tag
        else:
            assert element.tag == "text", "Got <%s>, expected <{text,b,i,a}> " % element.tag
        if element.text and (element.text.strip() or element.tag == "a"):
            res.append(cls(normspace(element.text), **cleantag(kwargs)))
        for child in element:
            res.extend(self._parse_xml_make_textelement(child, **kwargs))
        if element.tail and element.tail.strip():
            if element.text and element.text.strip() == "":
                # even though we've skipped an empty tag like "<i>
                # </i>", we record the fact that we've done so, since
                # it is useful for some unreliable font family
                # heuristics
                origkwargs['skippedempty'] = element.tag
            res.append(origcls(normspace(element.tail), **cleantag(origkwargs)))
        return res
    

    def _parse_xml_add_fontspec(self, element, fontinfo, fontspec):
        fontid = int(element.attrib['id'])
        # make sure we always deal with a basic dict (not
        # lxml.etree._Attrib) where all keys are str
        # object (not bytes)
        fspec = dict([(k, str(v)) for k, v in element.attrib.items()])
        # then make it more usable
        fspec['size'] = int(fspec['size'])
        if fontinfo.get(fspec['family']):
            # Commmon values: MacRoman, WinAnsi, Custom
            fspec['encoding'] = fontinfo[fspec['family']]['encoding']
        if "+" in fspec['family']:
            fspec['family'] = fspec['family'].split("+", 1)[1]
        fontspec[fontid] = self._textdecoder.fontspec(fspec)


    def _analyze_font_encodings(self, root, fontinfo):
        encoded_fontids = {}
        for pageelement in root:
            if pageelement.tag == "outline":
                continue
            elif isinstance(pageelement, etree._Comment):
                continue
            # we need to loop through all textboxes on all pages,
            # because the very last one might have a new fontspec
            for e in pageelement:
                if e.tag == 'fontspec':
                    fontid = e.attrib['id']
                    family = e.attrib['family']
                    if fontinfo.get(family) and fontinfo[family]['encoding'] == "Custom":
                        encoded_fontids[fontid] = []
                elif e.tag == 'text' and e.attrib["font"] in encoded_fontids:
                    if len(encoded_fontids[e.attrib["font"]]) < 10:
                        encoded_fontids[e.attrib["font"]].append(e)

        for fontid, samples in encoded_fontids.items():
            try:
                offset = self._textdecoder.analyze_font(fontid, samples)
                if offset:
                    self.log.debug("Font %s: Decoding with offset %02x" % (fontid, offset))
                else:
                    self.log.debug("Font %s: No offset used" % fontid)
            except errors.PDFDecodeError:
                self.log.debug("Font %s: Encoding could not be detected, assuming no encoding" %  fontid)

    ################################################################
    # Properties and methods relating to the initialized PDFReader
    # object
    tagname = "div"
    classname = "pdfreader"

    def is_empty(self):
        return 0 == sum([len(x) for x in self])

    def textboxes(self, gluefunc=None, pageobjects=False, keepempty=False, startpage=0, pagecount=None, cache=True):
        """Return an iterator of the textboxes available.

        ``gluefunc`` should be a callable that is called with
        (textbox, nextbox, prevbox), and returns True iff nextbox
        should be appended to textbox.

        If ``pageobjects``, the iterator can return Page objects to
        signal that pagebreak has ocurred (these Page objects may or
        may not have Textbox elements).

        If ``keepempty``, process and return textboxes that have no
        text content (these are filtered out by default)

        If ``cache``, store the resulting list of textboxes for each
        page and return it the next time.

        """
        textbox = None
        prevbox = None
        if gluefunc:
            glue = gluefunc
        else:
            glue = self._default_glue
        if pagecount:
            pages = self[startpage:startpage+pagecount]
        else:
            pages = self
        for page in pages:
            if pageobjects:
                yield page
            if cache:
                if page._textboxes_cache is not None:
                    # reuse the existing cache
                    # print("Reusing cache for page %s" % page.number)
                    for textbox in page._textboxes_cache:
                        yield textbox
                else:
                    # print("Setting up cache for page %s" % page.number)
                    page._textboxes_cache = []
            if not cache or not page._textboxes_cache:
                for nextbox in page:
                    if not (keepempty or str(nextbox).strip()):
                        continue
                    if not textbox:  # MUST glue
                        textbox = nextbox
                    else:
                        if glue(textbox, nextbox, prevbox):
                            # can't modify textbox in place -- this messes
                            # things up if we want/need to run textboxes()
                            # twice. Must create a new one.
                            # textbox += nextbox
                            textbox = textbox + nextbox
                        else:
                            if cache:
                                page._textboxes_cache.append(textbox)
                            yield textbox
                            textbox = nextbox
                    prevbox = nextbox
                if textbox:
                    if cache:
                        page._textboxes_cache.append(textbox)
                    yield textbox
            textbox = None

    def median_box_width(self, threshold=0):
        """Returns the median box width of all pages."""
        boxwidths = []
        for page in self:
            for box in page:
                if box.right - box.left < threshold:
                    continue
                # print "Box width: %d" % (box.right-box.left)
                boxwidths.append(box.right - box.left)
        boxwidths.sort()
        return boxwidths[int(len(boxwidths) / 2)]

    @staticmethod
    def _default_glue(textbox, nextbox, prevbox):

        def basefamily(family):
            return family.replace("-", "").replace("Bold", "").replace("Italic", "")
        # default logic: if lines are next to each other
        # horizontally, line up vertically, and have the same
        # font, then they should be glued
        linespacing = 1.5
#        a = str(textbox)
#        b = str(nextbox)
#        c = textbox.font.family == nextbox.font.family and textbox.font.size == nextbox.font.size
#        d = textbox.top < nextbox.top
#        e1 = textbox.bottom + (prevbox.height * linespacing) - prevbox.height
#        e2 = nextbox.top
#        e = e1 >= e2
#        f = textbox.font.family
#        g = nextbox.font.family

        # Accept font families that are almost equal (only differ by a
        # "Bold" or "Italic" in one but not the other). Otherwise
        # common constructs like:
        #
        # <b>Lead text</b>: Lorem ipsum dolor sit amet, consectetur
        # adipiscing elit. Donec suscipit nulla ut lorem dapibus.
        #
        # wont be considered the same textbox.
        if (basefamily(textbox.font.family) == basefamily(nextbox.font.family) and
                textbox.font.size == nextbox.font.size and
                textbox.left == nextbox.left and
                textbox.top < nextbox.top and
                textbox.bottom + (prevbox.height * linespacing) - prevbox.height >= nextbox.top):
            return True

    def __iadd__(self, other):
        if not hasattr(self, 'files'):
            self.files = [(0, len(self), self.filename)]
        self.files.append((len(self), len(other), other.filename))
        super(PDFReader, self).__iadd__(other)
        return self

class StreamingPDFReader(PDFReader):
    def __init__(self, *args, **kwargs):
        """Experimental API for PDFReader that separates conversion (Word
        etc->)PDF->intermediate format from parsing of the
        intermediate XML/hOCR data. """
        self.log = logging.getLogger('pdfreader')
        self.fontspec = kwargs.get('fontspec') or {}

    def parse(self, filename, workdir, images=True,
              convert_to_pdf=False,
              keep_xml=True,
              ocr_lang=None,
              fontspec=None,
              textdecoder=None):
        self.read(self.convert(filename, workdir, images, convert_to_pdf,
                               keep_xml, ocr_lang), textdecoder=textdecoder)

    def intermediate_filename(self, filename, ocr_lang, keep_xml):
        basename = os.path.basename(filename)
        stem = os.path.splitext(basename)[0]
        if ocr_lang:
            suffix = ".hocr.html"
        else:
            suffix = ".xml"
        convertedfile = os.sep.join([self.workdir, stem + suffix])
        if keep_xml == "bz2":
            real_convertedfile = convertedfile + ".bz2"
        else:
            real_convertedfile = convertedfile
        return real_convertedfile

    def convert(self, filename, workdir=None, images=True,
                convert_to_pdf=False, keep_xml=True, ocr_lang=None):
        self.filename=filename
        self.workdir = workdir
        if self.workdir is None:
            self.workdir = tempfile.mkdtemp()

        if convert_to_pdf:
            newfilename = workdir + os.sep + \
                os.path.splitext(os.path.basename(filename))[0] + ".pdf"
            if not os.path.exists(newfilename):
                util.ensure_dir(newfilename)
                cmdline = "soffice --headless --convert-to pdf --outdir '%s' %s" % (
                    workdir, filename)
                self.log.debug("%s: Converting to PDF: %s" % (filename, cmdline))
                (ret, stdout, stderr) = util.runcmd(
                    cmdline, require_success=True)
                filename = newfilename

        assert os.path.exists(filename), "PDF %s not found" % filename
        convertedfile = self.intermediate_filename(filename, ocr_lang, keep_xml)
        if ocr_lang:
            converter = self._tesseract
            converter_extra = {'lang': ocr_lang}
            tmpfilename = filename
        else:
            converter = self._pdftohtml
            converter_extra = {'images': images}
            tmpfilename = os.sep.join([workdir, os.path.basename(filename)])

        # copying the filename to the workdir is only needed if we use
        # PDFReader._pdftohtml

        if not util.outfile_is_newer([filename], convertedfile):
            if not ocr_lang:
                # this is somewhat expensive and not really needed when converter is tesseract
                util.copy_if_different(filename, tmpfilename)
            # this is the expensive operation
            converter(tmpfilename, workdir, **converter_extra)

            # check if result is empty (has no content in any text node, except outline nodes)
            try:
                with open(convertedfile.replace(".bz2", "")) as fp:
                    tree = etree.parse(fp)
                for bad in tree.findall("outline"):
                    bad.getparent().remove(bad)
                if not etree.tostring(tree, method="text", encoding="utf-8").strip():
                    os.unlink(convertedfile.replace(".bz2", ""))
                    raise errors.PDFFileIsEmpty("%s contains no text" % filename)
            except (etree.XMLSyntaxError, UnicodeDecodeError) as e:
                # this means pdftohtml created incorrect markup. This
                # probably means that the doc is nonempty, which is
                # all we care about at this point. At a later stage
                # (in _parse_xml), a workaround will be applied to the
                # document on the fly.
                pass
            if keep_xml == "bz2":
                with open(convertedfile.replace(".bz2", ""), mode="rb") as rfp:
                    # BZ2File supports the with statement in py27+,
                    # but we support py2.6
                    wfp = BZ2File(convertedfile, "wb")
                    wfp.write(rfp.read())
                    wfp.close()
                os.unlink(convertedfile.replace(".bz2", ""))
            else:  # keep_xml = True
                pass

        # it's important that we open the file as a bytestream since
        # we might do byte-level manipulation in _parse_xml.
        if keep_xml == "bz2":
            fp = BZ2File(convertedfile)
        else:
            fp = open(convertedfile, "rb")
        return fp

    def read(self, fp, parser="xml", textdecoder=None):
        if textdecoder is None:
            self._textdecoder = BaseTextDecoder()
        else:
            self._textdecoder = textdecoder
        filename = util.name_from_fp(fp)
        self.filename = filename
        if parser == "ocr":
            parser = self._parse_hocr
        else:
            parser = self._parse_xml
        parser(fp)  # does not return anything useful
        fp.close()
        return self  # for chainability


class Page(CompoundElement, OrdinalElement):

    """Represents a Page in a PDF file. Has *width* and *height*
     properties."""

    tagname = "div"
    classname = "pdfpage"
    margins = None

    def __init__(self, *args, **kwargs):
        self._textboxes_cache = None
        super(Page, self).__init__(*args, **kwargs)

    @property
    def id(self):
        # FIXME: this will only work for documents consisting of a
        # single PDF file, not multiple (see
        # pdfdocumentrepository.create_external_resources to
        # understand why)
        if isinstance(self.number, str):
            # if the page number is a roman numeral, there is no usable way of padding it
            return "page%s" % self.number
        else:
            return "page%03d" % self.number

    # text: can be string, re obj or callable (gets called with the box obj)
    # fontsize: can be int or callable
    # fontname: can be string or callable
    # top,left,bottom,right
    def boundingbox(self, top=0, left=0, bottom=None, right=None):
        """A generator of :py:class:`ferenda.pdfreader.Textbox` objects that
           fit into the bounding box specified by the parameters.

        """
        if not bottom:
            bottom = self.height
        if not right:
            right = self.width
        for box in self:
            if (box.top >= top and
                box.left >= left and
                box.bottom <= bottom and
                    box.right <= right):
                # print "    SUCCESS"
                yield box
            # else:
            #    print "    FAIL"

    def crop(self, top=0, left=0, bottom=None, right=None):
        """Removes any :py:class:`ferenda.pdfreader.Textbox` objects that does not fit within the bounding box specified by the parameters."""
        # Crop any text box that sticks out
        # Actually if top and left != 0, we need to adjust them
        newboxes = []
        for box in self.boundingbox(top, left, bottom, right):
            box.top = box.top - top
            box.left = box.left - left
            box.right = box.right - right
            box.bottom = box.bottom - bottom
            newboxes.append(box)
        self[:] = []
        self.extend(newboxes)
        self.width = right - left
        self.height = bottom - top
        # Then crop the background images... somehow
        if os.path.exists(self.background):
            cmdline = "convert %s -crop %dx%d+%d+%d +repage %s" % (self.background,
                                                                   self.width, self.height, left, top,
                                                                   self.background + ".new")
            # print "Running %s" % cmdline
            (returncode, stdout, stderr) = util.runcmd(cmdline,
                                                       require_success=True)
            util.replace_if_different(
                "%s.new" % self.background, self.background)

    def __str__(self):
        textexcerpt = " ".join([str(x) for x in self])
        return "Page %s (%d x %d): '%s...'" % (
            self.number, self.width, self.height, str(textexcerpt[:40]))

    def __repr__(self):
        return '<%s %s (%dx%d): %d textboxes>' % (self.__class__.__name__,
                                                  self.number, self.width, self.height,
                                                  len(self))


class Textbox(CompoundElement):

    """A textbox is a amount of text on a PDF page, with *top*, *left*,
*width* and *height* properties that specifies the bounding box of the
text. The *fontid* property specifies the id of font used (use
:py:meth:`~ferenda.pdfreader.Textbox.getfont` to get a dict of all
font properties). A textbox consists of a list of Textelements which
may differ in basic formatting (bold and or italics), but otherwise
all text in a Textbox has the same font and size.

    """
    tagname = "p"
    classname = "textbox"

    def __init__(self, *args, **kwargs):
        assert 'top' in kwargs, "top attribute missing"
        assert 'left' in kwargs, "left attribute missing"
        assert 'width' in kwargs, "width attribute missing"
        assert 'height' in kwargs, "height attribute missing"
        assert 'fontid' in kwargs, "font id attribute missing"

        self.top = int(kwargs['top'])
        self.left = int(kwargs['left'])
        self.width = int(kwargs['width'])
        self.height = int(kwargs['height'])
        self.right = self.left + self.width
        self.bottom = self.top + self.height
        self.lines = int(kwargs.get("lines", 0))
        
        # self._fontspecid = kwargs['fontid']
        self.fontid = kwargs['fontid'] or 0
        if 'fontspec' in kwargs:
            self._fontspec = kwargs['fontspec']
            del kwargs['fontspec']
        else:
            self._fontspec = {}
        if 'pdf' in kwargs:
            self._pdf = kwargs['pdf']
            del kwargs['pdf']
        else:
            self._pdf = None
        del kwargs['top']
        del kwargs['left']
        del kwargs['width']
        del kwargs['height']
        del kwargs['fontid']

        super(Textbox, self).__init__(*args, **kwargs)

    def __str__(self):
        s = "".join(self)
        return s


    def __repr__(self):
        # <Textbox 30x18+278+257 "5.1">
        # <Textbox 430x14+287+315 "Regeringens fÃ¶rslag: NÃ¤[...]g ska ">
        s = str(self)
        if len(s) > 40:
            s = s[:25] + "[...]" + s[-10:]

        #if six.PY2:
        #    # s = repr(s)
        #    s = s.encode('ascii', 'replace')
        if self.font:
            fontinfo = "%s@%s " % (self.font.family,
                                   self.font.size)
        else:
            fontinfo = ""
        return '<%s %sx%s+%s+%s %s"%s">' % (self.__class__.__name__,
                                            self.width, self.height,
                                            self.left, self.top,
                                            fontinfo,
                                            s)
    def __add__(self, other):

        def different_tags(self, other):
            # None, {b, i} => True
            # None, s => False
            # {b, i}, {bs, is} => False
            # b, bi => True
            selftag = getattr(self, 'tag', '').replace("s", "")
            othertag = getattr(other, 'tag', '').replace("s", "")
            return selftag != othertag
        
        # expand dimensions
        top = min(self.top, other.top)
        left = min(self.left, other.left)
        width = max(self.left + self.width,
                    other.left + other.width) - left
        height = max(self.top + self.height,
                     other.top + other.height) - top
        lines = self.lines + other.lines
        if self.bottom > other.top + (other.height / 2):
            # self and other is really on the same line
            lines -= 1
            
        res = Textbox(top=top, left=left, width=width, height=height,
                      fontid=self.fontid,
                      fontspec=self._fontspec,
                      pdf=self._pdf,
                      lines=lines)

        # add all Textelement objects, concatenating adjacent TE:s if
        # their tags match.
        tag = None if len(self) == 0 else self[0].tag
        c = Textelement(tag=tag)
        # possibly add a space instead of a missing newline -- but
        # not before superscript elements
        if (self and other and
            different_tags(self, other) and 
            not self[-1].endswith(" ")):
            self.append(Textelement(" ", tag=self[-1].tag))
        for e in itertools.chain(self, other):
            if e.tag != c.tag:
                if c:
                    res.append(c)
                res.append(e)
                c = Textelement(tag=e.tag)
            else:
                c = c + e
        # it MIGHT be the case that we need to merge c with the last
        # Textelement added to res iff their tags match
        if len(res) and c and c.tag == res[-1].tag and type(c) == type(res[-1]):
            res[-1] = res[-1] + c
        elif c:
            res.append(c)
        return res

    def __iadd__(self, other):
        self.top = min(self.top, other.top)
        self.left = min(self.left, other.left)
        self.width = max(self.left + self.width,
                         other.left + other.width) - self.left
        self.height = max(self.top + self.height,
                          other.top + other.height) - self.top
        self.right = self.left + self.width
        self.bottom = self.top + self.height
        self.lines += other.lines
        if self.bottom > other.top + (other.height / 2):
            # self and other is really on the same line
            self.lines -= 1
        if len(self):
            c = self.pop()
        else:
            c = Textelement(tag=None)
        for e in other:
            if e.tag != c.tag:
                if c:
                    self.append(c)
                self.append(e)
                c = Textelement(tag=e.tag)
                # c = e
            else:
                c = c + e
        if c:
            self.append(c)
        return self
#
#    def append(self, thing):
#        if len(self) == 0 or self[-1].tag != thing.tag:
#            return super(Textbox, self).append(thing)
#        else:
#            # concatenate adjacent TE:s if their tags match.
#            self[-1] = self[-1] + thing
#            return
#            


    def as_xhtml(self, uri, parent_uri=None):
        children = []
        first = True
        prevpart = None
        for subpart in self:
            if (not first and
                type(subpart) == type(prevpart) and
                getattr(subpart, 'tag', None) == getattr(prevpart, 'tag', None) and
                getattr(subpart, 'uri', None) == getattr(prevpart, 'uri', None)):
                prevpart = prevpart + subpart
            elif prevpart:
                # make sure Textelements w/o a tag doesn't render with
                # as_xhtml as this adds a meaningless <span>
                if (hasattr(prevpart, 'as_xhtml') and
                    (not isinstance(prevpart, Textelement) or
                     prevpart.tag or
                     getattr(prevpart, 'uri', None))):
                    prevpart = prevpart.as_xhtml(uri, parent_uri)
                if prevpart is not None:
                    children.append(self._cleanstring(prevpart))
                prevpart = subpart
            else:
                prevpart = subpart
            first = False
        if (hasattr(prevpart, 'as_xhtml') and
            (not isinstance(prevpart, Textelement) or
             prevpart.tag)):
            prevpart = prevpart.as_xhtml(uri, parent_uri)
        if prevpart is not None:
            children.append(self._cleanstring(prevpart))

        attribs = {}    
        if hasattr(self, 'fontid'):
            attribs['class'] = 'textbox fontspec%s' % self.fontid 
        element = E("p", attribs, *children)
        # FIXME: we should output these positioned style attributes
        # only when the resulting document is being serialized in a
        # positioned fashion (and probably also the textbox/fontspec
        # attributes).
        if hasattr(self, 'top') and hasattr(self, 'left'):
            element.set(
                'style', 'top: %spx; left: %spx; height: %spx; width: %spx' %
                (self.top, self.left, self.height, self.width))
        return element

    def _cleanstring(self, thing):
        if not isinstance(thing, str):
            return thing
        newstring = ""
        for char in thing:
            if unicodedata.category(char) != "Cc":
                newstring += char
        return newstring
                

    @cached_property
    def font(self):
        if self.fontid is not None:
            return LayeredConfig(Defaults(self._fontspec[self.fontid]))
        else:
            return LayeredConfig(Defaults({}))

# this doesnt work that well with the default __setattribute__
# implementation of this class' superclass.
#
#    @font.setter
#    def font(self, value):
#        for fontspecid, fontspec in self._fontspec.items():
#            if value == fontspecid:
#                self.font = fontspecid
#        if self.font is None:   # .font might have the valid value 0
#            self.font = str(len(self._fontspecid)) # start at 0
#            self._fontspec[self.font] = value
#
#


class Textelement(UnicodeElement):

    """Represent a single part of text where each letter has the exact
    same formatting. The ``tag`` property specifies whether the text
    as a whole is bold (``'b'``) , italic(``'i'`` bold + italic
    (``'bi'``) or regular (``None``).
    """
        
    def _get_tagname(self):
        if self.tag:
            return self.tag
        else:
            return "span"

    def as_xhtml(self, uri, parent_uri=None):
        if self.tag and len(self.tag) > 1 and self.tag != "sup":
            # first create a list of elements
            tagmap = {"s": "sup",
                      "b": "b",
                      "i": "i",
                      "a": "a"}
            tags = [E(tagmap[x]) for x in self.tag]
            # then place the text content in the last one
            tags[-1].text = self.clean_string()
            # then nest them
            for idx, tag in enumerate(tags):
                if idx < len(tags) - 1:
                    tag.append(tags[idx+1])
            return tags[0]
        else:
            return super(Textelement, self).as_xhtml(uri, parent_uri)

    tagname = property(_get_tagname)

    def __add__(self, other):
        # It seems like some versions of pdf2html automatically add a
        # space at the end of lines to that they can be concatenated,
        # but some (later) versions omit this, requiring us to add a
        # extra space to avoid mashing words together.
        if len(self) and not (self.endswith(" ") or self.endswith("-") or other == " "):
            extraspace = " "
        else:
            extraspace = ""
        if hasattr(self, 'top') and hasattr(other, 'top'):
            dims = {'top': min(self.top, other.top),
                    'left': min(self.left, other.left),
                    'width': max(self.left + self.width,
                                 other.left + other.width) - self.left,
                    'height': max(self.top + self.height,
                                  other.top + other.height) - self.top}
        else:
            if hasattr(self, 'top'):
                dims = {'top': self.top,
                        'left': self.left,
                        'width': self.width,
                        'height': self.height}
            elif hasattr(other, 'top'):
                dims = {'top': other.top,
                        'left': other.left,
                        'width': other.width,
                        'height': other.height}
            else:
                dims = {}
        strself = str(self)
        strother = str(other)
        # mandatory dehyphenation. FIXME: we'd like to make this
        # configurable (but where?).
        # 
        # FIXME: This dehyphenates eg "EG-" + "direktiv". How many
        # other exceptions to this algorithm are needed.
        if strself and strself[-1] == '-' and strother and strother[0].islower():
            strself = strself[:-1]
        new = self.__class__(strself + extraspace + strother, tag=self.tag, **dims)
        return new

class LinkedTextelement(Textelement):

    """Like Textelement, but with a uri property.
    """
        
    def __init__(self, *args, **kwargs):
        kwargs['tag'] = kwargs.get('tag')
        kwargs['uri'] = kwargs.get('uri')
        super(LinkedTextelement, self).__init__(*args, **kwargs)
    
    def _get_tagname(self):
        return "a"
    tagname = property(_get_tagname)

    def as_xhtml(self, uri, parent_uri=None):
        prevtag = self.tag
        if self.tag is None:
            self.tag = "a"
        else:
            self.tag = "a" + self.tag
        element = super(LinkedTextelement, self).as_xhtml(uri, parent_uri)
        self.tag = prevtag
        if element is not None:
            element.set("href", self.uri)
        return element

    def __add__(self, other):
        assert not type(other) == Textelement, "Can't join a LinkedTextelement (%s) with a plain Textelement (%s)" % (self, other)
        assert self.uri == other.uri, "Can't join two LinkedTextelements with different URIs (%s, %s)" % (self.uri, other.uri)
        new = super(LinkedTextelement, self).__add__(other)
        new.uri = self.uri
        return new

class BaseTextDecoder(object):
    def __init__(self, dummy=None):
        pass

    def __call__(self, textbox, fontspecs):
        return textbox

    def fontspec(self, fontspec):
        return fontspec
