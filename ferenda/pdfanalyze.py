# -*- coding: utf-8 -*-
from __future__ import unicode_literals
"""A bunch of helper functions for analyzing pdf files."""
import os
import logging
import json
from collections import Counter

from six import text_type as str

from .pdfreader import Page
from ferenda import util

class PDFAnalyzer(object):

    twopage = True
    """Whether or not the document is expected to have different margins
    depending on whether it's a even or odd page.

    """

    frontmatter = 1
    """The amount of frontmatter pages which might have differing
    typographic styles compared with the rest of the document. Affects
    style analysis, particularly how the title style is determined.

    """

    style_significance_threshold = 0.005
    """"The amount of use (as compared to the rest of the document that a
    style must have to be considered significant.

    """

    header_significance_threshold = 0.002
    """The maximum amount (expressed as part of the entire text amount) of
    text that can occur on the top of the page for it to be considered
    part of the header.

    """

    footer_significance_threshold = 0.002
    """The maximum amount (expressed as part of the entire text amount) of
    text that can occur on the bottom of the page for it to be
    considered part of the footer.

    """

    frontmatter = 1
    """The amount of pages to be considered frontmatter, which might have
    different typography, special title font etc."""
    
    def __init__(self, pdf):
        """Create a analyzer for the given pdf file.

        :param pdf: The pdf file to analyze.
        :type  pdf: ferenda.PDFReader

        """
        # FIXME: in time, we'd like to make it possible to specify
        # multiple pdf files (either because a single logical document
        # is split into several files, or because the user wants to
        # analyze a bunch of docs in one go).
        self.pdf = pdf

    def documents(self):
        """Attempts to distinguish different logical document (eg parts with
        differing pagesizes/margins/styles etc) within this PDF.

        You should override this method if you want to provide your
        own document segmentation logic.

        :returns: Tuples (startpage, pagecount) for the different identified documents
        :rtype: list

        """
        return [(0, len(self.pdf))]


    def metrics(self, metricspath=None, plotpath=None,
                startpage=0, pagecount=None):
        """Calculate and return the metrics for this analyzer.

        metrics is a set of named properties in the form of a
        dict. The keys of the dict can represent margins or other
        measurements of the document (left/right margins,
        header/footer etc) or font styles used in the document (eg.
        default, title, h1 -- h3). Style values are in turn dicts
        themselves, with the keys 'family' and 'size'.

        :param metricspath: The path of a JSON file used as cache for the 
                             calculated metrics
        :type  metricspath: str
        :param plotpath: The path to write a PNG file with histograms for 
                         different values (for debugging). 
        :type plotpath: str
        :param startpage: starting page for the analysis
        :type startpage: int
        :param startpage: number of pages to analyze (default: all available)
        :type startpage: int
        :returns: calculated metrics
        :rtype: dict

        The default implementation will try to find out values for the
        following metrics:

        ================== ===================================================
        key                description
        ================== ===================================================
        leftmargin         position of left margin (for odd pages if 
                           twopage = True)
        rightmargin        position of right margin (for odd pages if
                           twopage = True)
        leftmargin_even    position of left margin for even pages

        rightmargin_even   position of right margin for right pages

        header             position of header zone
        
        footer             position of footer zone

        default            style used for default text

        title              style used for main document title (on front page)

        h1                 style used for level 1 headings 

        h2                 style used for level 2 headings 

        h3                 style used for level 3 headings
        ================== ===================================================

        Subclasses might add (or remove) from the above.

        """


        if (metricspath and
            util.outfile_is_newer([self.pdf.filename], metricspath)):
            with open(metricspath) as fp:
                return json.load(fp)

        if pagecount is None:
            pagecount = len(self.pdf) - startpage
            
        hcounters = self.count_horizontal_margins(startpage, pagecount)
        vcounters = self.count_vertical_margins(startpage, pagecount)
        stylecounters = self.count_styles(startpage, pagecount)

        hmetrics = self.analyze_horizontal_margins(hcounters)
        vmetrics = self.analyze_vertical_margins(vcounters)
        stylemetrics = self.analyze_styles(stylecounters)

        allcounters = dict(chain(hcounters.items(), vcounters.items(), stylecounters.items()))
        allmetrics = dict(chain(hmetrics.items(), vmetrics.items(), stylemetrics.items()))

        if plotpath:
            self.plot(plotpath, allcounters, allmetrics)
        if metricspath:
            with open(metricspath, "w") as fp:
                json.dump(fp, allmetrics)
        return allmetrics

    def textboxes(self, startpage, pagecount):
        for page in self.pdf[startpage:startpage+pagecount]:
            for textbox in page:
                yield page.number, textbox
    
    def count_horizontal_margins(self, startpage, pagecount):
        counters = self.setup_horizontal_counters()
        for pagenumber, textbox in self.textboxes(startpage, pagecount):
            self.count_horizontal_textbox(pagenumber, textbox, counters)
        for page in self.pdf[startpage:startpage+pagecount]:
            counters['pagewidth'] = page.width
        return counters

    def setup_horizontal_counters(self):
        counters = {'leftmargin': Counter(),
                    'rightmargin': Counter(),
                    'pagewidth': Counter()}
        if self.twopage:
            counters['leftmargin_even'] = Counter()
            counters['rightmargin_even'] = Counter()
        return counters

    def count_horizontal_textbox(self, pagenumber, textbox, counters):
        if self.twopage and pagenumber % 2 == 0:
            counters['leftmargin_even'][textbox.left] += 1
            counters['rightmargin_even'][textbox.right] += 1
        else:
            counters['leftmargin'][textbox.left] += 1
            counters['rightmargin'][textbox.right] += 1

    def count_vertical_margins(self, startpage, pagecount):
        counters = self.setup_vertical_counters()
        for pagenumber, textbox in self.textboxes(startpage, pagecount):
            self.count_vertical_textbox(pagenumber, textbox, counters)
        for page in self.pdf[startpage:startpage+pagecount]:
            counters['pageheight'][page.height] += 1
        return counters

    def setup_vertical_counters(self):
        counters = {'topmargin': Counter(),
                    'bottommargin': Counter(),
                    'pageheight': Counter()}
        return counters

    def count_vertical_textbox(self, pagenumber, textbox, counters):
        text = str(textbox).strip()
        counters['topmargin'][textbox.top] += len(text)
        counters['bottommargin'][textbox.bottom] += len(text)
        
    def count_styles(self, startpage, pagecount):
        counters = {'frontmatter_styles': Counter(),
                    'rest_styles': Counter()}
        for pagenumber, textbox in self.textboxes(startpage, pagecount):
            self.count_styles_textbox(pagenumber, textbox, counters)
        return counters

    def count_styles_textbox(self, pagenumber, textbox, counters):
        text = str(textbox).strip()
        fonttuple = (textbox.font.family, textbox.font.size)
        if pagenumber <= self.frontmatter:
            cid = "frontmatter_styles"
        else:
            cid = "rest_styles"
        counters[cid][fonttuple] += len(text)

    # this is suitable for overriding, eg if you know that no pagenumbers occur in the footer
    def analyze_horizontal_margins(self, vcounters):
        # now find probable header and footer zones. default algorithm:
        # max 0.2 % of text content can be in the header/footer zone. (on
        # a page of 2000 chars, only 4 can be in the footer)
        maxcount = self.header_significance_threshold * sum(vcounters['topmargin'].values())
        charcount = 0
        for i in range(max(vcounters['pageheight'])):
            charcount += vcounters['topmargin'].get(i, 0)
            if charcount > maxcount:
                header = i - 1
                break
        charcount = 0
        maxcount = self.footer_significance_threshold * sum(vcounters['topmargin'].values())
        for i in range(max(vcounters['pageheight'])-1, -1, -1):
            charcount += vcounters['bottommargin'].get(i, 0)
            if charcount > maxcount:
                footer = i + 1
                break
        return {'header': header,
                'footer': footer}

    # subclasses can (should) add metrics like parindent and secondcolumn
    def analyze_vertical_margins(self, vcounters):
        # find the probable left margin = the place where most textboxes start
        vmargins = {'leftmargin': vcounters['leftmargin'].most_common(1)[0][0],
                    'rightmargin': vcounters['leftmargin'].most_common(1)[0][0]}
        if self.twopage:
            vmargins['leftmargin_even'] = vcounters['leftmargin_even'].most_common(1)[0][0]
            vmargins['rightmargin_even'] = vcounters['leftmargin_even'].most_common(1)[0][0]

        assert leftmargin < pagewidth / 2, "leftmargin shouldn't be on the right hand side of the page"
        if self.twopage:
            assert leftmargin_even < pagewidth / 2, "leftmargin shouldn't be on the right hand side of the page"

        vmargins['pagewidth'] = max(vcounters['pagewidth'])

        return vmargins

    def fontsize_key(self, fonttuple): 
        family, size = fonttuple
        if "Bold" in family:
            weight = 2
        elif "Italic" in family:
            weight = 1
        else:
            weight = 0
        return (size, weight)


    def fontdict(self, fonttuple):
        return {'family': fonttuple[0],
                'size': fonttuple[1]}


    def analyze_styles(self, frontmatter_styles, rest_styles):
        all_styles = frontmatter_styles + rest_styles

        # default style: the one that's most common
        ds = all_styles.most_common(1)[0][0]
        styledefs['default'] = self.fontdict(ds)

        # title style: the largest style that exists on the frontpage style
        # objects are (should be) sortable. for style objects at the same
        # size, Bold > Italic > Regular
        ts = sorted(frontmatter_styles.keys(), key=self.fontsize_key, reverse=True)[0]
        styledefs['title'] = fontdict(ts)

        # h1 - h3: Take all styles larger than or equal to default, with
        # significant use (each > 0.5 % of all chars from page 2 onwards,
        # as the front page often uses nontypical styles), then order
        # styles by font size.
        significantuse = sum(restcount.values()) * 0.005
        sortedstyles = sorted(restcount, key=self.fontsize_key, reverse=True)
        largestyles = [x for x in sortedstyles if
                       (fontsize_key(x) > self.fontsize_key(ds) and
                        stylecount[x] > significantuse)]

        for style in ('h1', 'h2', 'h3'):
            if largestyles: # any left?
                styledefs[style] = self.fontdict(largestyles.pop(0))
        return styledefs


    def drawboxes(self, outfilename, gluefunc=None, startpage=0, pagecount=None, counters=None, metrics=None):
        # for each page in pagespan:
        # load page from original pdf file
        # run gluefunc on page.textboxes
        # for each textbox draw a square 
        # add sequence number for textbox in lower right corner
        # if textbox style matches any in metrics, add the style name in upper lft corner
        # for each metrics that's an integer
        #    if metric in ('header', 'footer') draw horizontal line
        #    otherwise draw vertical line
        #    label the line
        # for each area in header, footer, leftmarg[_even], rightmarg[_even]:
        #    select proper counter to draw in each area:
        #      (headercounter->left gutter, 
        #       footercounter->right gutter, 
        #       leftmargcounter->header,
        #       rightmargcounter->footer)
        #   find the most frequent value in the selected counter, normalize agaist the space in the area
        #   for each value of the counter draw single-width line at correct posiion
        pass

    def plot(filename, counters, metrics):
        # make subplot grid based on the number of counters
        # margin_plots = (plt.grid2subplot(...),
        #                 ...)
        # margin_counters = all_counters - (frontmatter_styles, rest_styles)
        # plot_margins(margin_plots, margin_counters, metrics)
        # style_plot = plt.grid2subplot(..., colspan=2)
        # plot_styles(frontmatter_styles, rest_styles, metrics)
        # save plt to filename
        pass

    def plot_margins(subplots, margin_counters, metrics):
        # one plot per counter (4 or 6)
        # map each metric (6+ ) to correct plot and point it out
        pass

    def plot_styles(subplot, style_counters, metrics):
	# do a additive vhist. label those styles identified in metrics
        pass

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
