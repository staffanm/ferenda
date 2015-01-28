# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""A bunch of helper functions for analyzing pdf files."""
import os
import logging
from collections import Counter

from six import text_type as str

from .pdfreader import Page
from ferenda import util

#class PDFAnalyzer(object)
#    twopage = True
#    def __init__(self, pdf): pass
#
#
#    def documents(self): 
#        # -> [148, 14]  returns a list of pagecounts for different 
#        # "documents" (differing margins/styles) within this file
#
#
#    def metrics(self, plot=None, [metrics_path, startpage, pagecount]):
#        # -> {'footer': 73, ...}, uses metrics_path as cache if provided
#        if plot:
#            self.plot(all_the_counters, markers_of_interest, also stylehistograms...)
#
#    
#    def count_horizontal_margins(self, [startpage, pagecount]):
#        # -> (leftmarg, rightmarg, leftmarg_even, rightmarg_even) (counter objects)
#        # counter objects. topmarg/bottomarg have character counts,
#        # others have textbox count
#        for page in self.pdf[startpage:startpage+pagecount]:
#            for textbox in page:
#                rightmarg_even[textbox.right] += 1

    
#    def count_vertical_margins(self, [startpage, pagecount]):
#        # -> (leftmarg, rightmarg, leftmarg_even, rightmarg_even) (counter objects)
#        for page in self.pdf[startpage:startpage+pagecount]:
#            for textbox in page:
#                topmarg[textbox.top] += len(str(textbox))
		

#    def fontsize_key(fonttuple): pass
#    def fontdict(fonttuple): pass 
#    def make_stylecounter(styles): pass

#    frontmatter_pagecount = 1 (maybe 2 for SOU?)
#    def count_styles(self, [startpage, pagecount])
#
#        # -> (frontmatter_styles, rest_styles) (counter objects)
#        for page in self.pdf[startpage:startpage+pagecount]:
#            for textbox in page:
#                fonttuple = (textbox.font.family, textbox.font.size)
#                styles[
#
#    # this (and subparts) are suitable for overriding. The question is how to 
#    # optimally structure this into the correct submethods to minimize the 
#    # code by overriders. 
#    def analyze_styles(self, frontmatter_styles, rest_styles):
#        # -> {'default': {'family': 'TimesNewRoman', 'size': 12}, 
#        #     'title': ... ['h1', 'h2', 'h3', 'footnote' etc] }
#
#    def analyze_vertical_margins(leftmarg, rightmarg, leftmarg_even, rightmarg_even)
#        # -> {'leftmargin', 55, rightmargin: 698, 'leftmargin_even': 123, 'rightmargin_even': 755, ...}
#        # 
#        # subclasses can call the base implementation and then add other metrics (primary 'parindent' but 
#        # also '2ndcolumn' and other stuff)
#
#    # this is suitable for overriding, eg if you know that no pagenumbers occur in the footer
#    def analyze_horizontal_margins(topmarg, bottommarg):
#        # -> {'header': ..., 'footer': ...}

#    def drawboxes(self, gluefunc, outfilename, [startpage, pagecount, counters, metrics]):
#        # for each page in pagespan:
#        # load page from original pdf file
#        # run gluefunc on page.textboxes
#        # for each textbox draw a square 
#        # add sequence number for textbox in lower right corner
#        # if textbox style matches any in metrics, add the style name in upper left corner
#        # for each metrics that's an integer
#        #    if metric in ('header', 'footer') draw horizontal line
#        #    otherwise draw vertical line
#        #    label the line
#        # for each area in header, footer, leftmarg[_even], rightmarg[_even]:
#        #    select proper counter to draw in each area:
#        #      (headercounter->left gutter, 
#        #       footercounter->right gutter, 
#        #       leftmargcounter->header,
#        #       rightmargcounter->footer)
#        #   find the most frequent value in the selected counter, normalize against the space in the area
#        #   for each value of the counter draw single-width line at correct position 

#    def plot(all_counters, metrics, filename):
#          
#        # make subplot grid based on the number of counters
#        margin_plots = (plt.grid2subplot(...),
#                        ...)
#        margin_counters = all_counters - (frontmatter_styles, rest_styles)
#        plot_margins(margin_plots, margin_counters, metrics)
#
#        style_plot = plt.grid2subplot(..., colspan=2)
#        plot_styles(frontmatter_styles, rest_styles, metrics)
#
#        # save plt to filename


#    def plot_margins(subplots, margin_counters, metrics):
#        # one plot per counter (4 or 6)
#        # map each metric (6+ ) to correct plot and point it out

#    def plot_styles(subplot, frontmatter_styles, rest_styles, metrics)
#	# do a additive vhist. label those styles identified in metrics

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

def fontsize_key(fonttuple):
    family, size = fonttuple
    if "Bold" in family:
        weight = 2
    elif "Italic" in family:
        weight = 1
    else:
        weight = 0
    return (size, weight)


def fontdict(fonttuple):
    return {'family': fonttuple[0],
            'size': fonttuple[1]}


def make_stylecounter(styles):
    # Given a list of (styleobject, charcount) tuples, returns a
    # counter keyed on (style.family, style.size) mapped to the total
    # amount of chars
    stylecount = Counter()
    for style, length in styles:
        stylecount[(style.family, style.size)] += length
    return stylecount

def default_style_analyzer(styles, firstpagelen):
    styledefs = {}
    # styles: an list of (styleobject, charcount) tuples (cannot
    # just be an iterable, we need to traverse it multiple times).
    # firstpagelen: the number of such tuples on the first page
    stylecount = make_stylecounter(styles)

    # default style: the one that's most common
    ds = stylecount.most_common(1)[0][0]
    styledefs['default'] = fontdict(ds)

    # title style: the largest style that exists on the frontpage style
    # objects are (should be) sortable. for style objects at the same
    # size, Bold > Italic > Regular
    frontpagecount = make_stylecounter(styles[:firstpagelen])
    frontpagestyles = frontpagecount.keys()
    ts = sorted(frontpagestyles, key=fontsize_key, reverse=True)[0]
    styledefs['title'] = fontdict(ts)

    # h1 - h3: Take all styles larger than or equal to default, with
    # significant use (each > 0.5 % of all chars from page 2 onwards,
    # as the front page often uses nontypical styles), then order
    # styles by font size.
    restcount = make_stylecounter(styles[firstpagelen:])
    significantuse = sum(restcount.values()) * 0.005
    largestyles = [x for x in sorted(restcount, key=fontsize_key, reverse=True) if fontsize_key(x) > fontsize_key(ds) and stylecount[x] > significantuse]

    for style in ('h1', 'h2', 'h3'):
        if largestyles: # any left?
            styledefs[style] = fontdict(largestyles.pop(0))
    return styledefs
    
def analyze_metrics(pdf, twopage=True, style_analyzer=default_style_analyzer):
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

    topmarg = Counter()
    bottommarg = Counter()

    styles = []
    pagewidths = []
    pageheights = []
    firstpagelen = 0
    for (idx, page) in enumerate(pdf):
        physical_pageno = idx + 1
        logical_pageno = None
        # odd pages are right hand pages, even are left hand pages
        lefthandpage = physical_pageno % 2 == 0

        # assume that content in the bottom 1/10 of the page might
        # be footer information
        footer_zone = page.height * 0.9

        pagewidths.append(page.width)
        pageheights.append(page.height)
        # analyze the page margins for this particular page
        for textbox in page:
            if idx == 0:
                firstpagelen += 1
            text = str(textbox).strip()
            if lefthandpage:
                even_leftmarg.append(textbox.left)
                even_rightmarg.append(textbox.right)
            else:
                odd_leftmarg.append(textbox.left)
                odd_rightmarg.append(textbox.right)

            topmarg[textbox.top] += len(text)
            bottommarg[textbox.top] += len(text)

            # if a TB is in the footer zone, suitably small (max
            # 5% of page width) and contains only a digit, assume
            # that it's a pagenumber
            if (textbox.top > footer_zone and
                textbox.width < page.width * 0.05 and
                text.isdigit()):
                assert logical_pageno is None, "Found two logical pagenos on physical page %s: %s and %s" % (physical_pageno, logical_pageno, text)
                logical_pageno = text
            f = textbox.font
            styles.append((f, len(text)))
    pagewidth = Counter(pagewidths).most_common()[0][0]

    bname = os.path.basename
    filename = "plots/"+bname(pdf.filename).replace(".pdf", ".marginhist.png")
    if os.environ.get("FERENDA_PLOTSTATS"):
        print("Plotting stats")
        _plot_stats(filename,
                    even_leftmarg, odd_leftmarg, even_rightmarg, odd_rightmarg,
                    topmarg, bottommarg,
                    styles, pagewidths, pageheights)
    else:
        print("Not plotting stats")

    # find the probable left margin = the place where most textboxes start
    even_left = sorted(Counter(even_leftmarg).most_common(1), reverse=True)[0][0]
    odd_left = sorted(Counter(odd_leftmarg).most_common(1), reverse=True)[0][0]
    # same with right margin (note that the effect won't be as
    # pronounced, particularly if the text is not right-justified)
    even_right = sorted(Counter(even_rightmarg).most_common(1), reverse=True)[0][0]
    odd_right = sorted(Counter(odd_rightmarg).most_common(1), reverse=True)[0][0]

    assert even_left < pagewidth / 2, "leftmargin shouldn't be on the right hand side of the page"
    assert odd_left < pagewidth / 2, "leftmargin shouldn't be on the right hand side of the page"

    # now find probable header and footer zones. default algorithm:
    # max 0.2 % of text content can be in the header/footer zone. (on
    # a page of 2000 chars, only 4 can be in the footer)
    #
    # FIXME: this risks yielding too tight header/footer values for
    # documents that don't actually have any header/footer text. In
    # these cases, maxcount should be statically set to 1 (cutting the
    # header/footer area before the very first occurrence of text)
    maxcount = sum(topmarg.values()) * 0.002
    charcount = 0
    for i in range(max(pageheights)):
        charcount += topmarg.get(i, 0)
        if charcount >= maxcount:
            header = i - 1
            break
    charcount = 0
    maxcount = 1  # ie don't have any footer
    for i in range(max(pageheights)-1, -1, -1):
        charcount += bottommarg.get(i, 0)
        if charcount >= maxcount:
            footer = i + 1
            break

    # possible_indents = filter(lambda x: x[0] > leftmargin,
    #                           sorted(boxleft.most_common(),
    #                                  key=itemgetter(0)))
    # the preferred paragraph indent should be around page.width / 58
    # in from leftmargin. FIXME: look for the most significant bump in
    # this region, don't just assume pagewith/58

    even_parindent = even_left + (pagewidth / 58)
    odd_parindent = odd_left + (pagewidth / 58)

    metrics = {'even_leftmargin': even_left,
               'even_rightmargin': even_right,
               'even_parindent': even_parindent,
               'odd_leftmargin': odd_left,
               'odd_rightmargin': odd_right,
               'odd_parindent': odd_parindent,
               'header': header,
               'footer': footer}

    styledefs = style_analyzer(styles, firstpagelen)
    metrics.update(styledefs)
    log.debug("Metrics: %r" % metrics)
    return metrics


def _plot_stats(filename,
                even_leftmarg, odd_leftmarg, even_rightmarg, odd_rightmarg,
                topmarg, bottommarg,
                styles, pagewidths, pageheights):
    import matplotlib
    import matplotlib.pyplot as plt
    log = logging.getLogger("pdfanalyze")
    # plt.style.use('ggplot')  # looks good but makes our histograms unreadable
    from matplotlib.font_manager import FontProperties
    plt.figure(figsize=(15,8))  # width, height in inches
    matplotlib.rcParams.update({'font.size': 8})
    # make a 2x3 grid of subplots where the last (5th) spans 2 cols
    maxweight = max(Counter(pagewidths))
    maxheight = max(Counter(pageheights))
    for (plot, data, bins) in zip([plt.subplot2grid((2,4), (0,0)),
                                   plt.subplot2grid((2,4), (0,1)),
                                   plt.subplot2grid((2,4), (0,2)),
                                   plt.subplot2grid((2,4), (0,3)),
                                   plt.subplot2grid((2,4), (1,0)),
                                   plt.subplot2grid((2,4), (1,1))],
                                  [(even_leftmarg, "Textbox left property (even-hand pages)"),
                                   (odd_leftmarg, "Textbox left property (odd-hand pages)"),
                                   (even_rightmarg, "Textbox right property (even-hand pages)"),
                                   (odd_rightmarg, "Textbox right property (odd-hand pages)"),
                                   (list(topmarg.elements()), "Textbox top property"),
                                   (list(bottommarg.elements()), "Textbox bottom property")],
                                  [maxweight, maxweight, maxweight, maxweight,
                                   maxheight, maxheight]):
        series, label = data
        plot.hist(series, bins=bins, range=(0,bins), color='k')
        plot.set_title(label, fontdict={'fontsize': 7})
        (maxval, maxcnt) = Counter(series).most_common(1)[0]
        log.debug("analyze_metrics: %s: Top val %s (%s times)" % (label, maxval, maxcnt))
        plot.annotate(maxval, xy=(maxval, maxcnt),
                      xytext=(maxval*0.5, maxcnt*0.9),
                      arrowprops=dict(arrowstyle="->"))
    plot = plt.subplot2grid((2,4), (1,2), colspan=2)
    stylecount = Counter()
    for style, length in styles:
        stylecount[(style.family, style.size)] += length
    stylenames = [style[0].replace("TimesNewRomanPS", "Times")+"@"+str(style[1]) for style, count in stylecount.most_common()]
    stylecounts = [count for style, count in stylecount.most_common()]
    plt.yticks(range(len(stylenames)), stylenames, fontproperties=FontProperties(size=8))
    plot.barh(range(len(stylenames)), stylecounts, log=True)
    plot.set_title("Font usage", fontdict={'fontsize': 8})
    util.ensure_dir(filename)
    plt.savefig(filename, dpi=300)
    log.debug("analyze_metrics: Saved plot as %s" % filename)
