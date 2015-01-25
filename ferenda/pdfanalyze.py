# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""A bunch of helper functions for analyzing pdf files."""
import os
import logging
from collections import Counter

from six import text_type as str

from .pdfreader import Page
from ferenda import util

def drawboxes(pdffile, gluefunc=None):
    """Create a copy of the parsed PDF file, but with the textboxes
    created by ``gluefunc`` clearly marked. Returns the name of
    the created pdf file.

    ..note::

      This requires PyPDF2 and reportlab, which aren't installed
      by default. Reportlab (3.*) only works on py27+ and py33+

    """
    from PyPDF2 import PdfFileWriter, PdfFileReader
    from reportlab.pdfgen import canvas
    import StringIO
    log = logging.getLogger("pdfanalyze")
    packet = None
    output = PdfFileWriter()
    existing_pdf = PdfFileReader(open(pdffile.filename, "rb"))
    pageidx = 0
    sf = 2/3.0 # scaling factor
    dirty = False
    for tb in pdffile.textboxes(gluefunc, pageobjects=True):
        if isinstance(tb, Page):
            if dirty:
                can.save()
                packet.seek(0)
                new_pdf = PdfFileReader(packet)
                log.debug("Getting page %s from existing pdf" % pageidx)
                page = existing_pdf.getPage(pageidx)
                page.mergePage(new_pdf.getPage(0))
                output.addPage(page)
                pageidx += 1
            pagesize = (tb.width*sf, tb.height*sf)
            # print("pagesize %s x %s" % pagesize)
            packet = StringIO.StringIO()
            can = canvas.Canvas(packet, pagesize=pagesize,
                                bottomup=False)
            can.setStrokeColorRGB(0.2,0.5,0.3)
            can.translate(0,0)
        else:
            dirty = True
            # x = repr(tb)
            # print(x)
            can.rect(tb.left*sf, tb.top*sf,
                     tb.width*sf, tb.height*sf)

    packet.seek(0)
    can.save()
    new_pdf = PdfFileReader(packet)
    log.debug("Getting last page %s from existing pdf" % pageidx)
    page = existing_pdf.getPage(pageidx)
    page.mergePage(new_pdf.getPage(0))
    output.addPage(page)
    outfile = "marked/"+os.path.basename(pdffile.filename).replace(".pdf", ".marked.pdf")
    outputStream = open(outfile, "wb")
    output.write(outputStream)
    outputStream.close()
    log.debug("wrote %s" % outfile)
    return outfile

# attempt to analyze a PDFReader object with statistical methods
# to find out probable margins, section styles, etc.
def analyze_metrics(pdf, twopage=True):
    log = logging.getLogger("pdfanalyze")

    # if twopage, assume even and odd pages have differing
    # margins.
    # Keep a running tab on all left-and-right positions for even
    # and odd pages, respectively.
    if twopage:
        odd_leftmarg = []
        even_leftmarg = []
        odd_rightmarg = []
        even_rightmarg = []
    else:
        # do not keep different tabs for even/odd pages
        odd_leftmarg = even_leftmarg = []
        odd_rightmarg = even_rightmarg = []
    styles = Counter()
    pagewidths = Counter()
    for (idx, page) in enumerate(pdf):
        physical_pageno = idx + 1
        logical_pageno = None
        # odd pages are right hand pages, even are left hand pages
        lefthandpage = physical_pageno % 2 == 0

        # assume that content in the bottom 1/10 of the page might
        # be footer information
        footer_zone = page.height * 0.9

        pagewidths[page.width] += 1
        # analyze the page margins for this particular page
        for textbox in page:
            text = str(textbox).strip()
            if lefthandpage:
                even_leftmarg.append(textbox.left)
                even_rightmarg.append(textbox.right)
            else:
                odd_leftmarg.append(textbox.left)
                odd_rightmarg.append(textbox.right)

            # if a TB is in the footer zone, suitably small (max
            # 5% of page width) and contains only a digit, assume
            # that it's a pagenumber
            if (textbox.top > footer_zone and
                textbox.width < page.width * 0.05 and
                text.isdigit()):
                assert logical_pageno is None, "Found two logical pagenos on physical page %s: %s and %s" % (physical_pageno, logical_pageno, text)
                logical_pageno = text
            f = textbox.getfont()
            styles[(f['family'], f['size'])] += len(text)
    # find the probable left margin = the place where most textboxes start
    pagewidth = pagewidths.most_common()[0][0]
    plot = True
    if plot:
        import matplotlib
        import matplotlib.pyplot as plt
        # plt.style.use('ggplot')  # looks good but makes our histograms unreadable
        from matplotlib.font_manager import FontProperties
        matplotlib.rcParams.update({'font.size': 8})
        # make a 2x3 grid of subplots where the last (5th) spans 2 cols
        for (plot, data) in zip([plt.subplot2grid((2,3), (0,0)),
                                 plt.subplot2grid((2,3), (0,1)),
                                 plt.subplot2grid((2,3), (0,2)),
                                 plt.subplot2grid((2,3), (1,0))],
                                [(even_leftmarg, "Textbox left property (even-hand pages)"),
                                 (odd_leftmarg, "Textbox left property (odd-hand pages)"),
                                 (even_rightmarg, "Textbox right property (even-hand pages)"),
                                 (odd_rightmarg, "Textbox right property (odd-hand pages)")]):
            series, label = data
            plot.hist(series, bins=max(pagewidths), color='k')
            plot.set_title(label, fontdict={'fontsize': 7})
            (maxval, maxcnt) = Counter(series).most_common(1)[0]
            print("analyze_metrics: %s: Top val %s (%s times)" % (label, maxval, maxcnt))
            plot.annotate(maxval, xy=(maxval, maxcnt),
                          xytext=(maxval*0.5, maxcnt*0.9),
                          arrowprops=dict(arrowstyle="->"))
        plot = plt.subplot2grid((2,3), (1,1), colspan=2)
        stylenames = [x[0][0].replace("TimesNewRomanPS", "Times")+"@"+x[0][1] for x in styles.most_common()]
        stylecounts = [x[1] for x in styles.most_common()]
        plt.yticks(range(len(styles)), stylenames, fontproperties=FontProperties(size=8))
        plot.barh(range(len(styles)), stylecounts, log=True)
        plot.set_title("Font usage", fontdict={'fontsize': 8})
        filename = "plots/"+os.path.basename(pdf.filename).replace(".pdf", ".marginhist.png")
        util.ensure_dir(filename)
        plt.savefig(filename, dpi=300)
        log.debug("analyze_metrics: Saved plot as %s" % filename)
    even_left = sorted(Counter(even_leftmarg).most_common(1), reverse=True)[0][0]
    even_right = sorted(Counter(even_rightmarg).most_common(1), reverse=True)[0][0]
    odd_left = sorted(Counter(odd_leftmarg).most_common(1), reverse=True)[0][0]
    odd_right = sorted(Counter(odd_rightmarg).most_common(1), reverse=True)[0][0]

    assert even_left < pagewidth / 2, "leftmargin shouldn't be on the right hand side of the page"
    assert odd_left < pagewidth / 2, "leftmargin shouldn't be on the right hand side of the page"
    # possible_indents = filter(lambda x: x[0] > leftmargin,
    #                           sorted(boxleft.most_common(),
    #                                  key=itemgetter(0)))
    # the preferred paragraph indent should be around
    # page.width / 58 in from leftmargin
    even_parindent = even_left + (pagewidth / 58)
    odd_parindent = odd_left + (pagewidth / 58)
    margins = {'even_leftmargin': even_left,
               'even_rightmargin': even_right,
               'even_parindent': even_parindent,
               'odd_leftmargin': odd_left,
               'odd_rightmargin': odd_right,
               'odd_parindent': odd_parindent}
    log.debug("Margins: %r" % margins)
    return margins
