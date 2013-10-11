# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# system
from tempfile import mktemp
import random
import re
from six import text_type as str

# 3rdparty
from lxml import etree

# mine
from ferenda import DocumentRepository
from ferenda import util
# from ferenda.legalref import LegalRef, Link

# FIXME: Need to dynamically set this namespace (by inspecting the root?)
# as it varies with MW version
MW_NS = "{http://www.mediawiki.org/xml/export-0.4/}"


class MediaWiki(DocumentRepository):

    """Downloads content from a Mediawiki system and converts it to annotations on other documents.

    For efficient downloads, this docrepo requires that there exists a
    XML dump (created by `dumpBackup.php
    <http://www.mediawiki.org/wiki/Manual:DumpBackup.php>`_) of the
    mediawiki contents that can be fetched over HTTP/HTTPS. Configure
    the location of this dump using the ``mediawikiexport``
    parameter::

        [mediawiki]
        class = ferenda.sources.general.MediaWiki
        mediawikiexport = http://localhost/wiki/allpages-dump.xml

    """

    alias = "mediawiki"
    downloaded_suffix = ".xml"

    def get_default_options(self):
        opts = super(MediaWiki, self).get_default_options()
        # The API endpoint URLs change with MW language
        opts['mediawikiexport'] = 'http://localhost/wiki/Special:Export/%s(basefile)'
        opts['mediawikidump'] = 'http://localhost/wiki/allpages-dump.xml'
        opts['mediawikinamespaces'] = ['Category']
            # process pages in this namespace (as well as pages in the default namespace)
        return opts

    def download(self, basefile=None):
        if basefile:
            return self.download_single(basefile)

        if not self.config.mediawikidump:
            resp = requests.get(self.config.mediawikidump)
            xml = etree.parse(resp.content)

        wikinamespaces = []
        # FIXME: Find out the proper value of MW_NS
        for ns_el in xml.findall("//" + MW_NS + "namespace"):
            wikinamespaces.append(ns_el.text)

        # Get list of currently downloaded pages - if any of those
        # does not appear in the XML dump, remove them afterwards
        basefiles = self.store.list_basefiles_for("parse")
        downloaded_files = [self.store.downloaded_path(x) for x in basefiles]

        for page_el in xml.findall(MW_NS + "page"):
            basefile = page_el.find(MW_NS + "title").text
            if basefile == "Huvudsida":
                continue
            if ":" in basefile and basefile.split(":")[0] in wikinamespaces:
                (namespace, localtitle) = basefile.split(":", 1)
                if namespace not in self.config.mediawikinamespaces:
                    continue
            with self.store.open_downloaded(title, "w"):
                f.write(etree.tostring(page_el, encoding="utf-8"))

            if basefile in basefiles:
                del basefiles[basefiles.index(basefile)]

        for b in basefiles:
            self.log.debug("Removing stale %s" % b)
            util.robust_remove(self.store.downloaded_path(b))

    def download_single(self, basefile):
        # download a single term, for speed
        url = self.config.mediawikiexport % {'basefile': basefile}
        self.download_if_needed(url, basefile)

    re_anchors = re.compile('(<a.*?</a>)', re.DOTALL)
    re_anchor = re.compile('<a[^>]*>(.*)</a>', re.DOTALL)
    re_tags = re.compile('(</?[^>]*>)', re.DOTALL)

    # FIXME: these belong in a subclass of MediaWiki
    re_sfs_uri = re.compile('https?://[^/]*lagen.nu/(\d+):(.*)')
    re_dom_uri = re.compile('https?://[^/]*lagen.nu/dom/(.*)')

    def parse_document_from_soup(self, soup, doc):

        wikitext = soup.find("text")
        html = p.parse(wikitext)

        # the output from wikimarkup is less than ideal...
        html = html.replace("&", "&amp;")
        html = '<div>' + html + '</div>'

        try:
            xhtml = etree.fromstring(html.encode('utf-8'))
        except SyntaxError:
            self.log.warn(
                "%s: wikiparser did not return well-formed markup (working around)" % basefile)
            tmpfilename = mktemp()  # FIXME: security hole
            fp = open(tmpfilename, "w")
            fp.write(html.encode('utf-8'))
            fp.close()
            tidied = util.tidy(html.encode('utf-8')).replace(
                ' xmlns="http://www.w3.org/1999/xhtml"', '').replace('&nbsp;', '&#160;')
            # print "Valid markup:\n%s" % tidied
            xhtml = etree.fromstring(tidied.encode('utf-8')).find("body/div")

        # FIXME: Again, belongs to a subclass. And we'll need to figure out an
        # extensible mechanism.
        p = LegalRef(LegalRef.LAGRUM, LegalRef.KORTLAGRUM,
                     LegalRef.FORARBETEN, LegalRef.RATTSFALL)
        # find out the URI that this wikitext describes
        if doc.basefile.startswith("SFS/"):
            sfs_basefile = basefile.split("/", 1)[1]
            if sfs_basefile.count("/") > 1:
                sfs_basefile = sfs_basefile.rsplit("/", 1)[0]
            uri = SFS().canonical_uri(sfs_basefile)
            rdftype = None
        else:
            # FIXME: Arrrgh!
            uri = "http://lagen.nu/concept/" + doc.basefile.replace(" ", "_")
            rdftype = self.ns['skos'].Concept

        # FIXME: Change this mess to some code that constructs a
        # ferenda.elements tree and sets doc.body to it
        root = etree.Element("html")
        root.set("xmlns", 'http://www.w3.org/2002/06/xhtml2/')
        root.set("xmlns:dct", util.ns['dct'])
        root.set("xmlns:rdf", util.ns['rdf'])
        root.set("xmlns:rdfs", util.ns['rdfs'])
        root.set("xmlns:skos", util.ns['skos'])
        root.set("xml:lang", "sv")
        head = etree.SubElement(root, "head")
        title = etree.SubElement(head, "title")
        title.text = doc.basefile
        body = etree.SubElement(root, "body")
        body.set("about", uri)
        if rdftype:
            body.set("typeof", "skos:Concept")
            heading = etree.SubElement(body, "h")
            heading.set("property", "rdfs:label")
            heading.text = doc.basefile

        main = etree.SubElement(body, "div")
        main.set("property", "dct:description")
        main.set("datatype", "rdf:XMLLiteral")
        current = main
        currenturi = uri

        for child in xhtml:
            if not rdftype and child.tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                nodes = p.parse(child.text, uri)
                try:
                    suburi = nodes[0].uri
                    currenturi = suburi
                    self.log.debug("    Sub-URI: %s" % suburi)
                    h = etree.SubElement(body, child.tag)
                    h.text = child.text
                    current = etree.SubElement(body, "div")
                    current.set("about", suburi)
                    current.set("property", "dct:description")
                    current.set("datatype", "rdf:XMLLiteral")
                except AttributeError:
                    self.log.warning(
                        '%s är uppmärkt som en rubrik, men verkar inte vara en lagrumshänvisning' % child.text)
            else:
                serialized = etree.tostring(child, 'utf-8').decode('utf-8')
                separator = ""
                while separator in serialized:
                    separator = "".join(
                        random.sample("ABCDEFGHIJKLMNOPQRSTUVXYZ", 6))

                markers = {}
                res = ""
                # replace all whole <a> elements with markers, then
                # replace all other tags with markers
                for (regex, start) in ((self.re_anchors, '<a'),
                                      (self.re_tags, '<')):
                    for match in re.split(regex, serialized):
                        if match.startswith(start):
                            marker = "{%s-%d}" % (separator, len(markers))
                            markers[marker] = match
                            res += marker
                        else:
                            res += match
                    serialized = res
                    res = ""

                # Use LegalRef to parse references, then rebuild a
                # unicode string.
                parts = p.parse(serialized, currenturi)
                for part in parts:
                    if isinstance(part, Link):
                        res += '<a class="lr" href="%s">%s</a>' % (
                            part.uri, part)
                    else:  # just a text fragment
                        res += part

                # restore the replaced markers
                for marker, replacement in list(markers.items()):
                    # print "%s: '%s'" % (marker,util.normalize_space(replacement))
                    # normalize URIs, and remove 'empty' links
                    if 'href="https://lagen.nu/"' in replacement:
                        replacement = self.re_anchor.sub('\\1', replacement)
                    elif self.re_sfs_uri.search(replacement):
                        replacement = self.re_sfs_uri.sub(
                            'http://rinfo.lagrummet.se/publ/sfs/\\1:\\2', replacement)
                    elif self.re_dom_uri.search(replacement):
                        replacement = self.re_dom_uri.sub(
                            'http://rinfo.lagrummet.se/publ/rattsfall/\\1', replacement)
                    # print "%s: '%s'" % (marker,util.normalize_space(replacement))
                    res = res.replace(marker, replacement)

                current.append(etree.fromstring(res.encode('utf-8')))

        util.indent_et(root)
        # print etree.tostring(root,'utf-8').decode('utf-8')
        res = etree.tostring(root, encoding='utf-8')
        return res

    # differ from the default relate_triples in that it uses a different
    # context for every basefile and clears this beforehand.
    # Note that a basefile can contain statements
    # about multiple and changing subjects, so it's not trivial to erase all
    # statements that stem from a basefile w/o a dedicated context.
    def relate_triples(self, basefile):
        context = self.dataset_uri + "#" + basefile.replace(" ", "_")
        ts = self._get_triplestore()
        data = open(self.store.distilled_path(basefile)).read()
        ts.clear(context=context)
        ts.add_serialized(data, format="xml", context=context)

    # FIXME: Copy the few testcases from svn test/Wiki,
    # maybe translate the answers from XHT2 to XHTML1.1,
    # move the code into a RepoTester class
    testparams = {'Parse': {'dir': 'test/Wiki',
                            'testext': '.txt',
                            'testencoding': 'latin-1',
                            'answerext': '.xht2',
                            'answerencoding': 'utf-8'},
                  }

    def TestParse(self, data, verbose=None, quiet=None):
        # FIXME: Set this from FilebasedTester
        if verbose is None:
            verbose = False
        if quiet is None:
            # quiet=True
            pass

        p = WikiParser()
        p.verbose = verbose
        res = p.parse_wikitext("Test", data)
        if isinstance(res, str):
            return res
        else:
            return res.decode('utf-8')


# class LinkedWikimarkup(wikimarkup.Parser):
class LinkedWikimarkup(object):
    def __init__(self, show_toc=True):
        super(wikimarkup.Parser, self).__init__()
        self.show_toc = show_toc

    def parse(self, text):
        # print "Running subclassed parser!"
        utf8 = isinstance(text, str)
        text = wikimarkup.to_unicode(text)
        if text[-1:] != '\n':
            text = text + '\n'
            taggedNewline = True
        else:
            taggedNewline = False

        text = self.strip(text)
        text = self.removeHtmlTags(text)
        text = self.doTableStuff(text)
        text = self.parseHorizontalRule(text)
        text = self.checkTOC(text)
        text = self.parseHeaders(text)
        text = self.parseAllQuotes(text)
        text = self.replaceExternalLinks(text)
        if not self.show_toc and text.find("<!--MWTOC-->") == -1:
            self.show_toc = False
        text = self.formatHeadings(text, True)
        text = self.unstrip(text)
        text = self.fixtags(text)
        text = self.replaceRedirect(text)
        text = self.doBlockLevels(text, True)
        text = self.unstripNoWiki(text)
        text = self.replaceImageLinks(text)
        text = self.replaceCategories(text)
        text = self.replaceAuthorLinks(text)
        text = self.replaceWikiLinks(text)
        text = self.removeTemplates(text)

        text = text.split('\n')
        text = '\n'.join(text)
        if taggedNewline and text[-1:] == '\n':
            text = text[:-1]
        if utf8:
            return text.encode("utf-8")
        return text

    re_labeled_wiki_link = re.compile(r'\[\[([^\]]*?)\|(.*?)\]\](\w*)')
                                      # is the trailing group really needed?
    re_wiki_link = re.compile(r'\[\[([^\]]*?)\]\](\w*)')
    re_img_uri = re.compile('(https?://[\S]+\.(png|jpg|gif))')
    re_template = re.compile(r'{{[^}]*}}')
    re_category_wiki_link = re.compile(r'\[\[Kategori:([^\]]*?)\]\]')
    re_inline_category_wiki_link = re.compile(
        r'\[\[:Kategori:([^\]]*?)\|(.*?)\]\]')
    re_image_wiki_link = re.compile(r'\[\[Fil:([^\]]*?)\s*\]\]')
    re_author_wiki_link = re.compile(r'\[\[(Användare:[^\]]+?)\|(.*?)\]\]')

    def capitalizedLink(self, m):
        if m.group(1).startswith('SFS/'):
            uri = 'http://rinfo.lagrummet.se/publ/%s' % m.group(1).lower()
        else:
            uri = 'http://lagen.nu/concept/%s' % util.ucfirst(
                m.group(1)).replace(' ', '_')

        if len(m.groups()) == 3:
            # lwl = "Labeled WikiLink"
            return '<a class="lwl" href="%s">%s%s</a>' % (uri, m.group(2), m.group(3))
        else:
            return '<a class="wl" href="%s">%s%s</a>' % (uri, m.group(1), m.group(2))

    def categoryLink(self, m):
        uri = 'http://lagen.nu/concept/%s' % util.ucfirst(
            m.group(1)).replace(' ', '_')

        if len(m.groups()) == 2:
            # lcwl = "Labeled Category WikiLink"
            return '<a class="lcwl" href="%s">%s</a>' % (uri, m.group(2))
        else:
            # cwl = "Category wikilink"
            return '<a class="cwl" href="%s">%s</a>' % (uri, m.group(1))

    def hiddenLink(self, m):
        uri = 'http://lagen.nu/concept/%s' % util.ucfirst(
            m.group(1)).replace(' ', '_')
        return '<a class="hcwl" rel="dct:subject" href="%s"/>' % uri

    def imageLink(self, m):
        uri = 'http://wiki.lagen.nu/images/%s' % m.group(1).strip()
        return '<img class="iwl" src="%s" />' % uri

    def authorLink(self, m):
        uri = 'http://wiki.lagen.nu/index.php/%s' % util.ucfirst(
            m.group(1)).replace(' ', '_')
        return '<a class="awl" href="%s">%s</a>' % (uri, m.group(2))

    def replaceWikiLinks(self, text):
        # print "replacing wiki links: %s" % text[:30]
        text = self.re_labeled_wiki_link.sub(self.capitalizedLink, text)
        text = self.re_wiki_link.sub(self.capitalizedLink, text)
        return text

    def replaceImageLinks(self, text):
        # emulates the parser when using$ wgAllowExternalImages
        text = self.re_img_uri.sub('<img src="\\1"/>', text)
        # handle links like [[Fil:SOU_2003_99_s117.png]]
        text = self.re_image_wiki_link.sub(self.imageLink, text)
        return text

    def replaceAuthorLinks(self, text):
        # links to author descriptions should point directly to the wiki
        return self.re_author_wiki_link.sub(self.authorLink, text)

    def removeTemplates(self, text):
        # removes all usage of templates ("{{DISPLAYTITLE:Avtalslagen}}" etc)
        return self.re_template.sub('', text)

    def replaceCategories(self, text):
        # inline links ("Inom [[:Kategori:Allmän avtalsrätt|Allmän avtalsrätt]] studerar man...")
        text = self.re_inline_category_wiki_link.sub(self.categoryLink, text)
        # Normal category links - replace these with hidden RDFa typed links
        text = self.re_category_wiki_link.sub(self.hiddenLink, text)
        return text

    re_redirect = re.compile("^#REDIRECT ")

    def replaceRedirect(self, text):
        return self.re_redirect.sub("Se ", text)
