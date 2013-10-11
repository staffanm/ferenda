# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import re
import datetime
import itertools
from rdflib import Graph

from ferenda import DocumentRepository
from ferenda.sources.legal.se.legalref import LegalRef
from ferenda.elements import Paragraph

# FIXME: 2008.json, containing a handful of cases, some which should not be fetched, and one continuation link.
#        A few downloaded/62008CN0028.html (abbreviated)
#        Corresponding parsed/62008CN0028.xhtml and distilled/62008CN0028.ttl


class EurlexCaselaw(DocumentRepository):

    """Handles all case law from the European Court of Justice (ECJ)."""
    alias = "ecj"  # European Court of Justice

    start_url = "http://eur-lex.europa.eu/JURISIndex.do"
    document_url = "http://eur-lex.europa.eu/LexUriServ/LexUriServ.do?uri=CELEX:%(basefile)s:EN:NOT"
    source_encoding = "utf-8"

    namespaces = ('rdf',
                  'dct',
                  ('eurlex', 'http://lagen.nu/eurlex#'))

    # This regexp is specific to caselaw (the leading '6' is for the
    # caselaw area).
    re_celexno = re.compile('(6)(\d{4})(\w\w?)(\d{4})(\(\d{2}\)|)')

    def download(self, basefile=None):
        if basefile:
            self.download_single(basefile)
        if not self.config.force and 'startyear' in self.config:
            startyear = self.config.startyear
        else:
            startyear = 1954  # The first verdicts were published in this year
        for year in range(startyear, datetime.date.today().year + 1):
            # We use self.configfile directly rather than
            # self.moduleconfig, since the latter cannot be persisted
            # across sessions (as it is a subset of a composite
            # between the config file and command line options)
            self.config.startyear = year
            self.config.write()
            # FIXME: URL parameters may have changed -- this seem to produce every
            # case from year up till today
            list_url = "http://eur-lex.europa.eu/Result.do?T1=V6&T2=%d&T3=&RechType=RECH_naturel" % year
            self.log.debug("Searching for %d" % year)
            res = request.get(list_url)
            pagecnt = 0
            done = False
            while not done:
                pagecnt += 1
                self.log.debug("Result page #%s" % pagecnt)
                # Don't parse using BeautifulSoup etc -- just search the whole damn text blob
                celexnos = self.re_celexno.findall(res.text)
                # FIXME: support for config.downloadmax
                for celexno in itertools.chain(celexnos):
                    # the number will be split up in components - concatenate
                    celexno = "".join(celexno)
                    # only download actual judgements and orders
                    # FIXME: the below is outdated -- now "TA" and "CN" (amongst others?) are used

                    # J: Judgment of the Court
                    # A: Judgment of the Court of First Instance
                    # W: Judgement of the Civil Service Tribunal
                    # T: (old) Judgement of the Court
                    # B: Order of the CFI
                    # O: Order of the ECJ
                    if ('J' in celexno or 'A' in celexno
                        or 'W' in celexno or 'T' in celexno
                            or 'B' in celexno or 'O' in celexno):
                        if self.download_single(celexno, usecache=usecache):
                            self.log.info("Downloaded %s" % celexno)
                        else:
                            self.log.info("Skipped %s" % celexno)
                    else:
                        pass
                        #self.log.debug("Not downloading doc %s" % celexno)

                # see if there are any "next" pages
                url = lxml.html.parse(res.text).find("a", text=">").get('href', None)
                if url:
                    res = request.get(url)
                else:
                    self.log.info('No next page link found, we must be done')
                    done = True

    def parse_metadata_from_soup(self, soup, doc):
        # AVAILABLE METADATA IN CASES
        #
        # For now, we create a nonofficial eurlex vocab with namespace http://lagen.nu/eurlex#
        # - celex number (first h1) :celex (:celexnum?)
        #
        # - [Title and reference]
        #   - decision type and date "Judgment of the Court (Third Chamber) of 17 December 2009."
        #      :courtdecision (as opposed to :commissiondecision)
        #   - :party (or parties) "M v Agence européenne des médicaments (EMEA)."
        #   - :referingcourt "Reference for a preliminary ruling: Administrativen sad Sofia-grad - Bulgaria."
        #   - :legalissue - short description and/or(?) keywords (not always present, eg 62009J0403), hyphen sep:
        #     - "Review of the judgment in Case T-12/08 P"
        #     - "Whether the state of the proceedings permits final judgment to be given"
        #     - "Fair hearing"
        #     - "Rule that the parties should be heard"
        #     - "Whether the unity or consistency of Community law is affected."
        #   - :casenum Case number + unknown letters:
        #     - "Case C-197/09 RX-II."
        #     - "Joined cases T-117/03 to T-119/03 and T-171/03."
        #   - :casereporter Case reporter cite "European Court reports 2009 Page 00000"
        # - [Text]
        #   - :availablelang - Available languages ("bg", "es", "cs", "da" ....)
        # - :authenticlang - Authentic language ("fr" or "French")
        # - [Dates]
        #   - :decisiondate - Date of document (decision/judgement)
        #   - :applicationdate - Date of application
        # - [Classifications] (different from description/keywords above)
        #   - :subjectmatter Subject Matter, comma sep:
        #     - "Staff regulations and employment conditions - EC"
        #     - "Provisions governing the Institutions"
        #   - :directorycode - Case Law Directory Code (where is the full code list?), NL sep:
        #      - "B-09.03 EEC/EC / State aid / Exceptions to the prohibition of aid"
        #      - "B-20.05 EEC/EC / Acts of the institutions / Statement of the reasons on which a measure is based"
        #      - "B-09.03 EEC/EC / State aid / Exceptions to the prohibition of aid"
        #      - "B-09.04 EEC/EC / State aid / Review of aid by the Commission - Rules of procedure"
        # - [Miscellaneous information]
        #   - dct:author Author: "Court of Justice of the European Communities"
        #   - :form Form: "Judgement"
        # - [Procedure]
        #   - :proceduretype - Type of procedure, comma sep:
        #     - "Staff cases"
        #     - "Action for damages"
        #     - "Appeal"
        #     - "REEX=OB"
        #   - :applicant - Applicant: "Official"
        #   - :defendant - Defendant: "EMEA, Institutions"
        #   - :observation - Observations: "Italy, Poland, Member States, European Parliament, Council, Commission, Institutions"
        #   - :judgerapporteur - Judge-Rapporteur: "von Danwitz"
        #   - :advocategeneral - Advocate General: "Mazák"
        # - [Relationships between documents]
        #   - :treaty Treaty: "European Communities"
        #   - :caseaffecting Case affecting, NL-sep:
        #     - "Interprets [CELEXNO + pinpoint]"
        #     - "Declares void 61995A0091"
        #     - "Confirms 31996D0666"
        #   - :"Instruments cited in case law" (celex numbers with pinpoint locations?), nl-sep
        #     - "12001C/PRO/02-A61"
        #     - "12001C/PRO/02-NA13P1"
        #     - "31991Q0530-A114"
        #     - "62007K0023"
        #     - "62008A0012"
        #
        # convenience functions -- should not be needed now that we have Describer
        # def add_literal(predicate, literal):
        #     g.add((URIRef(uri),
        #            voc[predicate],
        #            Literal(literal, lang=lang)))
        #
        # def add_celex_object(predicate, celexno):
        #     g.add((URIRef(uri),
        #            voc[predicate],
        #            URIRef("http://lagen.nu/ext/celex/%s" % celexno)))
        #
        # def get_predicate(predicate):
        #     predicates = list(g.objects(URIRef(uri), voc[predicate]))
        #     return predicates != []
        #
        # These are a series of refinments for the "Affecting"
        # relationship. "Cites" doesn't have these (or similar), but
        # "is affected by" has (the inverse properties)
        affects_predicates = {"Interprets": "interprets",
                              "Interprets the judgment":
                              "interpretsJudgment",
                              "Declares void": "declaresVoid",
                              "Confirms": "confirms",
                              "Declares valid (incidentally)":
                              "declaresValidIncidentally",
                              "Declares valid (by a preliminary ruling)":
                              "declaresValidByPreliminaryRuling",
                              "Incidentally declares invalid":
                              "declaresInvalidIncidentally",
                              "Declares invalid (by a preliminary ruling)":
                              "declaresInvalidByPreliminaryRuling",
                              "Amends": "amends",
                              "Failure concerning": "failureConcerning"}

        isaffected_predicates = {"Interpreted by": "interpretedBy",
                                 "Confirmed by": "confirmedBy",
                                 "Declared void by": "declaredVoidBy",
                                 "Annulment requested by":
                                 "annulmentRequestedBy"}

        # 1. Express metadata about our document as a RDF graph
        desc = Describer(self.meta, self.uri)
        g = Graph()
        # :celex - first <h1>
        celexnum = soup.h1.get_text(strip=True)
        if celexnum == "No documents matching criteria.":
            raise errors.DocumentRemovedError("No documents matching criteria " + celexnum)
        elif "no_data_found" in celexnum:
            self.log.warning(
                "%s: No data found (try re-downloading)!" % basefile)
            raise errors.DocumentRemovedError("No data found!")

        assert celexnum == doc.basefile, "Celex number in file (%s) differ from filename (%s)" % (
            celexnum, basefile)
        doc.lang = soup.html['lang']

        m = self.re_celexno.match(celexnum)
        # FIXME: this list is outdated!
        rdftype = {'J': voc['Judgment'],
                   'A': voc['JudgmentFirstInstance'],
                   'W': voc['JudgmentCivilService'],
                   'O': voc['Order'],
                   'B': voc['OrderCivilService']}[m.group(3)]

        desc.rdftype(rdftype)
        desc.value(self.ns['eurlex'].celexnum, celexnum)

        # The first section, following <h2>Title and reference</h2>
        # contains :courtdecision, :party (one or two items),
        # :referingcourt (optional), :legalissue (list of strings),
        # :casenum, :casereporter. Since some are optional, we do a
        # little heuristics to find out what we're looking at at any
        # given moment.
        for section in soup.findAll(["h1", "h2"]):
            if section.name == "h1" and section.a and section.a.string == "Text":
                break
            if section.string == "Title and reference":
                for para in section.findNextSiblings("p"):
                    if not para.string:
                        continue
                    string = para.string.strip()

                    # optional: do sanitychecks to see if this really is a :courtdecision
                    if not get_predicate('courtdecision'):
                        add_literal('courtdecision', string)
                    elif not get_predicate('party'):
                        # this will be one or two items. Are they position dependent?
                        for party in string.split(" v "):
                            add_literal('party', party)
                    elif (not get_predicate('referingcourt') and
                          (string.startswith("Reference for a preliminary ruling") or
                           string.startswith("Preliminary ruling requested"))):
                        add_literal('referingcourt', string)
                    elif (not get_predicate('casenum') and
                          (string.lower().startswith("case ") or
                           string.lower().startswith("joined cases "))):
                        add_literal('casenum', string)
                    elif para.em:  # :casereporter is enclosed in an em
                        for row in para.findAll(text=True):
                            add_literal('casereporter', row.strip())
                    elif get_predicate('legalissue'):
                        # fixme: Split this up somehow
                        add_literal('legalissue', string)
            elif section.string == "Relationship between documents":
                for item in section.findNextSibling("ul").findAll("li"):
                    predicate = None
                    subpredicate = None
                    for node in item.childGenerator():
                        if not hasattr(node, "name"):
                            nodetext = node.strip()
                            if re.match("([ABCDEFGIJKLNPRST]+\d*)+$", nodetext):
                                continue
                            if re.match("\d[\d\-]*[ABC]?$", nodetext):
                                continue
                            if predicate == "affects" and nodetext:
                                if nodetext in affects_predicates:
                                    subpredicate = affects_predicates[nodetext]
                                else:
                                    self.log.warning(
                                        "Can't express '%s' as a affects predicate" % nodetext)
                            elif predicate == "isaffected" and nodetext:
                                if nodetext in isaffected_predicates:
                                    subpredicate = isaffected_predicates[
                                        nodetext]
                                else:
                                    self.log.warning(
                                        "Can't express '%s' as a isaffected predicate" % nodetext)

                        elif node.name == "strong":
                            subpredicate = None
                            if node.string == "Treaty:":
                                predicate = "treaty"
                            elif node.string == "Affected by case:":
                                predicate = "isaffected"
                            elif node.string == "Case affecting:":
                                predicate = "affects"
                            elif node.string == "Instruments cited in case law:":
                                predicate = "cites"
                            else:
                                self.log.warning("Don't know how to handle key '%s'" % node.string)
                        elif node.name == "a" and predicate:
                            p = predicate
                            if subpredicate:
                                p = subpredicate
                            # FIXME: If the
                            # predicate is "cites", the celex number
                            # may have extra crap
                            # (eg. "31968R0259(01)-N2A1L6") indicating
                            # pinpoint location. Transform these to a
                            # fragment identifier.
                            add_celex_object(p, node.string.strip())

    def parse_document_from_soup(self, soup, doc):
        # Process text and create DOM
        self.parser = LegalRef(LegalRef.EGRATTSFALL)

        textdiv = soup.find("div", "texte")
        if textdiv:
            for node in textdiv.childGenerator():
                if node.string:
                    # Here we should start analyzing for things like
                    # "C-197/09". Note that the Eurlex data does not use
                    # the ordinary hyphen like above, but rather
                    # 'NON-BREAKING HYPHEN' (U+2011) - LegaRef will mangle
                    # this to an ordinary hyphen.
                    subnodes = self.parser.parse(node.string,
                                                 predicate="dct:references")
                    doc.body.append(Paragraph(subnodes))
        else:
            self.log.warning("%s: No fulltext available!" % celexnum)
            doc.body.append(Paragraph(["(No fulltext available)"]))
