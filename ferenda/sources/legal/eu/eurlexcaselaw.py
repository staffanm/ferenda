#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
import sys
import os
import re
import datetime
import codecs
import itertools 
# Assume RDFLib 3.0
from rdflib import Namespace, URIRef, Literal, RDF, Graph

try:
    from whoosh.index import create_in, open_dir, exists_in
    from whoosh.fields import Schema, TEXT, ID, KEYWORD, STORED
    from whoosh.analysis import StemmingAnalyzer
    from whoosh.filedb.multiproc import MultiSegmentWriter
    whoosh_available = True
except ImportError:
    whoosh_available = False

from ferenda import DocumentRepository
from ferenda.errors import ParseError
from ferenda import util
from ferenda import legaluri
from ferenda.legalref import LegalRef, Link
from ferenda.elements import UnicodeElement, CompoundElement, Paragraph


__version__ = (1, 6)
__author__ = "Staffan Malmgren <staffan@tomtebo.org>"


class Body(CompoundElement):
    pass


class ListItem(CompoundElement):
    pass  # needed for generic render_xhtml


class EurlexCaselaw(DocumentRepository):
    module_dir = "ecj"  # European Court of Justice

    start_url = "http://eur-lex.europa.eu/JURISIndex.do"
    document_url = "http://eur-lex.europa.eu/LexUriServ/LexUriServ.do?uri=CELEX:%s:EN:NOT"
    vocab_url = "http://lagen.nu/eurlex#"
    source_encoding = "utf-8"

    # This regexp is specific to caselaw (the leading '6' is for the
    # caselaw area).
    re_celexno = re.compile('(6)(\d{4})(\w)(\d{4})(\(\d{2}\)|)')

    def download_everything(self, usecache=False):
        self.log.debug("Downloading, usecache is %s" % usecache)
        if usecache and 'startyear' in self.moduleconfig:
            startyear = int(self.moduleconfig['startyear'])
        else:
            startyear = 1954  # The first verdicts were published in this year
        for year in range(startyear, datetime.date.today().year + 1):
            # We use self.configfile directly rather than
            # self.moduleconfig, since the latter cannot be persisted
            # across sessions (as it is a subset of a composite
            # between the config file and command line options)

            # FIXME: Avoid hardcoding the module name like this
            self.configfile['ferenda.sources.EurlexCaselaw'][
                'startyear'] = year

            self.configfile.write()
            list_url = "http://eur-lex.europa.eu/Result.do?T1=V6&T2=%d&T3=&RechType=RECH_naturel" % year
            self.log.debug("Searching for %d" % year)
            self.browser.open(list_url)
            pagecnt = 0
            done = False
            while not done:
                pagecnt += 1
                self.log.debug("Result page #%s" % pagecnt)
                # For some reason, Mechanize can't find the link to
                # the HTML version of the case text. So we just get
                # the whole page as a string and find unique CELEX ids
                # in the tagsoup.
                pagetext = self.browser.response().read()
                celexnos = self.re_celexno.findall(pagetext)
                for celexno in itertools.chain(celexnos):
                #for celexno in util.unique_list(celexnos):
                    # the number will be split up in components - concatenate
                    celexno = "".join(celexno)
                    # only download actual judgements and orders
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
                try:
                    self.browser.follow_link(text='>')
                except LinkNotFoundError:
                    self.log.info('No next page link found, we must be done')
                    done = True

    @classmethod
    def basefile_from_path(cls, path):
        seg = os.path.splitext(path)[0].split(os.sep)
        return "/".join(seg[seg.index(cls.module_dir) + 3:])

    def generic_path(self, basefile, maindir, suffix):
        m = self.re_celexno.match(basefile)
        year = m.group(2)
        return os.path.sep.join([self.base_dir, self.module_dir, maindir, year, basefile + suffix])

    def parse_from_soup(self, soup, basefile):
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

        # convenience nested functions
        def add_literal(predicate, literal):
            g.add((URIRef(uri),
                   voc[predicate],
                   Literal(literal, lang=lang)))

        def add_celex_object(predicate, celexno):
            g.add((URIRef(uri),
                   voc[predicate],
                   URIRef("http://lagen.nu/ext/celex/%s" % celexno)))

        def get_predicate(predicate):
            predicates = list(g.objects(URIRef(uri), voc[predicate]))
            return predicates != []

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
        g = Graph()
        voc = Namespace(self.vocab_url)
        g.bind('dct', self.ns['dct'])
        g.bind('eurlex', voc)
        # :celex - first <h1>
        celexnum = util.element_text(soup.h1)
        if celexnum == "No documents matching criteria.":
            raise ParseError('"' + celexnum + '"')
        elif "no_data_found" in celexnum:
            self.log.warning(
                "%s: No data found (try re-downloading)!" % basefile)
            raise Exception("No data found!")

        assert celexnum == basefile, "Celex number in file (%s) differ from filename (%s)" % (celexnum, basefile)
        lang = soup.html['lang']
        # 1.1 Create canonical URI for our document. To keep things
        # simple, let's use the celex number as the basis (in the
        # future, we should extend LegalURI to do it)
        uri = "http://lagen.nu/ext/celex/%s" % celexnum

        m = self.re_celexno.match(celexnum)
        rdftype = {'J': voc['Judgment'],
                   'A': voc['JudgmentFirstInstance'],
                   'W': voc['JudgmentCivilService'],
                   'O': voc['Order'],
                   'B': voc['OrderCivilService']}[m.group(3)]

        g.add((URIRef(uri), RDF.type, rdftype))

        add_literal('celexnum', celexnum)

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

                    if not get_predicate('courtdecision'):  # optional: do sanitychecks to see if this really is a :courtdecision
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
                    pass
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
                                    self.log.warning("Can't express '%s' as a affects predicate" % nodetext)
                            elif predicate == "isaffected" and nodetext:
                                if nodetext in isaffected_predicates:
                                    subpredicate = isaffected_predicates[
                                        nodetext]
                                else:
                                    self.log.warning("Can't express '%s' as a isaffected predicate" % nodetext)

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

        # Process text and create DOM
        self.parser = LegalRef(LegalRef.EGRATTSFALL)
        body = Body()

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
                    body.append(Paragraph(subnodes))
        else:
            self.log.warning("%s: No fulltext available!" % celexnum)

        return {'meta': g,
                'body': body,
                'lang': 'en',
                'uri': uri}

    @classmethod
    def relate_all_setup(cls, config):
        # FIXME: Avoid hardcoding the module name
        if ('whooshindexing' in config['ferenda.sources.EurlexCaselaw'] and
                config['ferenda.sources.EurlexCaselaw']['whooshindexing'] == 'False'):
            print("Not indexing document text")
        else:
            print("Indexing document text")
            cls.whoosh_index(config)
        super(EurlexCaselaw, cls).relate_all_setup(config)

    @classmethod
    def whoosh_index(cls, config):

        # FIXME: copied from analyze_article_citations
        sameas = Graph()
        sameas_rdf = util.relpath(
            os.path.dirname(__file__) + "/../res/eut/sameas.n3")
        sameas.load(sameas_rdf, format="n3")
        equivs = {}
        pred = util.ns['owl'] + "sameAs"
        for (s, o) in sameas.subject_objects(URIRef(pred)):
            equivs[str(o)] = str(s)

        indexdir = os.path.sep.join(
            [config['datadir'], cls.module_dir, 'index'])
        basefiles = cls.list_basefiles_for("relate_all", config['datadir'])

        if not exists_in(indexdir):
            print("Creating whoosh index from scratch")
            cls.whoosh_index_create(
                config['datadir'], indexdir, basefiles, equivs)
        else:
            print("Incrementally updating whoosh index")
            cls.whoosh_index_update(
                config['datadir'], indexdir, basefiles, equivs)

    @classmethod
    def whoosh_index_create(cls, basedir, indexdir, basefiles, equivs):
        stemmer = StemmingAnalyzer()
        schema = Schema(title=TEXT,
                        basefile=ID(unique=True, stored=True),
                        articles=KEYWORD(stored=True),
                        updated=STORED,
                        content=TEXT(analyzer=stemmer))

        if not os.path.exists(indexdir):
            os.mkdir(indexdir)
        ix = create_in(indexdir, schema)
        # writer = MultiSegmentWriter(ix,procs=4,limitmb=128)
        writer = ix.writer(limitmb=256)
        stemmer = writer.schema["content"].analyzer
        stemmer.cachesize = -1
        stemmer.clean()

        from time import time
        for basefile in basefiles:
            cls.whoosh_index_add(basedir, basefile, writer, equivs)

        print("Comitting")
        writer.commit()

    @classmethod
    def whoosh_index_update(cls, basedir, indexdir, basefiles, equivs):
        # adapted from
        # http://packages.python.org/Whoosh/indexing.html#incremental-indexing
        ix = open_dir(indexdir)
        searcher = ix.searcher()
        indexed_basefiles = set()
        to_index = set()
        # writer = ix.writer(procs=4,limitmb=128)
        writer = ix.writer(limitmb=256)
        stemmer = writer.schema["content"].analyzer
        stemmer.cachesize = -1
        stemmer.clean()

        for fields in searcher.all_stored_fields():
            print(("Available fields: %r" % fields))

            indexed_basefile = fields['basefile']
            indexed_basefiles.add(indexed_basefile)
            m = cls.re_celexno.match(indexed_basefile)
            year = m.group(2)
            parsed_file = os.path.sep.join([basedir, cls.module_dir, 'parsed',
                                           year, indexed_basefile + '.xhtml'])
            if not os.path.exists(parsed_file):
                writer.delete_by_term('basefile', indexed_basefile)
                print(("Removing %s" % indexed_basefile))
            else:
                indexed_time = fields['updated']
                mtime = os.path.getmtime(parsed_file)
                if mtime > indexed_time:
                    writer.delete_by_term('basefile', indexed_basefile)
                    to_index.add(indexed_basefile)
                    print(("Updating %s" % indexed_basefile))

        for basefile in basefiles:
            if basefile in to_index or basefile not in indexed_basefiles:
                cls.whoosh_index_add(basedir, basefile, writer, equivs)

        print("Comitting")
        writer.commit()

    re_remove_comments = re.compile(r'<!--.*?-->').sub
    re_remove_tags = re.compile(r'<.*?>').sub

    @classmethod
    def whoosh_index_add(cls, basedir, basefile, writer, equivs):
        from time import time
        readstart = time()

        # just save the text from the document, strip out the tags
        m = cls.re_celexno.match(basefile)
        year = m.group(2)
        parsed_file = os.path.sep.join(
            [basedir, cls.module_dir, 'parsed', year, basefile + '.xhtml'])
        text = codecs.open(parsed_file, encoding='utf-8').read()
        #text = text[150:]
        text = util.normalize_space(
            cls.re_remove_tags(' ', cls.re_remove_comments('', text)))

        # Add all cited celex numbers as keywords (translating
        # them to Lisbon numbering if needed)
        distilled_file = os.path.sep.join(
            [basedir, cls.module_dir, 'distilled', year, basefile + '.rdf'])
        distilled_graph = Graph()
        distilled_graph.parse(distilled_file, format="xml")

        articles = []
        pred = util.ns['eurlex'] + "cites"
        for (s, o) in distilled_graph.subject_objects(URIRef(pred)):
            try:
                celex = str(o)
                if celex in equivs:
                    articles.append(equivs[celex])
                elif "celex/12008" in celex:
                    celex = celex.split("-")[0]
                    articles.append(celex)
            except:
                print(("WARNING: weird cite URI %r" % str(o)))

        if text:
            indexstart = time()
            writer.add_document(title="Case " + basefile,
                                basefile=basefile,
                                articles=" ".join(articles),
                                updated=os.path.getmtime(parsed_file),
                                content=text)
            print(("Added %s %r...%r %s art, %.2f kb in %.3f + %.3f s" % (basefile, text[:19], text[-20:], len(articles), float(len(text)) / 1024, indexstart - readstart, time() - indexstart)))
        else:
            print(("Noadd %s (no text)" % (basefile)))

    @classmethod
    def tabs(cls, primary=False):
        return [['EU law', '/eu/']]


if __name__ == "__main__":
    EurlexCaselaw.run()
