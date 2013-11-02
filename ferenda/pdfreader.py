# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
from lxml import etree
import logging

from six import text_type as str

from ferenda import util
from .elements import UnicodeElement
from .elements import CompoundElement
from .elements import OrdinalElement


class PDFReader(CompoundElement):

    """Parses PDF files and makes the content available as a object
    hierarchy. After calling :py:meth:`~ferenda.PDFReader.read`, the
    PDFReader itself is a list of :py:class:`ferenda.pdfreader.Page`
    objects, which each is a list of
    :py:class:`ferenda.pdfreader.Textbox` objects, which each is a
    list of :py:class:`ferenda.pdfreader.Textelement` objects.

    .. note::

       This class depends on the command line tool pdftohtml from
       `poppler <http://poppler.freedesktop.org/>`_.

    """

    def __init__(self):
        self.fontspec = {}
        self.log = logging.getLogger('pdfreader')

    def read(self, pdffile, workdir=None):
        """Initializes a PDFReader object from an existing PDF file. After
        initialization, the PDFReader contains a list of
        :py:class:`~ferenda.pdfreader.Page` objects.

        :param pdffile: The full path to the PDF file
        :param workdir: A directory where intermediate files (particularly
                        background PNG files) are stored

        """

        self.filename = pdffile
        assert os.path.exists(pdffile), "PDF %s not found" % pdffile
        if not workdir:
            workdir = os.path.dirname(pdffile)
        basename = os.path.basename(pdffile)
        xmlfile = os.sep.join(
            (workdir, os.path.splitext(basename)[0] + ".xml"))

        if not util.outfile_is_newer([pdffile], xmlfile):
            tmppdffile = os.sep.join([workdir, basename])
            util.copy_if_different(pdffile, tmppdffile)
            # two pass coding: First use -c (complex) to extract
            # background pictures, then use -xml to get easy-to-parse
            # text with bounding boxes.
            cmd = "pdftohtml -nodrm -c %s" % tmppdffile
            self.log.debug("Converting: %s" % cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd,
                                                       require_success=True)
            # we won't need the html files
            for f in os.listdir(workdir):
                if f.endswith(".html"):
                    os.unlink(workdir + os.sep + f)

            cmd = "pdftohtml -nodrm -xml %s" % tmppdffile
            self.log.debug("Converting: %s" % cmd)
            (returncode, stdout, stderr) = util.runcmd(cmd,
                                                       require_success=True)
        return self._parse_xml(xmlfile)

    # def set_background_path():
    #    pass

    def _parse_xml(self, xmlfile):
        self.log.debug("Loading %s" % xmlfile)
        assert os.path.exists(xmlfile), "XML %s not found" % xmlfile
        tree = etree.parse(xmlfile)

        # for each page element
        for pageelement in tree.getroot():
            if pageelement.tag == "outline":
                # FIXME: we want to do something with this information
                continue
            page = Page(number=int(pageelement.attrib['number']),  # always int?
                        width=int(pageelement.attrib['width']),
                        height=int(pageelement.attrib['height']),
                        background=None)
            background = "%s%03d.png" % (
                os.path.splitext(xmlfile)[0], page.number)
            if os.path.exists(background):
                page.background = background
            else:
                print(("Did not set %s as background: Doesn't exist" %
                      background))

            # print("Creating page %s" % page.number)
            assert pageelement.tag == "page", "Got <%s>, expected <page>" % page.tag
            for element in pageelement:
                if element.tag == 'fontspec':
                    self.fontspec[element.attrib['id']] = element.attrib
                elif element.tag == 'text':
                    # eliminate "empty" textboxes
                    if element.text and element.text.strip() == "" and not element.getchildren():
                        # print "Skipping empty box"
                        continue
                    attribs = dict(element.attrib)
                    attribs['fontspec'] = self.fontspec
                    b = Textbox(**attribs)

                    if element.text and element.text.strip():
                        b.append(Textelement(str(element.text)))
                    # The below loop could be done recursively to
                    # support arbitrarily deep nesting (if we change
                    # Textelement to be a non-unicode derived type),
                    # but pdftohtml should not create such XML (there
                    # is no such data in the PDF file)
                    for child in element:
                        grandchildren = child.getchildren()
                        # special handling of the <i><b> construct
                        if grandchildren != []:
                            # print "Grandchildren handling: %s '%s' '%s'" % (len(grandchildren),
                            #                                                child.text,
                            #                                                child.tail)
                            assert (len(grandchildren) == 1), "General grandchildren not supported"
                            if child.text:
                                Textelement(str(child.text), tag=child.tag)
                            b.append(Textelement(
                                str(grandchildren[0].text), tag="ib"))
                            if child.tail:
                                Textelement(str(child.tail), tag=child.tag)
                        else:
                            b.append(
                                Textelement(str(child.text), tag=child.tag))
                        if child.tail:
                            b.append(Textelement(str(child.tail)))
                    if element.tail and element.tail.strip():  # can this happen?
                        b.append(Textelement(str(element.tail)))
                    page.append(b)
            # done reading the page
            self.append(page)
        self.log.debug("PDFReader initialized: %d pages, %d fontspecs" %
                       (len(self), len(self.fontspec)))

#    def avg_font_size(self):
#        pass
#
#    def median_font_size(self):
#        pass

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

#    def common_horizontal_gutters():
#        pass
#
#    def common_vertical_gutters():
#        pass


class Page(CompoundElement, OrdinalElement):

    """Represents a Page in a PDF file. Has *width* and *height* properties."""

    def vertical_gutters(self):
        """
        Returns the x-coordinates for the start and end of the left and right gutter (the part of the page which contains no text) of the Page.

        .. note::

            This property does not work (returns hardcoded values)
        """
        return ((0, 42), (463, 482))

    def horizontal_gutters(self):
        """
        Returns the y-coordinates for the start and end of the top and bottom gutter (the part of the page which contains no text) of the Page.

        .. note::

            This property does not work (returns hardcoded values)
        """
        return ((0, 42), (463, 482))

    # text: can be string, re obj or callable (gets called with the box obj)
    # fontsize: can be int or callable
    # fontname: can be string or callable
    # top,left,bottom,right
    def boundingbox(self, top=0, left=0, bottom=None, right=None):
        """A generator :py:class:`ferenda.pdfreader.Textbox` objects that fit into the bounding box specified by the parameters."""
        if not bottom:
            bottom = self.height
        if not right:
            right = self.width
        for box in self:
            # print u"    Examining [%dx%d][%dx%d] against constraints [%dx%d][%dx%d]"
            # % (box.top,box.left,box.bottom,box.right, top,left,bottom,right)

            # if (box.top >= top): print "        Top OK"
            # if (box.left >= left): print "        Left OK"
            # if (box.bottom <= bottom): print "        Bottom OK"
            # if (box.right <= right): print "        Right OK"

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

    def __unicode__(self):
        textexcerpt = " ".join([str(x) for x in self])
        return "Page %d (%d x %d): '%s...'" % (self.number, self.width, self.height, str(textexcerpt[:40]))

    def __str__(self):
        return str(self).encode('ascii')


class Textbox(CompoundElement):

    """A textbox is a amount of text on a PDF page, with *top*, *left*,
*width* and *height* properties that specifies the bounding box of the
text. The *font* property specifies the id of font used (use
:py:meth:`~ferenda.pdfreader.Textbox.getfont` to get a dict of all
font properties). A textbox consists of a list of Textelements which
may differ in basic formatting (bold and or italics), but otherwise
all text in a Textbox has the same font and size.

    """

    def __init__(self, *args, **kwargs):
        assert 'top' in kwargs, "top attribute missing"
        assert 'left' in kwargs, "left attribute missing"
        assert 'width' in kwargs, "width attribute missing"
        assert 'height' in kwargs, "height attribute missing"
        assert 'font' in kwargs, "font attribute missing"

        self.top = int(kwargs['top'])
        self.left = int(kwargs['left'])
        self.width = int(kwargs['width'])
        self.height = int(kwargs['height'])
        self.right = self.left + self.width
        self.bottom = self.top + self.height

        self.__fontspecid = kwargs['font']
        self.__fontspec = kwargs['fontspec']

        del kwargs['top']
        del kwargs['left']
        del kwargs['width']
        del kwargs['height']
        del kwargs['font']
        del kwargs['fontspec']

        super(Textbox, self).__init__(*args, **kwargs)

    def __unicode__(self):
        return "".join(self)

    def getfont(self):
        """Returns a fontspec dict of all properties of the font used."""
        return self.__fontspec[self.__fontspecid]


class Textelement(UnicodeElement):

    """Represent a single part of text where each letter has the exact
    same formatting. The ``tag`` property specifies whether the text
    as a whole is bold (``'b'``) , italic(``'i'`` bold + italic
    (``'bi'``) or regular (``None``).
    """

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
