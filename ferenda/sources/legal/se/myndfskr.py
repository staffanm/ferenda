# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import os
import re
import logging
import codecs
from tempfile import mktemp
from operator import attrgetter
from xml.sax.saxutils import escape as xml_escape

from rdflib import Graph, URIRef, Literal, Namespace
from bs4 import BeautifulSoup
import requests
import six
from six.moves.urllib_parse import urljoin, unquote
import lxml.html

from ferenda import TextReader, Describer, Facet, DocumentRepository, PDFReader
from ferenda import util, decorators, errors
from ferenda.elements import Body, Page, Preformatted, Link
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.sources.legal.se import legaluri
from . import SwedishLegalSource
from .swedishlegalsource import SwedishCitationParser

from rdflib import RDF
from rdflib.namespace import DCTERMS, SKOS
from . import RPUBL, RINFOEX
PROV = Namespace(util.ns['prov'])


class MyndFskrBase(SwedishLegalSource):

    """A abstract base class for fetching and parsing regulations from
    various swedish government agencies. These documents often have a
    similar structure both linguistically and graphically (most of the
    time they are in similar PDF documents), enabling us to parse them
    in a generalized way. (Downloading them often requires
    special-case code, though.)

    """
    source_encoding = "utf-8"
    downloaded_suffix = ".pdf"
    alias = 'myndfskr'

    rdf_type = (RPUBL.Myndighetsforeskrift, RPUBL.AllmannaRad)
    required_predicates = [RDF.type, DCTERMS.title,
                           DCTERMS.identifier, RPUBL.arsutgava,
                           DCTERMS.publisher, RPUBL.beslutadAv,
                           RPUBL.beslutsdatum,
                           RPUBL.forfattningssamling,
                           RPUBL.ikrafttradandedatum, RPUBL.lopnummer,
                           RPUBL.utkomFranTryck, PROV.wasGeneratedBy]
    sparql_annotations = None  # until we can speed things up

    basefile_regex = re.compile('(?P<basefile>\d{4}[:/_-]\d{1,3})(?:|\.\w+)$')
    document_url_regex = re.compile('.*(?P<basefile>\d{4}[:/_-]\d{1,3}).pdf$')
    download_accept_404 = True  # because the occasional 404 is to be expected

    nextpage_regex = None
    nextpage_url_regex = None
    download_rewrite_url = False  # iff True, use remote_url to rewrite
    # download links instead of accepting
    # found links as-is
    download_formid = None  # if the paging uses forms, POSTs and other
    # forms of insanity

    def forfattningssamlingar(self):
        return [self.alias]

    def download_sanitize_basefile(self, basefile):
        segments = re.split('[/:_-]', basefile.lower())
        # force "01" to "1" (and check integerity (not integrity))
        segments[-1] = str(int(segments[-1]))
        if len(segments) == 2:
            basefile = "%s:%s" % tuple(segments)
        elif len(segments) == 3:
            basefile = "%s/%s:%s" % tuple(segments)
        if not any((basefile.startswith(fs + "/") for fs
                    in self.forfattningssamlingar())):
            return self.forfattningssamlingar()[0] + "/" + basefile
        else:
            return basefile

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        # this is an extended version of
        # DocumentRepository.download_get_basefiles which handles
        # "next page" navigation and also ensures that the default
        # basefilepattern is "myndfs/2015:1", not just "2015:1"
        # (through download_sanitize_basefile)
        yielded = set()
        while source:
            nextform = nexturl = None
            for (element, attribute, link, pos) in source:
                basefile = None

                # Two step process: First examine link text to see if
                # basefile_regex match. If not, examine link url to see
                # if document_url_regex
                elementtext = " ".join(element.itertext())
                if (self.basefile_regex and
                        elementtext and
                        re.search(self.basefile_regex, elementtext)):
                    m = re.search(self.basefile_regex, elementtext)
                    basefile = m.group("basefile")
                elif self.document_url_regex and re.match(self.document_url_regex, link):
                    m = re.match(self.document_url_regex, link)
                    if m:
                        basefile = m.group("basefile")

                if basefile:
                    basefile = self.download_sanitize_basefile(basefile)
                    if self.download_rewrite_url:
                        link = self.remote_url(basefile)
                    if basefile not in yielded:
                        yield (basefile, link)
                        yielded.add(basefile)
                if (self.nextpage_regex and elementtext and
                        re.search(self.nextpage_regex, elementtext)):
                    nexturl = link
                elif (self.nextpage_url_regex and
                      re.search(self.nextpage_url_regex, link)):
                    nexturl = link
                if (self.download_formid and
                        element.tag == "form" and
                        element.get("id") == self.download_formid):
                    nextform = element
            if nextform is not None and nexturl is not None:
                resp = self.download_post_form(nextform, nexturl)
            elif nexturl is not None:
                resp = self.session.get(nexturl)
            else:
                resp = None
                source = None

            if resp:
                tree = lxml.html.document_fromstring(resp.text)
                tree.make_links_absolute(resp.url,
                                         resolve_base_href=True)
                source = tree.iterlinks()

    def download_post_form(self, form, url):
        raise NotImplementedError

    def canonical_uri(self, basefile):
        # The canonical URI for these documents cannot always be
        # computed from the basefile. Find the primary subject of the
        # distilled RDF graph instead.
        if not os.path.exists(self.store.distilled_path(basefile)):
            return None

        g = Graph()
        g.parse(self.store.distilled_path(basefile))
        subjects = list(g.subject_objects(RDF.type))

        if subjects:
            return str(subjects[0][0])
        else:
            self.log.warning(
                "No canonical uri in %s" % (self.distilled_path(basefile)))
            return None

    def basefile_from_uri(self, uri):
        # this should map https://lagen.nu/sjvfs/2014:9 to basefile sjvfs/2014:9
        # but also https://lagen.nu/dfs/2007:8 -> dfs/2007:8
        for fs in self.forfattningssamlingar():
            if uri.startswith(self.config.url + "/" + fs + "/"):
                basefile = uri[len(self.config.url):]
                return basefile

    @decorators.action
    @decorators.managedparsing
    def parse(self, doc):
        # This has a similar structure to DocumentRepository.parse but
        # works on PDF docs converted to plaintext, instead of HTML
        # trees.
        reader = self.textreader_from_basefile(doc.basefile)
        self.parse_metadata_from_textreader(reader, doc)
        self.parse_document_from_textreader(reader, doc)
        self.parse_entry_update(doc)
        return True  # Signals that everything is OK

    def textreader_from_basefile(self, basefile):
        infile = self.store.downloaded_path(basefile)
        tmpfile = self.store.path(basefile, 'intermediate', '.pdf')
        outfile = self.store.path(basefile, 'intermediate', '.txt')
        if not util.outfile_is_newer([infile], outfile):
            util.copy_if_different(infile, tmpfile)
            # this command will create a file named as the val of outfile
            util.runcmd("pdftotext %s" % tmpfile, require_success=True)
            # check to see if the outfile actually contains any text. It
            # might just be a series of scanned images.
            text = util.readfile(outfile)
            if not text.strip():
                os.unlink(outfile)
                # OK, it's scanned images. We extract these, put them in a
                # tif file, and OCR them with tesseract.
                p = PDFReader()
                p._tesseract(tmpfile, os.path.dirname(outfile), "swe", False)
                tmptif = self.store.path(basefile, 'intermediate', '.tif')
                util.robust_remove(tmptif)
        text = util.readfile(outfile)
        # if there's less than 50 chars on each page, chances are it's
        # just watermarks or leftovers from the scanning toolchain,
        # and that the real text is in non-OCR:ed images.
        if len(text) / (text.count("\x0c") + 1) < 50:
            self.log.warning("%s: Extracted text from PDF is suspiciously short (%s bytes per page, %s total)" %
                             (basefile, len(text) / text.count("\x0c") + 1, len(text)))
        util.robust_remove(tmpfile)
        text = self.sanitize_text(text, basefile)
        return TextReader(string=text, encoding=self.source_encoding,
                          linesep=TextReader.UNIX)

    def sanitize_text(self, text, basefile):
        return text

    def fwdtests(self):
        return {'dcterms:issn': ['^ISSN (\d+\-\d+)$'],
                'dcterms:title':
                ['((?:Föreskrifter|[\w ]+s (?:föreskrifter|allmänna råd)).*?)\n\n'],
                'dcterms:identifier': ['^([A-ZÅÄÖ-]+FS\s\s?\d{4}:\d+)$'],
                'rpubl:utkomFranTryck':
                ['Utkom från\strycket\s+den\s(\d+ \w+ \d{4})'],
                'rpubl:omtryckAv': ['^(Omtryck)$'],
                'rpubl:genomforDirektiv': ['Celex (3\d{2,4}\w\d{4})'],
                'rpubl:beslutsdatum':
                ['(?:har beslutats|beslutade|beslutat|Beslutad) den (\d+ \w+ \d{4})',
                 'Beslutade av (?:[A-ZÅÄÖ][\w ]+) den (\d+ \w+ \d{4}).'],
                'rpubl:beslutadAv':
                ['\n\s*([A-ZÅÄÖ][\w ]+?)\d? (?:meddelar|lämnar|föreskriver|beslutar)',
                 '\s(?:meddelar|föreskriver) ([A-ZÅÄÖ][\w ]+?)\d?\s'],
                'rpubl:bemyndigande':
                [' ?(?:meddelar|föreskriver|Föreskrifterna meddelas|Föreskrifterna upphävs)\d?,? (?:följande |)med stöd av\s(.*?) ?(?:att|efter\ssamråd|dels|följande|i fråga om|och lämnar allmänna råd|och beslutar följande allmänna råd|\.\n)',
                 '^Med stöd av (.*)\s(?:meddelar|föreskriver)']
                }

    def revtests(self):
        return {'rpubl:ikrafttradandedatum':
                ['(?:Denna författning|Dessa föreskrifter|Dessa allmänna råd|Dessa föreskrifter och allmänna råd)\d* träder i ?kraft den (\d+ \w+ \d{4})',
                 'Dessa föreskrifter träder i kraft, (?:.*), i övrigt den (\d+ \w+ \d{4})',
                 'ska(?:ll|)\supphöra att gälla (?:den |)(\d+ \w+ \d{4}|denna dag|vid utgången av \w+ \d{4})',
                 'träder i kraft den dag då författningen enligt uppgift på den (utkom från trycket)'],
                'rpubl:upphaver':
                ['träder i kraft den (?:\d+ \w+ \d{4}), då(.*)ska upphöra att gälla',
                 'ska(?:ll|)\supphöra att gälla vid utgången av \w+ \d{4}, nämligen(.*?)\n\n',
                 'att (.*) skall upphöra att gälla (denna dag|vid utgången av \w+ \d{4})']
                }

    def parse_metadata_from_textreader(self, reader, doc):
        g = doc.meta

        # 1. Find some of the properties on the first page (or the
        #    2nd, or 3rd... continue past TOC pages, cover pages etc
        #    until the "real" first page is found) NB: FFFS 2007:1
        #    has ten (10) TOC pages!
        pagecount = 0
        for page in reader.getiterator(reader.readpage):
            pagecount += 1
            props = {}
            for (prop, tests) in list(self.fwdtests().items()):
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
            self.log.debug("%s: Couldn't find required props on page %s" %
                           (doc.basefile, pagecount))

        if 'rpubl:beslutsdatum' not in props:
            raise errors.ParseError(
                "%s: Couldn't find required properties on any page, giving up" %
                doc.basefile)

        # 2. Find some of the properties on the last 'real' page (not
        #    counting appendicies)
        reader.seek(0)
        pagesrev = reversed(list(reader.getiterator(reader.readpage)))
        # The language used to expres these two properties differ
        # quite a lot, more than what is reasonable to express in a
        # single regex. We therefore define a set of possible
        # expressions and try them in turn.
        revtests = self.revtests()
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

            # Single required propery. If we find this, we're done
            if 'rpubl:ikrafttradandedatum' in props:
                break

        self.sanitize_metadata(props, doc)
        self.polish_metadata(props, doc)
        self.infer_metadata(doc.meta.resource(doc.uri), doc.basefile)
        return doc

    def sanitize_metadata(self, props, doc):
        """Correct those irregularities in the extracted metadata that we can
           find"""

        # common false positive
        if 'dcterms:title' in props:
            if 'denna f\xf6rfattning har beslutats den' in props['dcterms:title']:
                del props['dcterms:title']
            elif ("\nbeslutade den " in props['dcterms:title'] or
                  "; beslutade den " in props['dcterms:title']):
                # sometimes the title isn't separated with two
                # newlines from the rest of the text
                props['dcterms:title'] = props[
                    'dcterms:title'].split("beslutade den ")[0]
        if 'rpubl:bemyndigande' in props:
            props['rpubl:bemyndigande'] = props[
                'rpubl:bemyndigande'].replace('\u2013', '-')

    def polish_metadata(self, props, doc):
        """Clean up data, including converting a string->string dict to a
        proper RDF graph.

        """
        def makeurl(attributes):
            resource = self.attributes_to_resource(attributes)
            return self.minter.space.coin_uri(resource)

        parser = SwedishCitationParser(LegalRef(LegalRef.LAGRUM),
                                       self.minter,
                                       self.commondata)

        # FIXME: this code should go into canonical_uri, if we can
        # find a way to give it access to props['dcterms:identifier']
        if 'dcterms:identifier' in props:
            (pub, year, ordinal) = re.split('[ :]',
                                            props['dcterms:identifier'])
        else:
            # do a a simple inference from basefile and populate props
            (pub, year, ordinal) = re.split('[/:_]', doc.basefile.upper())
            props['dcterms:identifier'] = "%s %s:%s" % (pub, year, ordinal)
            self.log.warning("%s: Couldn't find dcterms:identifier, inferred %s from basefile" %
                             (doc.basefile, props['dcterms:identifier']))
        uri = makeurl({'rdf:type': RPUBL.Myndighetsforeskrift,
                       'rpubl:forfattningssamling': pub,
                       'rpubl:arsutgava': year,
                       'rpubl:lopnummer': ordinal})

        if doc.uri is not None and uri != doc.uri:
            self.log.warning(
                "Assumed URI would be %s but it turns out to be %s" %
                (doc.uri, uri))
        doc.uri = uri
        desc = Describer(doc.meta, doc.uri)

        fs = self.lookup_resource(pub, SKOS.altLabel)
        desc.rel(RPUBL.forfattningssamling, fs)
        # publisher for the series == publisher for the document
        desc.rel(DCTERMS.publisher,
                 self.commondata.value(fs, DCTERMS.publisher))

        desc.value(RPUBL.arsutgava, year)
        desc.value(RPUBL.lopnummer, ordinal)
        desc.value(DCTERMS.identifier, props['dcterms:identifier'])
        if 'rpubl:beslutadAv' in props:
            desc.rel(RPUBL.beslutadAv,
                     self.lookup_resource(props['rpubl:beslutadAv']))

        if 'dcterms:issn' in props:
            desc.value(DCTERMS.issn, props['dcterms:issn'])

        if 'dcterms:title' in props:
            desc.value(DCTERMS.title,
                       Literal(util.normalize_space(
                           props['dcterms:title']), lang="sv"))

            if re.search('^(Föreskrifter|[\w ]+s föreskrifter) om ändring i ',
                         props['dcterms:title'], re.UNICODE):
                # There should be something like FOOFS 2013:42 (or
                # possibly just 2013:42) in the title
                m = re.search('([A-ZÅÄÖ-]+FS |)\d{4}:\d+',
                              props['dcterms:title'])
                if not m:
                    raise errors.ParseError("%s: Couldn't find reference to change act in title %r" %
                                            (doc.basefile, props['dcterms:title']))
                orig = m.group(0)
                if " " in orig:
                    (publication, year, ordinal) = re.split('[ :]', orig)
                else:
                    # No FS given for the base act, assume that it's
                    # the same as this change act
                    (year, ordinal) = re.split('[ :]', orig)
                    pub = props['dcterms:identifier'].split(" ")[0]
                origuri = makeurl({'rdf:type': RPUBL.Myndighetsforeskrift,
                                   'rpubl:forfattningssamling': pub,
                                   'rpubl:arsutgava': year,
                                   'rpubl:lopnummer': ordinal})
                desc.rel(RPUBL.andrar,
                         URIRef(origuri))

            # FIXME: is this a sensible value for rpubl:upphaver
            if (re.search('^(Föreskrifter|[\w ]+s föreskrifter) om upphävande '
                          'av', props['dcterms:title'], re.UNICODE)
                    and not 'rpubl:upphaver' in props):
                props['rpubl:upphaver'] = props['dcterms:title']

        for key, pred in (('rpubl:utkomFranTryck', RPUBL.utkomFranTryck),
                          ('rpubl:beslutsdatum', RPUBL.beslutsdatum),
                          ('rpubl:ikrafttradandedatum', RPUBL.ikrafttradandedatum)):
            if key in props:
                # FIXME: how does this even work
                if (props[key] == 'denna dag' and
                        key == 'rpubl:ikrafttradandedatum'):
                    desc.value(RPUBL.ikrafttradandedatum,
                               self.parse_swedish_date(props['rpubl:beslutsdatum']))
                elif (props[key] == 'utkom från trycket' and
                      key == 'rpubl:ikrafttradandedatum'):
                    desc.value(RPUBL.ikrafttradandedatum,
                               self.parse_swedish_date(props['rpubl:utkomFranTryck']))
                else:
                    desc.value(pred,
                               self.parse_swedish_date(props[key].lower()))

        if 'rpubl:genomforDirektiv' in props:
            diruri = makeurl({'rdf:type': RINFOEX.EUDirektiv, # FIXME: standardize this type
                              'rpubl:celexNummer':
                              props['rpubl:genomforDirektiv']})
            desc.rel(RPUBL.genomforDirektiv, diruri)

        has_bemyndiganden = False
        if 'rpubl:bemyndigande' in props:
            result = parser.parse_string(props['rpubl:bemyndigande'])
            bemyndiganden = [x.uri for x in result if hasattr(x, 'uri')]

            # some of these uris need to be filtered away due to
            # over-matching by parser.parse
            filtered_bemyndiganden = []
            for bem_uri in bemyndiganden:
                keep = True
                for compare in bemyndiganden:
                    if (len(compare) > len(bem_uri) and
                            compare.startswith(bem_uri)):
                        keep = False
                if keep:
                    filtered_bemyndiganden.append(bem_uri)

            for bem_uri in filtered_bemyndiganden:
                desc.rel(RPUBL.bemyndigande, bem_uri)

        if 'rpubl:upphaver' in props:
            for upph in re.findall('([A-ZÅÄÖ-]+FS \d{4}:\d+)',
                                   util.normalize_space(props['rpubl:upphaver'])):
                (pub, year, ordinal) = re.split('[ :]', upph)
                upphuri = makeurl({'rdf:type': RPUBL.Myndighetsforeskrift,
                                   'rpubl:forfattningssamling': pub,
                                   'rpubl:arsutgava': year,
                                   'rpubl:lopnummer': ordinal})
                desc.rel(RPUBL.upphaver, upphuri)

        if ('dcterms:title' in props and
            "allmänna råd" in props['dcterms:title'] and
                "föreskrifter" not in props['dcterms:title']):
            rdftype = RPUBL.AllmannaRad
        else:
            rdftype = RPUBL.Myndighetsforeskrift
        desc.rdftype(rdftype)
        desc.value(self.ns['prov'].wasGeneratedBy, self.qualified_class_name())
        if RPUBL.bemyndigande in self.required_predicates:
            self.required_predicates.pop(self.required_predicates.index(RPUBL.bemyndigande))
        if rdftype == RPUBL.Myndighetsforeskrift:
            self.required_predicates.append(RPUBL.bemyndigande)

    def parse_document_from_textreader(self, reader, doc):
        # Create data for the body, removing various control characters
        # TODO: Use pdftohtml to create a nice viewable HTML
        # version instead of this plaintext stuff
        reader.seek(0)
        body = Body()

        # A fairly involved way of filtering out all control
        # characters from a string

        # FIXME: should go in sanitize_text
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
        for idx, page in enumerate(reader.getiterator(reader.readpage)):
            text = xml_escape(control_char_re.sub('', page))
            p = Page(ordinal=idx + 1)
            p.append(Preformatted(text))
            body.append(p)
        doc.body = body

    def facets(self):
        return [Facet(RDF.type),
                Facet(DCTERMS.title),
                Facet(DCTERMS.publisher),
                Facet(DCTERMS.identifier),
                Facet(RPUBL.arsutgava,
                      use_for_toc=True)]

    def toc_item(self, binding, row):
        """Returns a formatted version of row, using Element objects"""
        # more defensive version of DocumentRepository.toc_item
        label = ""
        if 'dcterms_identifier' in row:
            label = row['dcterms_identifier']
        else:
            self.log.warning("No dcterms:identifier for %s" % row['uri'])

        if 'dcterms_title' in row:
            label += ": " + row['dcterms_title']
        else:
            self.log.warning("No dcterms:title for %s" % row['uri'])
            label = "URI: " + row['uri']
        return [Link(label, uri=row['uri'])]

    def tabs(self):
        return [(self.__class__.__name__, self.dataset_uri())]


class AFS(MyndFskrBase):
    alias = "afs"
    start_url = "http://www.av.se/lagochratt/afs/nummerordning.aspx"
    basefile_regex = None
    document_url_regex = ".*(afs|AFS)(?P<basefile>\d+_\d+)\.pdf$"

    # This handles the case when pdftotext confuses the metadata in
    # the right margin on the frontpage, eg:
    #    Arbetsmiljöverkets föreskrifter om upphävande AFS 2014:44
    #    Utkom från trycket
    #    av föreskrifterna (AFS 2005:19) om förebyggande den 20 januari 2014
    #    av allvarliga kemikalieolyckor;
    # and converts it to
    #    Arbetsmiljöverkets föreskrifter om upphävande
    #    av föreskrifterna (AFS 2005:19) om förebyggande
    #    av allvarliga kemikalieolyckor;
    #
    #    AFS 2014:44
    #    Utkom från trycket
    #    den 20 januari 2014

    def sanitize_text(self, text, basefile):
        # 'afs/2014:39' -> 'AFS 2014:39'
        probable_id = basefile.upper().replace("/", " ")
        newtext = ""
        margin = ""
        inmargin = False
        datematch = re.compile("den \d+ \w+ \d{4}$").search
        for line in text.split("\n"):
            newline = True
            if line.endswith(probable_id) and not margin and len(
                    line) > len(probable_id):  # and possibly other sanity checks
                inmargin = True
                margin += probable_id + "\n"
                newline = line[:line.index(probable_id)]
            elif inmargin and line.endswith("Utkom från trycket"):
                margin += "Utkom från trycket\n"
                newline = line[:line.index("Utkom från trycket")]
            elif inmargin and datematch(line):
                m = datematch(line)
                margin += m.group(0) + "\n"
                newline = line[:m.start()]
            elif inmargin and line == "":
                inmargin = False
                newline = "\n" + margin + "\n"
            else:
                newline = line
            if newline:
                if newline is True:
                    newline = ""
                newtext += newline + "\n"
        return newtext

    def download_sanitize_basefile(self, basefile):
        return super(AFS, self).download_sanitize_basefile(basefile.replace("_", ":"))


class BOLFS(MyndFskrBase):
    # FIXME: The id is not linked, and the link does not *reliably*
    # contain the id: given link, one should get
    # link.parent.parent.parent.div.h3.text for the basefile. Most of
    # the time, the ID is deductible from the link though.
    alias = "bolfs"
    start_url = "http://www.bolagsverket.se/om/oss/verksamhet/styr/forfattningssamling"


class DIFS(MyndFskrBase):
    alias = "difs"
    start_url = "http://www.datainspektionen.se/lagar-och-regler/datainspektionens-foreskrifter/"

    # def sanitize_text(self, text, basefile):


class DVFS(MyndFskrBase):
    alias = "dvfs"
    start_url = "http://www.domstol.se/Ladda-ner--bestall/Verksamhetsstyrning/DVFS/DVFS1/"
    downloaded_suffix = ".html"

    nextpage_regex = ">"
    nextpage_url_regex = None
    basefile_regex = "^\s*(?P<basefile>\d{4}:\d+)"
    download_rewrite_url = True
    download_formid = "aspnetForm"

    def remote_url(self, basefile):
        if "/" in basefile:
            basefile = basefile.split("/")[1]
        return "http://www.domstol.se/Ladda-ner--bestall/Verksamhetsstyrning/DVFS/DVFS2/%s/" % basefile.replace(
            ":", "")

    def download_post_form(self, form, url):
        # nexturl == "javascript:__doPostBack('ctl00$MainRegion$"
        #            "MainContentRegion$LeftContentRegion$ctl01$"
        #            "epiNewsList$ctl09$PagingID15','')"
        etgt, earg = [m.group(1) for m in re.finditer("'([^']*)'", url)]
        fields = dict(form.fields)

        # requests seem to prefer that keys and values to the
        # files argument should be str (eg bytes) on py2 and
        # str (eg unicode) on py3. But we use unicode_literals
        # for this file, so we define a wrapper to convert
        # unicode strs in the appropriate way
        if six.PY2:
            f = six.binary_type
        else:
            f = lambda x: x
        fields[f('__EVENTTARGET')] = etgt
        fields[f('__EVENTARGUMENT')] = earg
        for k, v in fields.items():
            if v is None:
                fields[k] = f('')
        # using the files argument to requests.post forces the
        # multipart/form-data encoding
        req = requests.Request(
            "POST", form.get("action"), cookies=self.session.cookies, files=fields).prepare()
        # Then we need to remove filename from req.body in an
        # unsupported manner in order not to upset the
        # sensitive server
        body = req.body
        if isinstance(body, bytes):
            body = body.decode()  # should be pure ascii
        req.body = re.sub(
            '; filename="[\w\-\/]+"', '', body).encode()
        req.headers['Content-Length'] = str(len(req.body))
        # self.log.debug("posting to event %s" % etgt)
        resp = self.session.send(req, allow_redirects=True)
        return resp

    def textreader_from_basefile(self, basefile):
        infile = self.store.downloaded_path(basefile)
        soup = BeautifulSoup(util.readfile(infile))
        main = soup.find("div", id="readme")
        if main:
            main.find("div", "rs_skip").decompose()
            maintext = main.get_text("\n\n", strip=True)
            outfile = self.store.path(basefile, 'intermediate', '.txt')
            util.writefile(outfile, maintext)
            return TextReader(string=maintext)
        elif soup.find("title").text == "Sveriges Domstolar - 404":
            e = errors.DocumentRemovedError()
            e.dummyfile = self.store.parsed_path(basefile)
            raise e

    def fwdtests(self):
        t = super(DVFS, self).fwdtests()
        t["dcterms:identifier"] = ['(DVFS\s\s?\d{4}:\d+)']
        return t


class EIFS(MyndFskrBase):
    alias = "eifs"
    start_url = "http://www.ei.se/sv/Publikationer/Foreskrifter/"
    basefile_regex = None
    document_url_regex = re.compile('.*(?P<basefile>EIFS_\d{4}_\d+).pdf$')

    def download_sanitize_basefile(self, basefile):
        basefile = basefile.replace("_", "/", 1)
        basefile = basefile.replace("_", ":", 1)
        return super(EIFS, self).download_sanitize_basefile(basefile)


class ELSAKFS(MyndFskrBase):
    alias = "elsakfs"  # real name is ELSÄK-FS, but avoid swedchars, uppercase and dashes
    uri_slug = "elsaek-fs"  # for use in
    start_url = "http://www.elsakerhetsverket.se/om-oss/lag-och-ratt/gallande-regler/Elsakerhetsverkets-foreskrifter-listade-i-nummerordning/"
    download_rewrite_url = True

    def remote_url(self, basefile):
        if "/" in basefile:
            basefile = basefile.split("/")[1]
        return "http://www.elsakerhetsverket.se/globalassets/foreskrifter/elsak-fs-%s.pdf" % basefile.replace(
            ":", "-")

    # FIXME: The crappy webserver returns status code 200 when it
    # really is a 404, eg
    # "http://www.elsakerhetsverket.se/globalassets/foreskrifter/1998-1.pdf". We
    # should handle this in download_single and not store error pages
    # when we expected documents


class Ehalso(MyndFskrBase):
    alias = "ehalso"
    # Ehälsomyndigheten publicerar i TLVFS
    start_url = "http://www.ehalsomyndigheten.se/Om-oss-/Foreskrifter/"


class FFFS(MyndFskrBase):
    alias = "fffs"
    start_url = "http://www.fi.se/Regler/FIs-forfattningar/Forteckning-FFFS/"
    document_url = "http://www.fi.se/Regler/FIs-forfattningar/Samtliga-forfattningar/%s/"
    storage_policy = "dir"  # must be able to handle attachments

    def download(self, basefile=None):
        self.session = requests.session()
        soup = BeautifulSoup(self.session.get(self.start_url).text)
        main = soup.find(id="fffs-searchresults")
        docs = []
        for numberlabel in main.find_all(text=re.compile('\s*Nummer\s*')):
            ndiv = numberlabel.find_parent('div').parent
            typediv = ndiv.findNextSibling()
            if typediv.find('div', 'FFFSListAreaLeft').get_text(strip=True) != "Typ":
                self.log.error("Expected 'Typ' in div, found %s" %
                               typediv.get_text(strip=True))
                continue

            titlediv = typediv.findNextSibling()
            if titlediv.find('div', 'FFFSListAreaLeft').get_text(strip=True) != "Rubrik":
                self.log.error("Expected 'Rubrik' in div, found %s" %
                               titlediv.get_text(strip=True))
                continue

            number = ndiv.find('div', 'FFFSListAreaRight').get_text(strip=True)
            basefile = "fffs/" + number
            tmpfile = mktemp()
            with self.store.open_downloaded(basefile, mode="w", attachment="snippet.html") as fp:
                fp.write(str(ndiv))
                fp.write(str(typediv))
                fp.write(str(titlediv))
            if (self.config.refresh or
                    (not os.path.exists(self.store.downloaded_path(basefile)))):
                self.download_single(basefile)

    # FIXME: This should create/update the documententry!!
    def download_single(self, basefile):
        pdffile = self.store.downloaded_path(basefile)
        self.log.debug("%s: download_single..." % basefile)
        snippetfile = self.store.downloaded_path(basefile, attachment="snippet.html")
        soup = BeautifulSoup(open(snippetfile))
        href = soup.find(
            text=re.compile("\s*Rubrik\s*")).find_parent("div", "FFFSListArea").a.get("href")
        url = urljoin("http://www.fi.se/Regler/FIs-forfattningar/Forteckning-FFFS/", href)
        if href.endswith(".pdf"):
            self.download_if_needed(url, basefile)

        elif "/Samtliga-forfattningar/" in href:
            self.log.debug("%s: Separate page" % basefile)
            self.download_if_needed(url, basefile,
                                    filename=self.store.downloaded_path(basefile, attachment="description.html"))
            descriptionfile = self.store.downloaded_path(
                basefile,
                attachment="description.html")
            soup = BeautifulSoup(open(descriptionfile))
            for link in soup.find("div", "maincontent").find_all("a"):
                suburl = urljoin(url, link['href']).replace(" ", "%20")
                if link.text.strip().startswith('Grundförfattning'):
                    if self.download_if_needed(suburl, basefile):
                        self.log.info("%s: downloaded main PDF" % basefile)

                elif link.text.strip().startswith('Konsoliderad version'):
                    if self.download_if_needed(suburl, basefile,
                                               filename=self.store.downloaded_path(basefile, attachment="konsoliderad.pdf")):
                        self.log.info(
                            "%s: downloaded consolidated PDF" % basefile)

                elif link.text.strip().startswith('Ändringsförfattning'):
                    self.log.info("Skipping change regulation")
                elif link['href'].endswith(".pdf"):
                    filename = link['href'].split("/")[-1]
                    if self.download_if_needed(
                            suburl, basefile, self.store.downloaded_path(basefile, attachment=filename)):
                        self.log.info("%s: downloaded '%s' to %s" %
                                      (basefile, link.text, filename))

        else:
            self.log.warning("%s: No idea!" % basefile)


class FFS(MyndFskrBase):
    alias = "ffs"
    start_url = "http://www.forsvarsmakten.se/sv/om-myndigheten/dokument/lagrum"
    # FIXME: document_url_regex should match
    #   http://www.forsvarsmakten.se/siteassets/4-om-myndigheten/dokumentfiler/lagrum/gallande-ffs-1995-2011/ffs-2010-8.pdf
    #   http://www.forsvarsmakten.se/siteassets/4-om-myndigheten/dokumentfiler/lagrum/gallande-ffs-1995-2011/ffs2010-10.pdf
    # but not
    #   http://www.forsvarsmakten.se/siteassets/4-om-myndigheten/dokumentfiler/lagrum/gallande-fib/fib2001-4.pdf


class FMI(MyndFskrBase):
    alias = "fmi"
    # Fastighetsmäklarinspektionen publicerar i KAMFS
    start_url = "http://www.fmi.se/gallande-foreskrifter"


class FoHMFS(MyndFskrBase):
    alias = "fohmfs"
    start_url = "http://www.folkhalsomyndigheten.se/publicerat-material/foreskrifter-och-allmanna-rad/"


class KFMFS(MyndFskrBase):
    alias = "kfmfs"
    start_url = "http://www.kronofogden.se/Foreskrifter.html"


class KOVFS(MyndFskrBase):
    alias = "kovfs"
    start_url = "http://publikationer.konsumentverket.se/sv/publikationer/lagarregler/forfattningssamling-kovfs/"


class KVFS(MyndFskrBase):
    alias = "kvfs"
    start_url = "http://www.kriminalvarden.se/om-kriminalvarden/publikationer/regelverk"
    # (finns även konsoliderade på http://www.kriminalvarden.se/om-kriminalvarden/styrning-och-regelverk/lagar-forordningar-och-foreskrifter)


class LMFS(MyndFskrBase):
    alias = "lmfs"
    start_url = "http://www.lantmateriet.se/Om-Lantmateriet/Rattsinformation/Foreskrifter/"


class LIFS(MyndFskrBase):
    alias = "lifs"
    start_url = "http://www.lotteriinspektionen.se/sv/Lagar-och-villkor/Foreskrifter/"


class LVFS(MyndFskrBase):
    alias = "lvfs"
    start_url = "http://www.lakemedelsverket.se/overgripande/Lagar--regler/Lakemedelsverkets-foreskrifter---LVFS/"


class MIGRFS(MyndFskrBase):
    alias = "migrfs"
    start_url = "http://www.migrationsverket.se/info/1082.html"


class MRTVFS(MyndFskrBase):
    alias = "mrtvfs"
    start_url = "http://www.radioochtv.se/Publikationer-Blanketter/Foreskrifter/"


class MSBFS(MyndFskrBase):
    alias = "msbfs"
    start_url = "https://www.msb.se/sv/Om-MSB/Lag-och-ratt/ (efter POST)"


class MYHFS(MyndFskrBase):
    #  (id vs länk)
    alias = "myhfs"
    start_url = "https://www.myh.se/Lagar-regler-och-tillsyn/Foreskrifter/"


class NFS(MyndFskrBase):
    alias = "nfs"
    start_url = "http://www.naturvardsverket.se/nfs"
    basefile_regex = "^(?P<basefile>S?NFS \d+:\d+)$"
    nextpage_regex = "Nästa"
    storage_policy = "dir"

    def download_sanitize_basefile(self, basefile):
        basefile = basefile.replace(" ", "/")
        return super(NFS, self).download_sanitize_basefile(basefile)

    def forfattningssamlingar(self):
        return ["nfs", "snfs"]

    def download_single(self, basefile, url):
        if url.endswith(".pdf") and "/Nerladdningssida/?fileType=pdf" not in url:
            # munge the URL for reasons unknown
            url = url.replace("http://www.naturvardsverket.se/",
                              "http://www.naturvardsverket.se/Nerladdningssida/?fileType=pdf&downloadUrl=/")
            return super(NFS, self).download_single(basefile, url)

        # NB: the basefile we got might be a later change act. first
        # order of business is to identify the base act basefile
        soup = BeautifulSoup(self.session.get(url).text)
        basehead = soup.find("h3", text=re.compile("Grundföreskrift$"))
        if not basehead:
            realbasefile = basefile
        else:
            m = re.match("(S?NFS)\s+(\d+:\d+)", basehead.get_text())
            realbasefile = m.group(1).lower() + "/" + m.group(2)
        self.log.info(
            "%s: Downloaded index %s, real basefile was %s" %
            (basefile, url, realbasefile))
        basefile = realbasefile
        descpath = self.store.downloaded_path(basefile,
                                              attachment="description.html")
        self.download_if_needed(url, basefile, filename=descpath)
        soup = BeautifulSoup(util.readfile(descpath))
        seen_consolidated = False
        # find all pdf links, identify consolidated version if present
        # [1:] in order to skip header
        for tr in soup.find("table", "regulations-table").find_all("tr")[1:]:
            head = tr.find("h3")
            link = tr.find("a", href=re.compile("\.pdf$", re.I))
            if not link:
                continue
            if "Konsoliderad" in head.get_text() or "-k" in link.get("href"):
                assert not seen_consolidated
                conspath = self.store.downloaded_path(basefile,
                                                      attachment="consolidated.pdf")
                consurl = urljoin(url, link.get("href"))
                self.log.info(
                    "%s: Downloading consolidated version from %s" %
                    (basefile, consurl))
                self.download_if_needed(consurl, basefile, filename=conspath)
                seen_consolidated = True
            else:
                m = re.match("(S?NFS)\s+(\d+:\d+)", head.get_text())
                subbasefile = m.group(1).lower() + "/" + m.group(2)
                suburl = urljoin(url, link.get("href"))
                self.download_single(subbasefile, suburl)


class RNFS(MyndFskrBase):
    alias = "rnfs"
    start_url = "http://www.revisorsnamnden.se/rn/om_rn/regler/kronologi.html"


class RAFS(MyndFskrBase):
    #  (efter POST)
    alias = "rafs"
    start_url = "http://riksarkivet.se/rafs"


class RGKFS(MyndFskrBase):
    alias = "rgkfs"
    start_url = "https://www.riksgalden.se/sv/omriksgalden/Pressrum/publicerat/Foreskrifter/"


class SJVFS(MyndFskrBase):
    alias = "sjvfs"
    start_url = "http://www.jordbruksverket.se/forfattningar/forfattningssamling.4.5aec661121e2613852800012537.html"
    download_iterlinks = False

    def forfattningssamlingar(self):
        return ["sjvfs", "dfs"]

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source)
        main = soup.find_all("li", "active")
        assert len(main) == 1
        extra = []
        for a in list(main[0].ul.find_all("a")):
            # only fetch subsections that start with a year, not
            # "Allmänna råd"/"Notiser"/"Meddelanden"
            label = a.text.split()[0]
            if not label.isdigit():
                continue
            # if lastdownload was 2015-02-24, dont download 2014
            # and earlier
            if (not self.config.refresh and
                    self.config.lastdownload and
                    self.config.lastdownload.year > int(label)):
                continue
            url = urljoin(self.start_url, a['href'])
            self.log.debug("Fetching index page for %s" % (a.text))
            subsoup = BeautifulSoup(self.session.get(url).text)
            submain = subsoup.find("div", "pagecontent")
            for a in submain.find_all("a", href=re.compile(".pdf$", re.I)):
                if re.search('\d{4}:\d+', a.text):
                    m = re.search('(\w+FS|) ?(\d{4}:\d+)', a.text)
                    fs = m.group(1).lower()
                    fsnr = m.group(2)
                    if not fs:
                        fs = "sjvfs"
                    basefile = "%s/%s" % (fs, fsnr)
                    suburl = unquote(urljoin(url, a['href']))
                    yield(basefile, suburl)


class SKVFS(MyndFskrBase):
    alias = "skvfs"
    source_encoding = "utf-8"
    storage_policy = "dir"
    downloaded_suffix = ".html"

    # start_url = "http://www.skatteverket.se/rattsinformation/foreskrifter/tidigarear.4.1cf57160116817b976680001670.html"
    # This url contains slightly more (older) links (and a different layout)?
    start_url = "http://www.skatteverket.se/rattsinformation/lagrummet/foreskriftergallande/aldrear.4.19b9f599116a9e8ef3680003547.html"

    # also consolidated versions
    # http://www.skatteverket.se/rattsinformation/lagrummet/foreskrifterkonsoliderade/aldrear.4.19b9f599116a9e8ef3680004242.html

    def forfattningssamlingar(self):
        return ["skvfs", "rsfs"]

    # URL's are highly unpredictable. We must find the URL for every
    # resource we want to download, we cannot transform the resource
    # id into a URL
    @decorators.recordlastdownload
    def download_get_basefiles(self, source):
        startyear = str(
            self.config.lastdownload.year) if 'lastdownload' in self.config and not self.config.refresh else "0"

        for (element, attribute, link, pos) in source:
            if not attribute == "href" or not element.text or not re.match(
                    '\d{4}', element.text):
                continue
            year = element.text
            if year >= startyear:   # string comparison is ok in this case
                self.log.debug("SKVFS: Downloading year %s from %s" % (year, link))
                resp = self.session.get(link)
                tree = lxml.html.document_fromstring(resp.text)
                tree.make_links_absolute(link, resolve_base_href=True)
                for (docelement, docattribute, doclink, docpos) in tree.iterlinks():
                    if not docelement.text or not re.match(
                            '\w+FS \d+:\d+', docelement.text):
                        continue
                    linktext = re.match("\w+FS \d+:\d+", docelement.text).group(0)
                    basefile = self.download_sanitize_basefile(linktext.replace(" ", "/"))
                    if "bilaga" in element.text:
                        self.log.warning(
                            "%s: Skipping attachment in %s" %
                            (basefile, element.text))
                        continue
                    yield(basefile, doclink)

    def download_single(self, basefile, url):
        # The HTML version is the one we always can count on being
        # present. The PDF version exists for acts 2007 or
        # later. Treat the HTML version as the main version and the
        # eventual PDF as an attachment
        # this also updates the docentry
        html_downloaded = super(SKVFS, self).download_single(basefile, url)
        # try to find link to a PDF in what was just downloaded
        soup = BeautifulSoup(util.readfile(self.store.downloaded_path(basefile)))
        pdffilename = self.store.downloaded_path(basefile,
                                                 attachment="index.pdf")
        if (self.config.refresh or not(os.path.exists(pdffilename))):
            pdflinkel = soup.find(href=re.compile('\.pdf$'))
            if pdflinkel:
                pdflink = urljoin(url, pdflinkel.get("href"))
                self.log.debug("%s: Found PDF at %s" % (basefile, pdflink))
                pdf_downloaded = self.download_if_needed(
                    pdflink,
                    basefile,
                    filename=pdffilename)
                return html_downloaded and pdf_downloaded
            else:
                return False
        else:
            return html_downloaded

    # adapted from DVFS
    def textreader_from_basefile(self, basefile):
        infile = self.store.downloaded_path(basefile)
        soup = BeautifulSoup(util.readfile(infile))
        # the DOM tree for SKVFS HTML are a real mess -- let's hope
        # this captures the relevant content. Maybe we should look for
        # a "index.pdf" attachment and prefer that one?
        h = soup.find("h1", text=re.compile("(Rikss|S)katteverkets författningssamling"))
        main = h.find_parent("div").find_parent("div").find_parent("div")
        if main:
            maintext = main.get_text("\n\n", strip=True)
            outfile = self.store.path(basefile, 'intermediate', '.txt')
            util.writefile(outfile, maintext)
            return TextReader(string=maintext)
        else:
            raise ParseError("%s: Didn't find a suitable header" % basefile)


class SOSFS(MyndFskrBase):
    alias = "sosfs"
    start_url = "http://www.socialstyrelsen.se/sosfs"
    storage_policy = "dir"  # must be able to handle attachments
    download_iterlinks = False

    def _basefile_from_text(self, linktext):
        if linktext:
            m = re.search("SOSFS\s+(\d+:\d+)", linktext)
            if m:
                return self.download_sanitize_basefile(m.group(1))

    @decorators.downloadmax
    def download_get_basefiles(self, source):
        soup = BeautifulSoup(source)
        for td in soup.find_all("td", "col3"):
            txt = td.get_text().strip()
            basefile = self._basefile_from_text(txt)
            if basefile is None:
                continue
            link_el = td.find_previous_sibling("td").a
            link = urljoin(self.start_url, link_el.get("href"))
            if link.startswith("javascript:"):
                continue
            # If a base act has no changes, only type 1 links will be
            # on the front page. If it has any changes, only a type 2
            # link will be on the front page, but type 1 links will be
            # on that subsequent page.
            if txt.startswith("Grundförfattning"):
                # 1) links to HTML pages describing (and linking to) a
                # base act, eg for SOSFS 2014:10
                # http://www.socialstyrelsen.se/publikationer2014/2014-10-12
                yield(basefile, link)
            elif txt.startswith("Konsoliderad"):
                # 2) links to HTML pages containing a consolidated act
                # (with links to type 1 base and change acts), eg for
                # SOSFS 2011:13
                # http://www.socialstyrelsen.se/sosfs/2011-13 - fetch
                # page, yield all type 1 links, also find basefile form
                # element.text
                konsfile = self.store.downloaded_path(
                    basefile, attachment="konsolidering.html")
                if (self.config.refresh or (not os.path.exists(konsfile))):
                    soup = BeautifulSoup(self.session.get(link).text)
                    self.log.debug("%s: Has had changes -- downloading base act and all changes" %
                                   basefile)

                    linkhead = soup.find(text=re.compile(
                        "(Ladda ner eller beställ|Beställ eller ladda ner)"))
                    if linkhead:
                        for link_el in linkhead.find_parent("div").find_all("a"):
                            if '/publikationer' in link_el.get("href"):
                                subbasefile = self._basefile_from_text(link_el.get_text())
                                if subbasefile:
                                    yield(subbasefile,
                                          urljoin(link, link_el.get("href")))
                    else:
                        self.log.warning("%s: Can't find links to base/change"
                                         " acts" % basefile)
                    # then save page itself as grundforf/konsoldering.html
                    self.log.debug("%s: Downloading consolidated version" %
                                   basefile)
                    self.download_if_needed(link, basefile, filename=konsfile)
            elif txt.startswith("Ändringsförfattning"):
                if (self.config.refresh or (
                        not os.path.exists(self.store.downloaded_path(basefile)))):
                    self.log.debug(
                        "%s: Downloading updated consolidated version of base" %
                        basefile)
                    self.log.debug("%s:    first getting %s" % (basefile, link))
                    soup = BeautifulSoup(self.session.get(link).text)
                    konsbasefileregex = re.compile(
                        "Senaste version av SOSFS (?P<basefile>\d+:\d+)")
                    konslinkel = soup.find("a", text=konsbasefileregex)
                    if konslinkel:
                        konsbasefile = self.download_sanitize_basefile(
                            konsbasefileregex.search(
                                konslinkel.text).group("basefile"))
                        konsfile = self.store.downloaded_path(
                            konsbasefile,
                            attachment="konsolidering.html")
                        konslink = urljoin(link, konslinkel.get("href"))
                        self.log.debug(
                            "%s:    now downloading consolidated %s" %
                            (konsbasefile, konslink))
                        self.download_if_needed(konslink, basefile, filename=konsfile)
                    else:
                        self.log.warning(
                            "%s:    Couldn't find link to consolidated version" %
                            basefile)
                yield(basefile, link)

    def download_single(self, basefile, url):
        # the url will be to a HTML landing page. We extract the link
        # to the actual PDF file and then call default impl of
        # download_single in order to update documententry. This'll
        # mean that the orig_url is set to the PDF link, not this HTML
        # landing page.
        soup = BeautifulSoup(self.session.get(url).text)
        link_el = soup.find("a", text=re.compile("^\s*Ladda ner\s*$"))
        if link_el:
            link = urljoin(url, link_el.get("href"))
            return super(SOSFS, self).download_single(basefile, link)
        else:
            self.log.warning("%s: No link to PDF file found at %s" % (basefile, url))
            return False

    def fwdtests(self):
        t = super(SOSFS, self).fwdtests()
        t["dcterms:identifier"] = ['^([A-ZÅÄÖ-]+FS\s\s?\d{4}:\d+)']
        return t

    def parse_metadata_from_textreader(self, reader, doc):
        # cue past the first cover pages until we find the first real page
        page = 1
        try:
            while "Ansvarig utgivare" not in reader.peekchunk('\f'):
                self.log.debug("%s: Skipping cover page %s" % (doc.basefile, page))
                reader.readpage()
                page += 1
        except IOError:   # read past end of file
            raise errors.ParseError("%s: Could not find a proper first page" % doc.basefile)
        return super(SOSFS, self).parse_metadata_from_textreader(reader, doc)


class STAFS(MyndFskrBase):
    alias = "stafs"
    start_url = ("http://www.swedac.se/sv/Det-handlar-om-fortroende/"
                 "Lagar-och-regler/Gallande-foreskrifter-i-nummerordning/")
    basefile_regex = "^STAFS (?P<basefile>\d{4}:\d+)$"
    storage_policy = "dir"

    re_identifier = re.compile('STAFS[ _]+(\d{4}[:/_-]\d+)')

    def download_single(self, basefile, mainurl):
        consolidated_link = None
        try:
            soup = BeautifulSoup(self.session.get(mainurl).text)
            if not soup.find_all("a", text=self.re_identifier):
                self.log.error(
                    "%s: Couldn't find any document links at %s" %
                    (basefile, mainurl))
                return False
            for linkel in soup.find_all("a", text=self.re_identifier):
                url = urljoin(mainurl, linkel.get("href"))
                if "konso" in linkel.text:
                    consolidated_link = url
                else:
                    m = self.re_identifier.search(linkel.text)
                    assert m
                    if url.endswith(".pdf"):
                        newbasefile = self.download_sanitize_basefile(m.group(1))
                        if basefile != newbasefile:
                            # incorrectly labeled file -- no way of
                            # knowing which label is correct
                            self.log.warning(
                                "Expected %s but got %s, skipping this" %
                                (basefile, newbasefile))
                            continue
                        # download directly - but call baseclass
                        # method to ensure DocumentEntry updates
                        super(STAFS, self).download_single(basefile, url)
                    else:
                        # not pdf - link to yet another pg
                        self.log.debug("%s:    Fetching landing page %s" % (basefile, url))
                        subsoup = BeautifulSoup(self.session.get(url).text)
                        for sublink in soup.find_all("a", text=self.re_identifier):
                            m = self.re_identifier.search(sublink.text)
                            assert m
                            suburl = urljoin(url, sublink.get("href"))
                            if suburl.endswith(".pdf"):
                                subbasefile = self.download_sanitize_basefile(m.group(1))
                                self.log.debug("%s:    Downloading change %s from %s" %
                                               (basefile, subbasefile, suburl))
                                self.download_if_needed(suburl, subbasefile)

            if consolidated_link:
                filename = self.store.downloaded_path(
                    basefile,
                    attachment="consolidated.pdf")
                self.log.debug(
                    "%s:    Downloading consolidated  to %s" %
                    (basefile, filename))
                self.download_if_needed(
                    consolidated_link, basefile, filename=filename)
        except requests.exceptions.ConnectionError as e:
            self.log.error(
                "%s: Failure fetching %s (or some sub-URL): %s" %
                (basefile, mainurl, e))


class STFS(MyndFskrBase):
    # (id vs länk)
    alias = "stfs"
    start_url = "http://www.sametinget.se/1014?cat_id=52"


class SvKFS(MyndFskrBase):
    alias = "svkfs"
    start_url = "http://www.svk.se/Tekniska-krav/Foreskrifter/"
