# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import logging
import re
import itertools
import codecs
import tempfile
from glob import glob
from bz2 import BZ2File

from lxml import etree
from six import text_type as str
# from six import binary_type as bytes
import six

from ferenda import util, errors
from .elements import UnicodeElement
from .elements import CompoundElement
from .elements import OrdinalElement


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
    def __init__(self,
                 pages=None,
                 filename=None,
                 workdir=None,
                 images=True,
                 convert_to_pdf=False,
                 keep_xml=True,
                 ocr_lang=None,
                 fontspec=None):
        if pages: # special-case: The object has been initialized as a
                  # regular list (by deserialize), we have no need to
                  # parse and create pages.
            return
        self.log = logging.getLogger('pdfreader')
        self.fontspec = fontspec or {}
        self.filename = filename
        """Initializes a PDFReader object from an existing PDF file. After
        initialization, the PDFReader contains a list of
        :py:class:`~ferenda.pdfreader.Page` objects.

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
        self.workdir = workdir
        if self.workdir is None:
            self.workdir = tempfile.mkdtemp()
            
        if convert_to_pdf:
            newfilename = workdir + os.sep + os.path.splitext(os.path.basename(filename))[0] + ".pdf"
            if not os.path.exists(newfilename):
                util.ensure_dir(newfilename)
                cmdline = "soffice --headless -convert-to pdf -outdir '%s' %s" % (workdir, filename)
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
            if keep_xml == "bz2":
                with open(convertedfile, mode="rb") as rfp:
                    # BZ2File supports the with statement in py27+,
                    # but we support py2.6
                    wfp = BZ2File(real_convertedfile, "wb")
                    wfp.write(rfp.read())
                    wfp.close()
                os.unlink(convertedfile)
            else: # keep_xml = True
                pass

        if keep_xml == "bz2":
            # FIXME: explicitly state that encoding is utf-8 (in a
            # py26 compatible manner
            fp = BZ2File(real_convertedfile)
        else:
            fp = codecs.open(real_convertedfile, encoding="utf-8")

        res = parser(fp, real_convertedfile)

        fp.close()
        if keep_xml == False:
            os.unlink(convertedfile)
        return res

    def _tesseract(self, tmppdffile, workdir, lang):
        root = os.path.splitext(os.path.basename(tmppdffile))[0]

        # step 1: find the number of pages
        cmd = "pdfinfo %s" % tmppdffile
        (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
        m = re.search("Pages:\s+(\d+)", stdout)
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
            cmd = "pdfimages -p -f %(frompage)s -l %(topage)s %(tmppdffile)s %(workdir)s/%(root)s" % locals()
            self.log.debug("- running "+cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
            # step 2.1: Combine the recently extracted images and
            # into a new tif (so that we add 10
            # pages at a time to the tif, as imagemagick can
            # create a number of pretty large files for each page,
            # so converting 200 images will fill 10 G of your temp
            # space -- which we'd like to avoid)
            cmd = "convert %(workdir)s/%(root)s-*.pbm -compress Zip %(workdir)s/%(root)s-tmp%(idx)04d.tif" % locals()
            self.log.debug("- running " + cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
            # step 2.2: Remove pbm files now that they're in the .tif
            for f in glob("%(workdir)s/%(root)s-*.pbm" % locals()):
                os.unlink(f)

        # Step 3: Combine all the 10-page tifs into a giant tif using tiffcp
        cmd = "tiffcp -c zip %(workdir)s/%(root)s-tmp*.tif %(workdir)s/%(root)s.tif" % locals()
        self.log.debug("- running " + cmd)
        (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)
        
                
        # Step 3: OCR the giant tif file to create a .hocr.html file
        # Note that -psm 1 (automatic page segmentation with
        # orientation and script detection) requires the installation
        # of tesseract-ocr-3.01.osd.tar.gz
        cmd = "tesseract %(workdir)s/%(root)s.tif %(workdir)s/%(root)s.hocr -l %(lang)s -psm 1 hocr" % locals()
        self.log.debug("running " + cmd)
        (returncode, stdout, stderr) = util.runcmd(cmd, require_success=True)

        # Step 5: Cleanup (the main .tif file can stay)
        os.unlink(tmppdffile)
        for f in glob("%(workdir)s/%(root)s-tmp*.tif" % locals()):
            os.unlink(f)

    def _pdftohtml(self, tmppdffile, workdir, images):
        root = os.path.splitext(os.path.basename(tmppdffile))[0]
        try:
            if images:
                # two pass coding: First use -c (complex) to extract
                # background pictures, then use -xml to get easy-to-parse
                # text with bounding boxes.
                cmd = "pdftohtml -nodrm -c %s" % tmppdffile
                self.log.debug("Converting: %s" % cmd)
                (returncode, stdout, stderr) = util.runcmd(cmd,
                                                          require_success=True)
                # we won't need the html files, or the blank PNG files
                for f in os.listdir(workdir):
                    if f.startswith(root) and f.endswith(".html"):
                        os.unlink(workdir + os.sep + f)
                    elif f.startswith(root) and f.endswith(".png"):
                        # this checks the number of unique colors in the
                        # bitmap. If there's only one color, we don't need
                        # the file
                        (returncode, stdout, stderr) = util.runcmd('convert %s -format "%%k" info:' % (workdir + os.sep + f))
                        if stdout.strip() == "1":
                            os.unlink(workdir + os.sep + f)
                        else:
                            self.log.debug("Keeping non-blank image %s" % f)

            # Without -fontfullname, all fonts are just reported as
            # having family="Times"...
            imgflag = "-i" if not images else ""

            cmd = "pdftohtml -nodrm -xml -fontfullname %s %s" % (imgflag, tmppdffile)
            self.log.debug("Converting: %s" % cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd,
                                                       require_success=True)
            # if pdftohtml fails (if it's an old version that doesn't
            # support the fullfontname flag) it still uses returncode
            # 0! Only way to know if it failed is to inspect stderr
            # and look for if the xml file wasn't created.
            xmlfile = os.path.splitext(tmppdffile)[0] + ".xml"
            if stderr and not os.path.exists(xmlfile):
                raise errors.ExternalCommandError(stderr)
        finally:
            os.unlink(tmppdffile)
            assert not os.path.exists(tmppdffile)

    dims = "bbox (?P<left>\d+) (?P<top>\d+) (?P<right>\d+) (?P<bottom>\d+)"
    re_dimensions = re.compile(dims).search

    def _parse_hocr(self, fp, filename):
        def dimensions(s):
            m = self.re_dimensions(s)
            return m.groupdict()
        tree = etree.parse(fp)
        for pageelement in tree.findall("//{http://www.w3.org/1999/xhtml}div[@class='ocr_page']"):
            dim = dimensions(pageelement.get('title'))
            page = Page(number=int(pageelement.get('id')[5:]),
                        width=int(dim['right']) - int(dim['left']),
                        height=int(dim['bottom']) - int(dim['top']),
                        background=None)
            pageheight_in_mm = 242  # FIXME: get this from PDF
            pointsize = 0.352777778 # constant
            pageheight_in_points = pageheight_in_mm / pointsize
            px_per_point = page.height / pageheight_in_points

            # we discard elements at the ocr_carea (content area?)
            # level, we're only concerned with paragraph-level
            # elements
            for boxelement in pageelement.findall(".//{http://www.w3.org/1999/xhtml}span[@class='ocr_line']"):
                boxdim = dimensions(boxelement.get('title'))
                textelements = []
                for element in boxelement.findall(".//{http://www.w3.org/1999/xhtml}span[@class='ocrx_word']"):
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
                                       top=int(dim['top']),
                                       left=int(dim['left']),
                                       width=int(dim['right']) - int(dim['left']),
                                       height=int(dim['bottom']) - int(dim['top']),                    )
                    textelements.append(text)
                
                # Now that we know all text elements that should be in
                # the Textbox, we can guess the font size.
                fontspec = {'family': "unknown",
                            'size': str(int(round(text.height / px_per_point)))}

                # find any previous definition of this fontspec
                fontid = None
                for specid, spec in self.fontspec.items():
                    if fontspec == spec:
                        fontid = specid
                # None was found, create a new
                if not fontid:
                    fontid = str(len(self.fontspec)) # start at 0
                    self.fontspec[fontid] = fontspec

                # finally create the box and add all our elements
                # (should not be more than one?) to it
                box = Textbox(top=int(boxdim['top']),
                              left=int(boxdim['left']),
                              width=int(boxdim['right']) - int(boxdim['left']),
                              height=int(boxdim['bottom']) - int(boxdim['top']),
                              fontspec=self.fontspec,
                              font=fontid)
                for e in textelements:
                    box.append(e)
                page.append(box)
            self.append(page)
        self.log.debug("PDFReader initialized: %d pages" %
                       (len(self)))

    def _parse_xml(self, xmlfp, xmlfilename):
        def txt(element_text):
            return re.sub(r"[\s\xa0\xc2]+", " ", str(element_text))
            
        self.log.debug("Loading %s" % xmlfilename)

        try:
            tree = etree.parse(xmlfp)
        except etree.XMLSyntaxError as e:
            self.log.warning("pdftohtml created incorrect markup, trying to fix using BeautifulSoup: %s" % e)
            xmlfp.seek(0)
            from bs4 import BeautifulSoup
            from io import BytesIO
            soup = BeautifulSoup(xmlfp, "xml")
            xmlfp = BytesIO(str(soup).encode("utf-8"))
            tree = etree.parse(xmlfp)
            self.log.debug("BeautifulSoup workaround successful")

        # for each page element
        for pageelement in tree.getroot():
            if pageelement.tag == "outline":
                # FIXME: we want to do something with this information
                continue
            page = Page(number=int(pageelement.attrib['number']),  # alwaysint?
                        width=int(pageelement.attrib['width']),
                        height=int(pageelement.attrib['height']),
                        background=None)
            background = "%s%03d.png" % (
                os.path.splitext(xmlfilename)[0], page.number)

            # Reasons this file might not exist: it was blank and
            # therefore removed, or We're running under RepoTester
            if os.path.exists(background):
                page.background = background

            assert pageelement.tag == "page", "Got <%s>, expected <page>" % page.tag
            for element in pageelement:
                if element.tag == 'fontspec':
                    fontid =  element.attrib['id']
                    # make sure we always deal with a basic dict (not
                    # lxml.etree._Attrib) where all keys are str
                    # object (not bytes)
                    self.fontspec[fontid] = dict([(k,str(v)) for k,v in element.attrib.items()])
                    if "+" in element.attrib['family']:
                        self.fontspec[fontid]['family'] = element.attrib['family'].split("+",1)[1]
                    
                elif element.tag == 'text':
                    # eliminate "empty" textboxes
                    if element.text and txt(element.text).strip() == "" and not element.getchildren():
                        # print "Skipping empty box"
                        continue

                    attribs = dict(element.attrib)
                    # all textboxes share the same fontspec dict
                    attribs['fontspec'] = self.fontspec
                    b = Textbox(**attribs)

                    if element.text and element.text.strip():
                        b.append(Textelement(txt(element.text), tag=None))
                    # The below loop could be done recursively to
                    # support arbitrarily deep nesting (if we change
                    # Textelement to be a non-unicode derived type),
                    # but pdftohtml should not create such XML (there
                    # is no such data in the PDF file)
                    for child in element:
                        grandchildren = child.getchildren()
                        # special handling of the <i><b> construct
                        if grandchildren != []:
                            if child.text:
                                b.append(Textelement(txt(child.text), tag=child.tag))
                            b.append(Textelement(
                                txt(" ".join([x.text for x in grandchildren])), tag="ib"))
                            if child.tail:
                                b.append(Textelement(txt(child.tail), tag=None))
                        else:
                            b.append(
                                Textelement(txt(child.text), tag=child.tag))
                            if child.tail:
                                b.append(Textelement(txt(child.tail), tag=None))
                    if element.tail and element.tail.strip():  # can this happen?
                        b.append(Textelement(txt(element.tail), tag=None))
                    page.append(b)
            # done reading the page
            self.append(page)
        self.log.debug("PDFReader initialized: %d pages, %d fontspecs" %
                       (len(self), len(self.fontspec)))

    ################################################################
    # Properties and methods relating to the initialized PDFReader
    # object
    tagname = "div"
    classname = "pdfreader"

    def is_empty(self):
        return 0 == sum([len(x) for x in self])

    def textboxes(self, gluefunc=None, pageobjects=False, keepempty=False):
        """Return an iterator of the textboxes available.

        ``gluefunc`` should be a callable that is called with
        (textbox, nextbox, prevbox), and returns True iff nextbox
        should be appended to textbox.

        If ``pageobjects``, the iterator can return Page objects to
        signal that pagebreak has ocurred (these Page objects may or
        may not have Textbox elements).

        If ``keepempty``, process and return textboxes that have no
        text content (these are filtered out by default)
        """
        textbox = None
        prevbox = None
        if gluefunc:
            glue = gluefunc
        else:
            glue = self._default_glue
        for page in self:
            if pageobjects:
                yield page
            for nextbox in page:
                if not (keepempty or str(nextbox).strip()):
                    continue
                if not textbox: # MUST glue
                    textbox = nextbox
                else:
                    if glue(textbox, nextbox, prevbox):
                        textbox += nextbox
                    else:
                        yield textbox
                        textbox = nextbox
                prevbox = nextbox
            if textbox:
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
        # default logic: if lines are next to each other
        # horizontally, line up vertically, and have the same
        # font, then they should be glued
        linespacing = 1
        if (textbox.getfont() == nextbox.getfont() and
            textbox.left == nextbox.left and
            textbox.top + textbox.height + linespacing >= nextbox.top):
            return True


class Page(CompoundElement, OrdinalElement):

    """Represents a Page in a PDF file. Has *width* and *height* properties."""

    tagname = "div"
    classname = "pdfpage"
    margins = None
    
    @property
    def id(self):
        # FIXME: this will only work for documents consisting of a
        # single PDF file, not multiple (see
        # pdfdocumentrepository.create_external_resources to
        # understand why)
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
        return "Page %d (%d x %d): '%s...'" % (self.number, self.width, self.height, str(textexcerpt[:40]))

    def __repr__(self):
        return '<%s %d (%dx%d): %d textboxes>' % (self.__class__.__name__,
                                                   self.number, self.width, self.height,
                                                   len(self))

class Textbox(CompoundElement):

    """A textbox is a amount of text on a PDF page, with *top*, *left*,
*width* and *height* properties that specifies the bounding box of the
text. The *font* property specifies the id of font used (use
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
        assert 'font' in kwargs, "font id attribute missing"

        self.top = int(kwargs['top'])
        self.left = int(kwargs['left'])
        self.width = int(kwargs['width'])
        self.height = int(kwargs['height'])
        self.right = self.left + self.width
        self.bottom = self.top + self.height

        # self.__fontspecid = kwargs['font']
        self.font = kwargs['font'] or 0
        if 'fontspec' in kwargs:
            self.__fontspec = kwargs['fontspec'] 
            del kwargs['fontspec']
        else:
            self.__fontspec = {}
        del kwargs['top']
        del kwargs['left']
        del kwargs['width']
        del kwargs['height']
        del kwargs['font']

        super(Textbox, self).__init__(*args, **kwargs)

    def __str__(self):
        return "".join(self)

    def __repr__(self):
        # <Textbox 30x18+278+257 "5.1">
        # <Textbox 430x14+287+315 "Regeringens fÃ¶rslag: NÃ¤[...]g ska ">
        s = str(self)
        if len(s) > 40:
            s = s[:25] + "[...]" + s[-10:]

        if six.PY2:
            # s = repr(s)
            s = s.encode('ascii', 'replace')
        if self.getfont():
            fontinfo = "%s@%s " % (self.getfont()['family'],
                                  self.getfont()['size'])
        else:
            fontinfo = ""
        return '<%s %sx%s+%s+%s %s"%s">' % (self.__class__.__name__,
                                            self.width, self.height,
                                            self.left, self.top,
                                            fontinfo,
                                            s)
    def __add__(self, other):
        # expand dimensions
        top = min(self.top, other.top)
        left = min(self.left, other.left)
        width = max(self.left + self.width,
                        other.left + other.width) - left
        height = max(self.top + self.height,
                          other.top + other.height) - top

        res = Textbox(top=top, left=left, width=width, height=height,
                      font=self.font,
                      fontspec=self.__fontspec)
        
        # add all text elements
        c = Textelement(tag=None)
        for e in itertools.chain(self, other):
            if e.tag != c.tag:
                res.append(c)
                c = Textelement(tag=e.tag)
            else:
                c = c + e
        res.append(c)
        return res


    def __iadd__(self, other):
        if len(self):
            c = self.pop()
        else:
            c = Textelement(tag=None)
        for e in other:
            if e.tag != c.tag:
                self.append(c)
                self.append(e)
                c = Textelement(tag=e.tag)
                # c = e
            else:
                c = c + e
        self.append(c)
        self.top = min(self.top, other.top)
        self.left = min(self.left, other.left)
        self.width = max(self.left + self.width,
                         other.left + other.width) - self.left
        self.height = max(self.top + self.height,
                          other.top + other.height) - self.top
        return self
        
    def as_xhtml(self, uri, parent_uri=None):
        element = super(Textbox, self).as_xhtml(uri, parent_uri)
        # FIXME: we should output these positioned style attributes
        # only when the resulting document is being serialized in a
        # positioned fashion. Possibly do some translation from PDF
        # points (which is what self.top, .left etc is using) and
        # pixels (which is what the CSS uses)
        element.set('style', 'top: %spx, left: %spx, height: %spx, width: %spx' % (self.top, self.left, self.height, self.width))
        return element


    def getfont(self):
        """Returns a fontspec dict of all properties of the font used."""
        #
        # this would be a place to insert fontmapping functionality
        # "TimesNewRomanPS-ItalicMT" => "Times New Roman,Italic"
        if self.font:
            return self.__fontspec[self.font]
        else:
            return {}
        
# this doesnt work that well with the default __setattribute__
# implementation of this class' superclass.
# 
#    @property
#    def font(self):
#        return self.getfont()
#
#    @font.setter
#    def font(self, value):
#        for fontspecid, fontspec in self.__fontspec.items():
#            if value == fontspecid:
#                self.font = fontspecid
#        if self.font is None:   # .font might have the valid value 0
#            self.font = str(len(self.__fontspecid)) # start at 0
#            self.__fontspec[self.font] = value
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

    tagname = property(_get_tagname)

    def __add__(self, other):
        return Textelement(str(self) + str(other), tag = self.tag)
    

# The below code fixes a error with incorrectly nested tags often
# found in pdftohtml generated xml. Main problem is that this relies
# on sgmllib which is not included in python3. This is commented out
# in the hope that more recent pdftohtml versions fix this problem in
# the right place

# import sgmllib
# from xml.sax.saxutils import escape as xml_escape
# import unicodedata
#
# class PDFXMLFix(sgmllib.SGMLParser):
#     selfclosing = ["fontspec"]
#
# preparations to remove invalid chars in handle_data
#     all_chars = (unichr(i) for i in range(0x10000))
#     control_chars = ''.join(
#         c for c in all_chars if unicodedata.category(c) == 'Cc')
# tab and newline are technically Control characters in
# unicode, but we want to keep them.
#     control_chars = control_chars.replace("\t", "").replace("\n", "")
#     control_char_re = re.compile('[%s]' % re.escape(control_chars))
#
#     def __init__(self):
#         sgmllib.SGMLParser.__init__(self)
#         self.tags = []
#         self.fp = None
#
#     def fix(self, filename):
#         usetempfile = not self.fp
#
#         if usetempfile:
#             tmpfile = mktemp()
#             self.fp = open(tmpfile, "w")
#
#         self.fp.write('<?xml version="1.0" encoding="UTF-8"?>')
#
#         f = open(filename)
#         while True:
#             s = f.read(8192)
#             if not s:
#                 break
#             self.feed(s)
#         self.close()
#
#         if usetempfile:
#             self.fp.close()
#             if util.replace_if_different(tmpfile, filename):
#                 print(("replaced %s with %s" % (filename, tmpfile)))
#             else:
#                 print(("%s was identical to %s" % (filename, tmpfile)))
#
#     def close(self):
#         sgmllib.SGMLParser.close(self)
#         if self.tags:
#             sys.stderr.write(
#                 "WARNING: opened tag(s) %s not closed" % self.tags)
#             self.fp.write(
#                 "".join(["</%s>" % x for x in reversed(self.tags)]))
#
#     def handle_decl(self, decl):
# self.fp.write "Decl: ", decl
#         self.fp.write("<!%s>" % decl)
#
#     def handle_data(self, data):
#         len_before = len(data)
#         data = xml_escape(self.control_char_re.sub('', data))
#         len_after = len(data)
# self.fp.write "Data: ", data.strip()
# if len_before != len_after:
# sys.stderr.write("WARNING: data changed from %s to %s chars: %r\n" % (len_before,len_after,data))
#         self.fp.write(data)
#
#     def unknown_starttag(self, start, attrs):
# self.fp.write "Start: ", start, attrs
#         if start in self.selfclosing:
#             close = "/"
#         else:
#             close = ""
#             self.tags.append(start)
# sys.stderr.write(repr(self.tags)+"\n")
#         if attrs:
#             fmt = ['%s="%s"' % (x[0], x[1]) for x in attrs]
#             self.fp.write("<%s %s%s>" % (start, " ".join(fmt), close))
#         else:
#             self.fp.write("<%s>" % start)
#
#     def unknown_endtag(self, end):
# sys.stderr.write(repr(self.tags)+"\n")
#         start = self.tags.pop()
#         if end != start and end in self.tags:
# sys.stderr.write("%s is not %s, working around\n" % (end, start))
#             self.fp.write("</%s>" % start)
#             self.fp.write("</%s>" % end)
#             self.fp.write("<%s>" % start)
#         else:
#             self.fp.write("</%s>" % end)
