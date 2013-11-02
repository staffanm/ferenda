# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import os
import re
import logging
import codecs
from tempfile import mktemp
from xml.sax.saxutils import escape as xml_escape

from rdflib import Graph, URIRef, Literal
from bs4 import BeautifulSoup
import requests
import six

from ferenda import TextReader
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda import util
from . import SwedishLegalSource


class MyndFskr(SwedishLegalSource):

    """A abstract base class for fetching and parsing regulations from
various swedish government agencies. These PDF documents often have
a similar structure both graphically and linguistically, enabling us
to parse them in a generalized way. (Downloading them often requires
special-case code, though.)"""
    source_encoding = "utf-8"
    downloaded_suffix = ".pdf"
    alias = 'myndfskr'

    def download(self, basefile=None):
        """Simple default implementation that downloads all PDF files
        from self.start_url that look like regulation document
        numbers."""
        resp = requests.get(self.start_url)
        # regex to search the link url, text or title for something
        # looking like a FS number
        re_fsnr = re.compile('(\d{4})[:/_-](\d+)(|\.\w+)$')
        tree = lxml.html.document_fromstring(resp.text)
        tree.make_links_absolute(url, resolve_base_href=True)
        for element, attribute, link, pos in tree.iterlinks():
            if link[-4:].lower() != ".pdf":
                continue
            done = False
            # print "Examining %s"  % link
            attrs = dict(link.attrs)
            flds = [link.url, link.text]
            if 'title' in attrs:
                flds.append(attrs['title'])
            for fld in flds:
                if re_fsnr.search(fld) and not done:
                    m = re_fsnr.search(fld)
                    # Make sure we end up with "2011:4" rather than
                    # "2011:04"
                    basefile = "%s:%s" % (m.group(1), int(m.group(2)))
                    self.download_single(basefile, usecache, link.absolute_url)
                    done = True

    def canonical_uri(self, basefile):
        # The canonical URI for these documents cannot always be
        # computed from the basefile. Find the primary subject of the
        # distilled RDF graph instead.
        if not os.path.exists(self.store.distilled_path(basefile)):
            return None

        g = Graph()
        g.parse(self.store.distilled_path(basefile))
        subjects = list(g.subject_objects(self.ns['rdf']['type']))

        if subjects:
            return str(subjects[0][0])
        else:
            self.log.warning(
                "No canonical uri in %s" % (self.distilled_path(basefile)))
            # fall back
            return super(MyndFskr, self).canonical_uri(basefile)

    def textreader_from_basefile(self, basefile, encoding):
        infile = self.store.downloaded_path(basefile)
        tmpfile = self.store.path(basefile, 'intermediate', '.pdf')
        outfile = self.store.path(basefile, 'intermediate', '.txt')
        util.copy_if_different(infile, tmpfile)
        util.runcmd("pdftotext %s" % tmpfile, require_success=True)
        util.robust_remove(tmpfile)

        return TextReader(outfile, encoding=encoding, linesep=TextReader.UNIX)

    def rpubl_uri_transform(self, s):
        # Inspired by
        # http://code.activestate.com/recipes/81330-single-pass-multiple-replace/
        table = {'å': 'aa',
                 'ä': 'ae',
                 'ö': 'oe'}
        r = re.compile("|".join(list(table.keys())))
        # return r.sub(lambda f: table[f.string[f.start():f.end()]], s.lower())
        return r.sub(lambda m: table[m.group(0)], s.lower())

    def download_resource_lists(self, resource_url, graph_path):
        hdr = self._addheaders()
        hdr['Accept'] = 'application/rdf+xml'
        resp = requests.get(resource_url, headers=hdr)
        g = Graph()
        g.parse(data=resp.text, format="xml")
        for subj in g.subjects(self.ns['rdf'].type,
                               self.ns['rpubl'].Forfattningssamling):
            resp = requests.get(str(subj), headers=hdr)
            resp.encoding = "utf-8"
            g.parse(data=resp.text, format="xml")
        with open(graph_path, "wb") as fp:
            data = g.serialize(format="xml")
            fp.write(data)

    def parse_from_textreader(self, reader, basefile):
        tracelog = logging.getLogger("%s.tracelog" % self.alias)

        doc = self.make_document(basefile)
        g = doc.meta

        # 1.2: Load known entities and their URIs (we have to add some
        # that are not yet in the official resource lists
        resource_list_file = self.store.path('resourcelist', 'intermediate', '.rdf')
        if not os.path.exists(resource_list_file):
            self.download_resource_lists("http://service.lagrummet.se/var/common",
                                         resource_list_file)
        resources = Graph()
        resources.parse(resource_list_file, format="xml")

        # 1.3: Define regexps for the data we search for.
        fwdtests = {'dct:issn': ['^ISSN (\d+\-\d+)$'],
                    'dct:title': ['((?:Föreskrifter|[\w ]+s (?:föreskrifter|allmänna råd)).*?)\n\n'],
                    'dct:identifier': ['^([A-ZÅÄÖ-]+FS\s\s?\d{4}:\d+)$'],
                    'rpubl:utkomFranTryck': ['Utkom från\strycket\s+den\s(\d+ \w+ \d{4})'],
                    'rpubl:omtryckAv': ['^(Omtryck)$'],
                    'rpubl:genomforDirektiv': ['Celex (3\d{2,4}\w\d{4})'],
                    'rpubl:beslutsdatum': ['(?:har beslutats|beslutade|beslutat) den (\d+ \w+ \d{4})'],
                    'rpubl:beslutadAv': ['\n([A-ZÅÄÖ][\w ]+?)\d? (?:meddelar|lämnar|föreskriver)',
                                         '\s(?:meddelar|föreskriver) ([A-ZÅÄÖ][\w ]+?)\d?\s'],
                    'rpubl:bemyndigande': [' ?(?:meddelar|föreskriver|Föreskrifterna meddelas|Föreskrifterna upphävs)\d?,? (?:följande |)med stöd av\s(.*?) ?(?:att|efter\ssamråd|dels|följande|i fråga om|och lämnar allmänna råd|och beslutar följande allmänna råd|\.\n)',
                                           '^Med stöd av (.*)\s(?:meddelar|föreskriver)']
                    }

        # 2: Find metadata properties

        # 2.1 Find some of the properties on the first page (or the
        # 2nd, or 3rd... continue past TOC pages, cover pages etc
        # until the "real" first page is found) NB: FFFS 2007:1 has
        # ten (10) TOC pages!
        pagecnt = 0
        for page in reader.getiterator(reader.readpage):
            # replace single newlines with spaces, but keep double
            # newlines
            # page = "\n\n".join([util.normalize_space(x) for x in page.split("\n\n")])
            pagecnt += 1
            props = {}
            for (prop, tests) in list(fwdtests.items()):
                if prop in props:
                    continue
                for test in tests:
                    m = re.search(
                        test, page, re.MULTILINE | re.DOTALL | re.UNICODE)
                    if m:
                        props[prop] = util.normalize_space(m.group(1))
            # Single required propery. If we find this, we're done
            if 'rpubl:beslutsdatum' in props:
                break
            self.log.warning("%s: Couldn't find required props on page %s" %
                             (basefile, pagecnt))

        # 2.2 Find some of the properties on the last 'real' page (not
        # counting appendicies)
        reader.seek(0)
        pagesrev = reversed(list(reader.getiterator(reader.readpage)))
        # The language used to expres these two properties differ
        # quite a lot, more than what is reasonable to express in a
        # single regex. We therefore define a set of possible
        # expressions and try them in turn.
        revtests = {'rpubl:ikrafttradandedatum':
                    ['(?:Denna författning|Dessa föreskrifter|Dessa allmänna råd|Dessa föreskrifter och allmänna råd)\d* träder i ?kraft den (\d+ \w+ \d{4})',
                     'Dessa föreskrifter träder i kraft, (?:.*), i övrigt den (\d+ \w+ \d{4})',
                     'ska(?:ll|)\supphöra att gälla (?:den |)(\d+ \w+ \d{4}|denna dag|vid utgången av \w+ \d{4})',
                     'träder i kraft den dag då författningen enligt uppgift på den (utkom från trycket)'],
                    'rpubl:upphaver':
                    ['träder i kraft den (?:\d+ \w+ \d{4}), då(.*)ska upphöra att gälla',
                     'ska(?:ll|)\supphöra att gälla vid utgången av \w+ \d{4}, nämligen(.*?)\n\n',
                     'att (.*) skall upphöra att gälla (denna dag|vid utgången av \w+ \d{4})']
                    }

        cnt = 0
        for page in pagesrev:
            cnt += 1
            # Normalize the whitespace in each paragraph so that a
            # linebreak in the middle of the natural language
            # expression doesn't break our regexes.
            page = "\n\n".join(
                [util.normalize_space(x) for x in page.split("\n\n")])

            for (prop, tests) in list(revtests.items()):
                if prop in props:
                    continue
                for test in tests:
                    # Not re.DOTALL -- we've normalized whitespace and
                    # don't want to match across paragraphs
                    m = re.search(test, page, re.MULTILINE | re.UNICODE)
                    if m:
                        props[prop] = util.normalize_space(m.group(1))
                        # print u"%s: '%s' resulted in match '%s' at page %s from end" %
                        # (prop,test,props[prop], cnt)

            # Single required propery. If we find this, we're done
            if 'rpubl:ikrafttradandedatum' in props:
                break

        # 3: Clean up data - converting strings to Literals or
        # URIRefs, find legal references, etc
        if 'dct:identifier' in props:
            (publication, year, ordinal) = re.split('[ :]',
                                                    props['dct:identifier'])
            # FIXME: Read resources graph instead
            fs = resources.value(predicate=self.ns['skos'].altLabel,
                                 object=Literal(publication, lang='sv'))
            props['rpubl:forfattningssamling'] = fs
            publ = resources.value(subject=fs,
                                   predicate=self.ns['dct'].publisher)
            props['dct:publisher'] = publ

            props['rpubl:arsutgava'] = Literal(
                year)  # conversion to int, date not needed
            props['rpubl:lopnummer'] = Literal(ordinal)
            props['dct:identifier'] = Literal(props['dct:identifier'])

            # Now we can mint the uri (should be done through LegalURI)
            uri = ("http://rinfo.lagrummet.se/publ/%s/%s:%s" %
                   (props['rpubl:forfattningssamling'].split('/')[-1],
                    props['rpubl:arsutgava'],
                    props['rpubl:lopnummer']))
            self.log.debug("URI: %s" % uri)
        else:
            self.log.error(
                "Couldn't find dct:identifier, cannot create URI, giving up")
            return None

        tracelog.info("Cleaning rpubl:beslutadAv")
        if 'rpubl:beslutadAv' in props:
            agency = resources.value(predicate=self.ns['foaf'].name,
                                     object=Literal(props['rpubl:beslutadAv'],
                                                    lang="sv"))
            if agency:
                props['rpubl:beslutadAv'] = agency
            else:
                self.log.warning(
                    "Cannot find URI for rpubl:beslutadAv value %r" % props['rpubl:beslutadAv'])
                del props['rpubl:beslutadAv']

        tracelog.info("Cleaning dct:issn")
        if 'dct:issn' in props:
            props['dct:issn'] = Literal(props['dct:issn'])

        tracelog.info("Cleaning dct:title")

        # common false positive
        if 'dct:title' in props and 'denna f\xf6rfattning har beslutats den' in props['dct:title']:
            del props['dct:title']

        if 'dct:title' in props:
            tracelog.info("Inspecting dct:title %r" % props['dct:title'])
            # sometimes the title isn't separated with two newlines from the rest of the text
            if "\nbeslutade den " in props['dct:title']:
                props['dct:title'] = props[
                    'dct:title'].split("\nbeslutade den ")[0]
            props['dct:title'] = Literal(
                util.normalize_space(props['dct:title']), lang="sv")

            if re.search('^(Föreskrifter|[\w ]+s föreskrifter) om ändring i ', props['dct:title'], re.UNICODE):
                tracelog.info("Finding rpubl:andrar in dct:title")
                orig = re.search(
                    '([A-ZÅÄÖ-]+FS \d{4}:\d+)', props['dct:title']).group(0)
                (publication, year, ordinal) = re.split('[ :]', orig)
                origuri = "http://rinfo.lagrummet.se/publ/%s/%s:%s" % (self.rpubl_uri_transform(publication),
                                                                       year, ordinal)
                props['rpubl:andrar'] = URIRef(origuri)
                if 'rpubl:omtryckAv' in props:
                    props['rpubl:omtryckAv'] = URIRef(origuri)
            if (re.search('^(Föreskrifter|[\w ]+s föreskrifter) om upphävande av', props['dct:title'], re.UNICODE)
                    and not 'rpubl:upphaver' in props):
                tracelog.info("Finding rpubl:upphaver in dct:title")
                props['rpubl:upphaver'] = six.text_type(
                    props['dct:title'])  # cleaned below

        tracelog.info("Cleaning date properties")
        for prop in ('rpubl:utkomFranTryck', 'rpubl:beslutsdatum', 'rpubl:ikrafttradandedatum'):
            if prop in props:
                if (props[prop] == 'denna dag' and
                        prop == 'rpubl:ikrafttradandedatum'):
                    props[prop] = props['rpubl:beslutsdatum']
                elif (props[prop] == 'utkom från trycket' and
                      prop == 'rpubl:ikrafttradandedatum'):
                    props[prop] = props['rpubl:utkomFranTryck']
                else:
                    props[prop] = Literal(
                        self.parse_swedish_date(props[prop].lower()))

        tracelog.info("Cleaning rpubl:genomforDirektiv")
        if 'rpubl:genomforDirektiv' in props:
            props['rpubl:genomforDirektiv'] = URIRef("http://rinfo.lagrummet.se/ext/eur-lex/%s" %
                                                     props['rpubl:genomforDirektiv'])

        tracelog.info("Cleaning rpubl:bemyndigande")
        has_bemyndiganden = False

        if 'rpubl:bemyndigande' in props:
            # SimpleParse can't handle unicode endash sign, transform
            # into regular ascii hyphen
            props['rpubl:bemyndigande'] = props[
                'rpubl:bemyndigande'].replace('\u2013', '-')
            parser = LegalRef(LegalRef.LAGRUM)
            result = parser.parse(props['rpubl:bemyndigande'])
            bemyndigande_uris = [x.uri for x in result if hasattr(x, 'uri')]

            # some of these uris need to be filtered away due to
            # over-matching by parser.parse
            filtered_bemyndigande_uris = []
            for bem_uri in bemyndigande_uris:
                keep = True
                for compare in bemyndigande_uris:
                    if (len(compare) > len(bem_uri) and
                            compare.startswith(bem_uri)):
                        keep = False
                if keep:
                    filtered_bemyndigande_uris.append(bem_uri)

            for bem_uri in filtered_bemyndigande_uris:
                g.add((URIRef(
                    uri), self.ns['rpubl']['bemyndigande'], URIRef(bem_uri)))
                has_bemyndiganden = True
            del props['rpubl:bemyndigande']

        tracelog.info("Cleaning rpubl:upphaver")
        if 'rpubl:upphaver' in props:
            for upph in re.findall('([A-ZÅÄÖ-]+FS \d{4}:\d+)', util.normalize_space(props['rpubl:upphaver'])):
                (publication, year, ordinal) = re.split('[ :]', upph)
                upphuri = "http://rinfo.lagrummet.se/publ/%s/%s:%s" % (publication.lower(),
                                                                       year, ordinal)
                g.add((URIRef(
                    uri), self.ns['rpubl']['upphaver'], URIRef(upphuri)))
            del props['rpubl:upphaver']

        tracelog.info("Deciding rdf:type")
        if ('dct:title' in props and
            "allmänna råd" in props['dct:title'] and
                not "föreskrifter" in props['dct:title']):
            props['rdf:type'] = self.ns['rpubl']['AllmannaRad']
        else:
            props['rdf:type'] = self.ns['rpubl']['Myndighetsforeskrift']

        # 3.5: Check to see that we have all properties that we expect
        # (should maybe be done elsewhere later?)
        tracelog.info("Checking required properties")
        for prop in ('dct:identifier', 'dct:title', 'rpubl:arsutgava',
                     'dct:publisher', 'rpubl:beslutadAv', 'rpubl:beslutsdatum',
                     'rpubl:forfattningssamling', 'rpubl:ikrafttradandedatum',
                     'rpubl:lopnummer', 'rpubl:utkomFranTryck'):
            if not prop in props:
                self.log.warning("%s: Failed to find %s" % (basefile, prop))

        tracelog.info("Checking rpubl:bemyndigande")
        if props['rdf:type'] == self.ns['rpubl']['Myndighetsforeskrift']:
            if not has_bemyndiganden:
                self.log.warning(
                    "%s: Failed to find rpubl:bemyndigande" % (basefile))

        # 4: Add the cleaned data to a RDFLib Graph
        # (maybe we should do that as early as possible?)
        tracelog.info("Adding items to rdflib.Graph")
        for (prop, value) in list(props.items()):
            (prefix, term) = prop.split(":", 1)
            p = self.ns[prefix][term]
            if not (isinstance(value, URIRef) or isinstance(value, Literal)):
                self.log.warning("%s: %s is a %s, not a URIRef or Literal" %
                                 (basefile, prop, type(value)))
            g.add((URIRef(uri), p, value))

        # 5: Create data for the body, removing various control characters
        # TODO: Use pdftohtml to create a nice viewable HTML
        # version instead of this plaintext stuff
        reader.seek(0)
        body = []

        # A fairly involved way of filtering out all control
        # characters from a string
        import unicodedata
        if six.PY3:
            all_chars = (chr(i) for i in range(0x10000))
        else:
            all_chars = (unichr(i) for i in range(0x10000))
        control_chars = ''.join(
            c for c in all_chars if unicodedata.category(c) == 'Cc')
        # tab and newline are technically Control characters in
        # unicode, but we want to keep them.
        control_chars = control_chars.replace("\t", "").replace("\n", "")

        control_char_re = re.compile('[%s]' % re.escape(control_chars))
        for page in reader.getiterator(reader.readpage):
            text = xml_escape(control_char_re.sub('', page))
            body.append("<pre>%s</pre>\n\n" % text)

        # 5: Done!
        #
        doc.body = body
        doc.lang = 'sv'
        doc.uri = uri
        return doc

    def tabs(cls, primary=False):
        return [['Myndighetsföreskrifter', '/myndfskr/']]


class SJVFS(MyndFskr):
    alias = "sjvfs"
    start_url = "http://www.jordbruksverket.se/forfattningar/forfattningssamling.4.5aec661121e2613852800012537.html"

    def download(self, basefile=None):
        soup = BeautifulSoup(requests.get(self.start_url).text)
        main = soup.find("ul", "c112")
        extra = []
        for a in list(main.findAll("a")):
            url = urllib.parse.urljoin(self.start_url, a['href'])
            self.log.info("Fetching %s %s" % (a.text, url))
            extra.extend(self.download_indexpage(url, usecache=usecache))

        extra2 = []
        for url in list(set(extra)):
            self.log.info("Extrafetching %s" % (url))
            extra2.extend(self.download_indexpage(url, usecache=usecache))

        for url in list(set(extra2)):
            self.log.info("Extra2fetching %s" % (url))
            self.download_indexpage(url, usecache=usecache)

    def download_indexpage(self, url):

        subsoup = BeautifulSoup(requests.get(url).text)
        submain = subsoup.find("div", "pagecontent")
        extrapages = []
        for a in submain.findAll("a"):
            if a['href'].endswith(".pdf") or a['href'].endswith(".PDF"):
                if re.search('\d{4}:\d+', a.text):
                    m = re.search('(\w+FS|) ?(\d{4}:\d+)', a.text)
                    fs = m.group(1).lower()
                    fsnr = m.group(2)
                    if not fs:
                        fs = "sjvfs"
                    basefile = "%s/%s" % (fs, fsnr)
                    suburl = urllib.parse.unquote(
                        urllib.parse.urljoin(url, a['href'])).encode('utf-8')
                    self.download_single(
                        basefile, usecache=usecache, url=suburl)
                elif a.text == "Besult":
                    basefile = a.findParent(
                        "td").findPreviousSibling("td").find("a").text
                    self.log.debug(
                        "Will download beslut to %s (later)" % basefile)
                elif a.text == "Bilaga":
                    basefile = a.findParent(
                        "td").findPreviousSibling("td").find("a").text
                    self.log.debug(
                        "Will download bilaga to %s (later)" % basefile)
                elif a.text == "Rättelseblad":
                    basefile = a.findParent(
                        "td").findPreviousSibling("td").find("a").text
                    self.log.debug(
                        "Will download rättelseblad to %s (later)" % basefile)
                else:
                    self.log.debug("I don't know what to do with %s" % a.text)
            else:
                suburl = urljoin(url, a['href'])
                extrapages.append(suburl)
        return extrapages


class DVFS(MyndFskr):
    alias = "dvfs"


class FFFS(MyndFskr):
    alias = "fffs"
    start_url = "http://www.fi.se/Regler/FIs-forfattningar/Forteckning-FFFS/"
    document_url = "http://www.fi.se/Regler/FIs-forfattningar/Samtliga-forfattningar/%s/"

    def download(self, basefile=None):
        soup = BeautifulSoup(requests.get(self.start_url).text)
        main = soup.find(id="mainarea")
        docs = []
        for numberlabel in main.findAll(text='NUMMER'):
            numberdiv = numberlabel.findParent('div').parent

            typediv = numberdiv.findNextSibling()
            if typediv.find('div', 'FFFSListAreaLeft').get_text(strip=True) != "TYP":
                self.log.error("Expected TYP in div, found %s" %
                               typediv.get_text(strip=True))
                continue

            titlediv = typediv.findNextSibling()
            if titlediv.find('div', 'FFFSListAreaLeft').get_text(strip=True) != "RUBRIK":
                self.log.error("Expected RUBRIK in div, found %s" %
                               titlediv.get_text(strip=True))
                continue

            number = numberdiv.find('div', 'FFFSListAreaRight').get_text(strip=True)
            tmpfile = mktemp()
            snippetfile = self.store.downloaded_path(
                number).replace(".pdf", ".snippet.html")
            fp = codecs.open(tmpfile, "w", encoding="utf-8")
            fp.write(str(numberdiv))
            fp.write(str(typediv))
            fp.write(str(titlediv))
            fp.close()
            util.replace_if_different(tmpfile, snippetfile)

            self.download_single(number, usecache)

    def download_single(self, basefile, usecache=False):
        self.log.debug("%s: download_single..." % basefile)
        pdffile = self.store.downloaded_path(basefile)
        existed = os.path.exists(pdffile)
        if usecache and existed:
            self.log.debug("%s: already exists, not downloading" % basefile)
            return
        snippetfile = pdffile.replace(".pdf", ".snippet.html")
        descriptionfile = pdffile.replace(".pdf", ".html")

        soup = BeautifulSoup(open(snippetfile))
        href = soup.find(text="RUBRIK").findParent(
            "div").findPreviousSibling().find('a')['href']
        url = urljoin("http://www.fi.se/Regler/FIs-forfattningar/Forteckning-FFFS/", href)
        if href.endswith(".pdf"):
            if self.download_if_needed(url, pdffile):
                if existed:
                    self.log.info("%s: downloaded new version from %s" %
                                  (basefile, url))
                else:
                    self.log.info("%s: downloaded from %s" % (basefile, url))

        elif "/Samtliga-forfattningar/" in href:
            self.log.debug("%s: Separate page" % basefile)
            self.download_if_needed(url, descriptionfile)
            soup = BeautifulSoup(open(descriptionfile))
            for link in soup.find("div", id="mainarea").findAll("a"):
                suburl = urljoin(url, link['href']).replace(" ", "%20")
                if link.text == 'Grundförfattning':
                    if self.download_if_needed(suburl, pdffile):
                        self.log.info("%s: downloaded main PDF" % basefile)

                elif link.text == 'Konsoliderad version':
                    conspdffile = pdffile.replace(".pdf", "_k.pdf")
                    if self.download_if_needed(suburl, conspdffile):
                        self.log.info(
                            "%s: downloaded consolidated PDF" % basefile)

                elif link.text == 'Ändringsförfattning':
                    self.log.info("Skipping change regulation")
                elif link['href'].endswith(".pdf"):
                    filename = link['href'].split("/")[-1]
                    otherpdffile = pdffile.replace(".pdf", "-" + filename)
                    if self.download_if_needed(suburl, otherpdffile):
                        self.log.info("%s: downloaded '%s' to %s" %
                                      (basefile, link.text, otherpdffile))

        else:
            self.log.warning("%s: No idea!" % basefile)


class ELSAKFS(MyndFskr):
    alias = "elsakfs"  # real name is ELSÄK-FS, but avoid swedchars, uppercase and dashes
    uri_slug = "elsaek-fs"  # for use in

    start_url = "http://www.elsakerhetsverket.se/sv/Lag-och-ratt/Foreskrifter/Elsakerhetsverkets-foreskrifter-listade-i-nummerordning/"


class NFS(MyndFskr):
    alias = "nfs"

    start_url = "http://www.naturvardsverket.se/sv/Start/Lagar-och-styrning/Foreskrifter-och-allmanna-rad/Foreskrifter/"


class STAFS(MyndFskr):
    alias = "stafs"
    re_identifier = re.compile('STAFS (\d{4})[:/_-](\d+)')

    start_url = "http://www.swedac.se/sv/Det-handlar-om-fortroende/Lagar-och-regler/Alla-foreskrifter-i-nummerordning/"

    def download(self, basefile=None):
        soup = BeautifulSoup(requests.get(self.start_url).text)
        for link in list(soup.find_all("a", href=re.compile('/STAFS/'))):
            basefile = re.search('\d{4}:\d+', link.text).group(0)
            self.download_single(basefile, urljoin(self.start_url, link['href']))

    def download_single(self, basefile, url):
        self.log.info("%s: %s" % (basefile, url))
        consolidated_link = None
        newest = None
        soup = BeautifulSoup(requests.get(url).text)
        for link in soup.find_all("a", text=self.re_identifier):
            self.log.info("   %s: %s %s" % (basefile, link.text, link.url))
            if "konso" in link.text:
                consolidated_link = link
            else:
                m = self.re_identifier.search(link.text)
                assert m
                if link.url.endswith(".pdf"):
                    basefile = m.group(1) + ":" + m.group(2)
                    filename = self.store.downloaded_path(basefile)
                    self.log.info("        Downloading to %s" % filename)
                    self.download_if_needed(link.absolute_url, filename)
                    if basefile > newest:
                        self.log.debug(
                            "%s larger than %s" % (basefile, newest))
                        consolidated_basefile = basefile + \
                            "/konsoliderad/" + basefile
                        newest = basefile
                    else:
                        self.log.debug(
                            "%s not larger than %s" % (basefile, newest))
                else:
                    # not pdf - link to yet another pg
                    subsoup = BeautifulSoup(requests.get(link).text)
                    for sublink in soup.find_all("a", text=self.re_identifier):
                        self.log.info("   Sub %s: %s %s" %
                                      (basefile, sublink.text, sublink['href']))
                        m = self.re_identifier.search(sublink.text)
                        assert m
                        if sublink.url.endswith(".pdf"):
                            subbasefile = m.group(1) + ":" + m.group(2)
                            self.download_if_needed(urljoin(link, sublink['href'], subbasefile))

        if consolidated_link:
            filename = self.store.downloaded_path(consolidated_basefile)
            self.log.info("        Downloading consd to %s" % filename)
            self.download_if_needed(
                consolidated_link.absolute_url, consolidated_basefile, filename=filename)


class SKVFS(MyndFskr):
    alias = "skvfs"
    source_encoding = "utf-8"
    downloaded_suffix = ".pdf"

    # start_url = "http://www.skatteverket.se/rattsinformation/foreskrifter/tidigarear.4.1cf57160116817b976680001670.html"
    # This url contains slightly more (older) links (and a different layout)?
    start_url = "http://www.skatteverket.se/rattsinformation/lagrummet/foreskriftergallande/aldrear.4.19b9f599116a9e8ef3680003547.html"

    # also consolidated versions
    # http://www.skatteverket.se/rattsinformation/lagrummet/foreskrifterkonsoliderade/aldrear.4.19b9f599116a9e8ef3680004242.html

    # URL's are highly unpredictable. We must find the URL for every
    # resource we want to download, we cannot transform the resource
    # id into a URL
    def download(self, basefile=None):
        self.log.info("Starting at %s" % self.start_url)
        years = {}
        soup = BeautifulSoup(requests.get(self.start_url).text)
        for link in sorted(list(soup.find_all("a", text=re.compile('^\d{4}$'))),
                           key=attrgetter('text')):
            year = int(link.text)
            # Documents for the years 1985-2003 are all on one page
            # (with links leading to different anchors). To avoid
            # re-downloading stuff when usecache=False, make sure we
            # haven't seen this url (sans fragment) before
            url = link.absolute_url.split("#")[0]
            if year not in years and url not in list(years.values()):
                self.download_year(year, url)
                years[year] = url

    # just download the most recent year
    def download_new(self):
        self.log.info("Starting at %s" % self.start_url)
        soup = BeautifulSoup(requests.get(self.start_url).text)
        link = sorted(list(soup.find_all("a", text=re.compile('^\d{4}$'))),
                      key=attrgetter('text'), reverse=True)[0]
        self.download_year(int(link.text), link.absolute_url, usecache=True)

    def download_year(self, year, url):
        self.log.info("Downloading year %s from %s" % (year, url))
        soup = BeautifulSoup(requests.get(self.start_url).text)
        for link in soup.find_all("a", text=re.compile('FS \d+:\d+')):
            if "bilaga" in link.text:
                self.log.warning("Skipping attachment in %s" % link.text)
                continue

            # sanitize trailing junk
            linktext = re.match("\w+FS \d+:\d+", link.text).group(0)
            # something like skvfs/2010/23 or rsfs/1996/9
            basefile = linktext.strip(
            ).lower().replace(" ", "/").replace(":", "/")
            self.download_single(
                basefile, link.absolute_url)

    def download_single(self, basefile, url):
        self.log.info("Downloading %s from %s" % (basefile, url))
        self.document_url = url + "#%s"
        html_downloaded = super(
            SKVFS, self).download_single(basefile)
        year = int(basefile.split("/")[1])
        if year >= 2007:  # download pdf as well
            filename = self.store.downloaded_path(basefile)
            pdffilename = os.path.splitext(filename)[0] + ".pdf"
            if not os.path.exists(pdffilename):
                soup = self.soup_from_basefile(basefile)
                pdflink = soup.find(href=re.compile('\.pdf$'))
                if not pdflink:
                    self.log.debug("No PDF file could be found")
                    return html_downloaded
                pdftext = pdflink.get_text(strip=True)
                pdfurl = urljoin(url, pdflink['href'])
                self.log.debug("Found %s at %s" % (pdftext, pdfurl))
                pdf_downloaded = self.download_if_needed(pdfurl, pdffilename)
                return html_downloaded and pdf_downloaded
            else:
                return False
        else:
            return html_downloaded
