from .elements import *

re_SimpleSfsId = re.compile(r'(\d{4}:\d+)\s*$')
re_SearchSfsId = re.compile(r'\((\d{4}:\d+)\)').search
re_ChangeNote = re.compile(r'(Lag|Förordning) \(\d{4}:\d+\)\.?$')
re_ChapterId = re.compile(r'^(\d+( \w|)) [Kk][Aa][Pp]\.').match
re_DivisionId = re.compile(r'^AVD. ([IVX]*)').match
re_SectionId = re.compile(
   r'^(\d+ ?\w?) \xa7[ \.]')  # used for both match+sub
re_SectionIdOld = re.compile(
    r'^\xa7 (\d+ ?\w?).')     # as used in eg 1810:0926
re_DottedNumber = re.compile(r'^(\d+ ?\w?)\. ')
re_Bullet = re.compile(r'^(\-\-?|\x96) ')
re_NumberRightPara = re.compile(r'^(\d+)\) ').match
re_Bokstavslista = re.compile(r'^(\w)\) ')
re_ElementId = re.compile(
    r'^(\d+) mom\.')        # used for both match+sub
re_ChapterRevoked = re.compile(
    r'^(\d+( \w|)) [Kk]ap. (upphävd|har upphävts) genom (förordning|lag) \([\d\:\. s]+\)\.?$').match
re_SectionRevoked = re.compile(
    r'^(\d+ ?\w?) \xa7[ \.]([Hh]ar upphävts|[Nn]y beteckning (\d+ ?\w?) \xa7) genom ([Ff]örordning|[Ll]ag) \([\d\:\. s]+\)\.$').match
re_RevokeDate = re.compile(
    r'/(?:Rubriken u|U)pphör att gälla U:(\d+)-(\d+)-(\d+)/')
re_RevokeAuthorization = re.compile(
    r'/Upphör att gälla U:(den dag regeringen bestämmer)/')
re_EntryIntoForceDate = re.compile(
    r'/(?:Rubriken t|T)räder i kraft I:(\d+)-(\d+)-(\d+)/')
re_EntryIntoForceAuthorization = re.compile(
    r'/Träder i kraft I:(den dag regeringen bestämmer)/')
re_dehyphenate = re.compile(r'\b- (?!(och|eller))', re.UNICODE).sub
re_definitions = re.compile(
    r'^I (lagen|förordningen|balken|denna lag|denna förordning|denna balk|denna paragraf|detta kapitel) (avses med|betyder|används följande)').match
re_brottsdef = re.compile(
    r'\b(döms|dömes)(?: han)?(?:,[\w\xa7 ]+,)? för ([\w ]{3,50}) till (böter|fängelse)', re.UNICODE).search
re_brottsdef_alt = re.compile(
    r'[Ff]ör ([\w ]{3,50}) (döms|dömas) till (böter|fängelse)', re.UNICODE).search
re_parantesdef = re.compile(r'\(([\w ]{3,50})\)\.', re.UNICODE).search
re_loptextdef = re.compile(
    r'^Med ([\w ]{3,50}) (?:avses|förstås) i denna (förordning|lag|balk)', re.UNICODE).search

# use this custom matcher to ensure any strings you intend to convert
# are legal roman numerals (simpler than having from_roman throwing
# an exception)
re_roman_numeral_matcher = re.compile(
    '^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$').match


def make_parser(self):

    def makeForfattning(self):
        while self.reader.peekline() == "":
            self.reader.readline()

        self.log.debug('Första raden \'%s\'' % self.reader.peekline())
        (line, upphor, ikrafttrader) = self.andringsDatum(
            self.reader.peekline())
        if ikrafttrader:
            self.log.debug(
                'Författning med ikraftträdandedatum %s' % ikrafttrader)

            b = Forfattning(ikrafttrader=ikrafttrader,
                            uri=self.canonical_uri(self.id))
            self.reader.readline()
        else:
            self.log.debug('Författning utan ikraftträdandedatum')
            b = Forfattning(uri=self.canonical_uri(self.id))

        while not self.reader.eof():
            state_handler = self.guess_state()
            # special case - if a Overgangsbestammelse is encountered
            # without the preceeding headline (which would normally
            # set state_handler to makeOvergangsbestammelser (notice
            # the plural)
            if state_handler == self.makeOvergangsbestammelse:
                res = self.makeOvergangsbestammelser(rubrik_saknas=True)
            else:
                res = state_handler()
            if res is not None:
                b.append(res)
        return b

    def makeAvdelning(self):
        avdelningsnummer = self.idOfAvdelning()
        p = Avdelning(rubrik=self.reader.readline(),
                      ordinal=avdelningsnummer,
                      underrubrik=None)
        if (self.reader.peekline(1) == "" and
            self.reader.peekline(3) == "" and
                not self.isKapitel(self.reader.peekline(2))):
            self.reader.readline()
            p.underrubrik = self.reader.readline()

        self.log.debug("  Ny avdelning: '%s...'" % p.rubrik[:30])

        while not self.reader.eof():
            state_handler = self.guess_state()

            if state_handler in (self.makeAvdelning,  # Strukturer som signalerar att denna avdelning är slut
                                 self.makeOvergangsbestammelser,
                                 self.makeBilaga):
                self.log.debug("  Avdelning %s färdig" % p.ordinal)
                return p
            else:
                res = state_handler()
                if res is not None:
                    p.append(res)
        # if eof is reached
        return p

    def makeUpphavtKapitel(self):
        kapitelnummer = self.idOfKapitel()
        c = UpphavtKapitel(self.reader.readline(),
                           ordinal=kapitelnummer)
        self.log.debug("  Upphävt kapitel: '%s...'" % c[:30])

        return c

    def makeKapitel(self):
        kapitelnummer = self.idOfKapitel()

        para = self.reader.readparagraph()
        (line, upphor, ikrafttrader) = self.andringsDatum(para)

        kwargs = {'rubrik': util.normalize_space(line),
                  'ordinal': kapitelnummer}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        k = Kapitel(**kwargs)
        self.current_headline_level = 0
        self.current_section = '0'

        self.log.debug("    Nytt kapitel: '%s...'" % line[:30])

        while not self.reader.eof():
            state_handler = self.guess_state()

            if state_handler in (self.makeKapitel,  # Strukturer som signalerar slutet på detta kapitel
                                 self.makeUpphavtKapitel,
                                 self.makeAvdelning,
                                 self.makeOvergangsbestammelser,
                                 self.makeBilaga):
                self.log.debug("    Kapitel %s färdigt" % k.ordinal)
                return (k)
            else:
                res = state_handler()
                if res is not None:
                    k.append(res)
        # if eof is reached
        return k

    def makeRubrik(self):
        para = self.reader.readparagraph()
        (line, upphor, ikrafttrader) = self.andringsDatum(para)
        self.log.debug("      Ny rubrik: '%s...'" % para[:30])

        kwargs = {}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        if self.current_headline_level == 2:
            kwargs['type'] = 'underrubrik'
        elif self.current_headline_level == 1:
            self.current_headline_level = 2

        h = Rubrik(line, **kwargs)
        return h

    def makeUpphavdParagraf(self):
        paragrafnummer = self.idOfParagraf(self.reader.peekline())
        p = UpphavdParagraf(self.reader.readline(),
                            ordinal=paragrafnummer)
        self.current_section = paragrafnummer
        self.log.debug("      Upphävd paragraf: '%s...'" % p[:30])
        return p

    def makeParagraf(self):
        paragrafnummer = self.idOfParagraf(self.reader.peekline())
        self.current_section = paragrafnummer
        firstline = self.reader.peekline()
        self.log.debug("      Ny paragraf: '%s...'" % firstline[:30])
        # Läs förbi paragrafnumret:
        self.reader.read(len(paragrafnummer) + len(' \xa7 '))

        # some really old laws have sections split up in "elements"
        # (moment), eg '1 \xa7 1 mom.', '1 \xa7 2 mom.' etc
        match = self.re_ElementId.match(firstline)
        if self.re_ElementId.match(firstline):
            momentnummer = match.group(1)
            self.reader.read(len(momentnummer) + len(' mom. '))
        else:
            momentnummer = None

        (fixedline, upphor, ikrafttrader) = self.andringsDatum(firstline)
        # Läs förbi '/Upphör [...]/' och '/Ikraftträder [...]/'-strängarna
        self.reader.read(len(firstline) - len(fixedline))
        kwargs = {'ordinal': paragrafnummer}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader

        if momentnummer:
            kwargs['moment'] = momentnummer

        p = Paragraf(**kwargs)

        state_handler = self.makeStycke
        res = self.makeStycke()
        p.append(res)

        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler in (self.makeParagraf,
                                 self.makeUpphavdParagraf,
                                 self.makeKapitel,
                                 self.makeUpphavtKapitel,
                                 self.makeAvdelning,
                                 self.makeRubrik,
                                 self.makeOvergangsbestammelser,
                                 self.makeBilaga):
                self.log.debug("      Paragraf %s färdig" % paragrafnummer)
                return p
            elif state_handler == self.blankline:
                state_handler()  # Bara att slänga bort
            elif state_handler == self.makeOvergangsbestammelse:
                self.log.debug("      Paragraf %s färdig" % paragrafnummer)
                self.log.warning(
                    "%s: Avskiljande rubrik saknas mellan författningstext och övergångsbestämmelser" % self.id)
                return p
            else:
                assert state_handler == self.makeStycke, "guess_state returned %s, not makeStycke" % state_handler.__name__
                # if state_handler != self.makeStycke:
                #    self.log.warning("behandlar '%s...' som stycke, inte med %s" % (self.reader.peekline()[:30], state_handler.__name__))
                res = self.makeStycke()
                p.append(res)

        # eof occurred
        return p

    def makeStycke(self):
        self.log.debug(
            "        Nytt stycke: '%s...'" % self.reader.peekline()[:30])
        s = Stycke([util.normalize_space(self.reader.readparagraph())])
        while not self.reader.eof():
            #self.log.debug("            makeStycke: calling guess_state ")
            state_handler = self.guess_state()
            #self.log.debug("            makeStycke: guess_state returned %s " % state_handler.__name__)
            if state_handler in (self.makeNumreradLista,
                                 self.makeBokstavslista,
                                 self.makeStrecksatslista,
                                 self.makeTabell):
                res = state_handler()
                s.append(res)
            elif state_handler == self.blankline:
                state_handler()  # Bara att slänga bort
            else:
                #self.log.debug("            makeStycke: ...we're done")
                return s
        return s

    def makeNumreradLista(self):
        n = NumreradLista()
        while not self.reader.eof():
            # Utgå i första hand från att nästa stycke är ytterligare
            # en listpunkt (vissa tänkbara stycken kan även matcha
            # tabell m.fl.)
            if self.isNumreradLista():
                state_handler = self.makeNumreradLista
            else:
                state_handler = self.guess_state()

            if state_handler not in (self.blankline,
                                     self.makeNumreradLista,
                                     self.makeBokstavslista,
                                     self.makeStrecksatslista):
                return n
            elif state_handler == self.blankline:
                state_handler()
            else:
                if state_handler == self.makeNumreradLista:
                    self.log.debug("          Ny punkt: '%s...'" %
                                   self.reader.peekline()[:30])
                    listelement_ordinal = self.idOfNumreradLista()
                    li = Listelement(ordinal=listelement_ordinal)
                    p = self.reader.readparagraph()
                    li.append(p)
                    n.append(li)
                else:
                    # this must be a sublist
                    res = state_handler()
                    n[-1].append(res)
                self.log.debug(
                    "          Punkt %s avslutad" % listelement_ordinal)
        return n

    def makeBokstavslista(self):
        n = Bokstavslista()
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler not in (self.blankline, self.makeBokstavslista):
                return n
            elif state_handler == self.blankline:
                state_handler()
            else:
                self.log.debug("            Ny underpunkt: '%s...'" %
                               self.reader.peekline()[:30])
                listelement_ordinal = self.idOfBokstavslista()
                li = Listelement(ordinal=listelement_ordinal)
                p = self.reader.readparagraph()
                li.append(p)
                n.append(li)
                self.log.debug("            Underpunkt %s avslutad" %
                               listelement_ordinal)
        return n

    def makeStrecksatslista(self):
        n = Strecksatslista()
        cnt = 0
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler not in (self.blankline, self.makeStrecksatslista):
                return n
            elif state_handler == self.blankline:
                state_handler()
            else:
                self.log.debug("            Ny strecksats: '%s...'" %
                               self.reader.peekline()[:60])
                cnt += 1
                p = self.reader.readparagraph()
                li = Listelement(ordinal=str(cnt))
                li.append(p)
                n.append(li)
                self.log.debug("            Strecksats #%s avslutad" % cnt)
        return n

    def blankline(self):
        self.reader.readline()
        return None

    def eof(self):
        return None

    # svenska: övergångsbestämmelser
    def makeOvergangsbestammelser(self, rubrik_saknas=False):
        # det kan diskuteras om dessa ska ses som en del av den
        # konsoliderade lagtexten öht, men det verkar vara kutym att
        # ha med åtminstone de som kan ha relevans för gällande rätt
        self.log.debug("    Ny Övergångsbestämmelser")

        if rubrik_saknas:
            rubrik = "[Övergångsbestämmelser]"
        else:
            rubrik = self.reader.readparagraph()
        obs = Overgangsbestammelser(rubrik=rubrik)

        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler == self.makeBilaga:
                return obs

            res = state_handler()
            if res is not None:
                if state_handler != self.makeOvergangsbestammelse:
                    # assume these are the initial Övergångsbestämmelser
                    if hasattr(self, 'id'):
                        sfsnr = self.id
                        self.log.warning(
                            "%s: Övergångsbestämmelsen saknar SFS-nummer - antar %s" % (self.id, sfsnr))
                    else:
                        sfsnr = '0000:000'
                        self.log.warning(
                            "(unknown): Övergångsbestämmelsen saknar ett SFS-nummer - antar %s" % (sfsnr))

                    obs.append(Overgangsbestammelse([res], sfsnr=sfsnr))
                else:
                    obs.append(res)

        return obs

    def makeOvergangsbestammelse(self):
        p = self.reader.readline()
        self.log.debug("      Ny Övergångsbestämmelse: %s" % p)
        ob = Overgangsbestammelse(sfsnr=p)
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler in (self.makeOvergangsbestammelse,
                                 self.makeBilaga):
                return ob
            res = state_handler()
            if res is not None:
                ob.append(res)

        return ob

    def makeBilaga(self):  # svenska: bilaga
        rubrik = self.reader.readparagraph()
        (rubrik, upphor, ikrafttrader) = self.andringsDatum(rubrik)

        kwargs = {'rubrik': rubrik}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        b = Bilaga(**kwargs)
        self.log.debug("    Ny bilaga: %s" % rubrik)
        while not self.reader.eof():
            state_handler = self.guess_state()
            if state_handler in (self.makeBilaga,
                                 self.makeOvergangsbestammelser):
                return b
            res = state_handler()
            if res is not None:
                b.append(res)
        return b

    def andringsDatum(self, line, match=False):
        # Hittar ändringsdatumdirektiv i line. Om match, matcha från strängens
        # början, annars sök i hela strängen.
        dates = {'ikrafttrader': None,
                 'upphor': None}

        for (regex, key) in list({self.re_RevokeDate: 'upphor',
                                  self.re_RevokeAuthorization: 'upphor',
                                  self.re_EntryIntoForceDate: 'ikrafttrader',
                                  self.re_EntryIntoForceAuthorization: 'ikrafttrader'}.items()):
            if match:
                m = regex.match(line)
            else:
                m = regex.search(line)
            if m:
                try:
                    if len(m.groups()) == 3:
                        dates[key] = datetime(int(m.group(1)),
                                              int(m.group(2)),
                                              int(m.group(3)))
                    else:
                        dates[key] = m.group(1)
                    line = regex.sub('', line)
                except ValueError:  # eg if datestring was
                                   # "2014-081-01" or something
                                   # similarly invalid - result in no
                                   # match, eg unaffected line
                    pass

        return (line.strip(), dates['upphor'], dates['ikrafttrader'])

    def guess_state(self):
        # sys.stdout.write("        Guessing for '%s...'" % self.reader.peekline()[:30])
        try:
            if self.reader.peekline() == "":
                handler = self.blankline
            elif self.isAvdelning():
                handler = self.makeAvdelning
            elif self.isUpphavtKapitel():
                handler = self.makeUpphavtKapitel
            elif self.isUpphavdParagraf():
                handler = self.makeUpphavdParagraf
            elif self.isKapitel():
                handler = self.makeKapitel
            elif self.isParagraf():
                handler = self.makeParagraf
            elif self.isTabell():
                handler = self.makeTabell
            elif self.isOvergangsbestammelser():
                handler = self.makeOvergangsbestammelser
            elif self.isOvergangsbestammelse():
                handler = self.makeOvergangsbestammelse
            elif self.isBilaga():
                handler = self.makeBilaga
            elif self.isNumreradLista():
                handler = self.makeNumreradLista
            elif self.isStrecksatslista():
                handler = self.makeStrecksatslista
            elif self.isBokstavslista():
                handler = self.makeBokstavslista
            elif self.isRubrik():
                handler = self.makeRubrik
            else:
                handler = self.makeStycke
        except IOError:
            handler = self.eof
        # sys.stdout.write("%r\n" % handler)
        return handler

    def isAvdelning(self):
        # The start of a part ("avdelning") should be a single line
        if '\n' in self.reader.peekparagraph() != "":
            return False

        return self.idOfAvdelning() is not None

    def idOfAvdelning(self):
        # There are four main styles of parts ("Avdelning") in swedish law
        #
        # 1998:808: "FÖRSTA AVDELNINGEN\n\nÖVERGRIPANDE BESTÄMMELSER"
        #  (also in 1932:130, 1942:740, 1956:623, 1957:297, 1962:381, 1962:700,
        #   1970:988, 1970:994, 1971:235 (revoked), 1973:370 (revoked),
        #   1977:263 (revoked), 1987:230, 1992:300 (revoked), 1994:200,
        #   1998:674, 2000:192, 2005:104 and 2007:528 -- not always in all
        #   uppercase. However, the initial line "FÖRSTA AVDELNININGEN"
        #   (in any casing) is always followed by another line that
        #   describes/labels the part.)
        #
        # 1979:1152: "Avd. 1. Bestämmelser om taxering av fastighet"
        #  (also in 1979:1193 (revoked))
        #
        # 1994:1009: "Avdelning I Fartyg"
        #
        # 1999:1229: "AVD. I INNEH\XE5LL OCH DEFINITIONER"
        #
        # 2009:400: "AVDELNING I. INLEDANDE BESTÄMMELSER"
        #
        # and also "1 avd." (in 1959:287 (revoked), 1959:420 (revoked)
        #
        #  The below code checks for all these patterns in turn
        #
        # The variant "Avdelning 1" has also been found, but only in
        # appendixes
        p = self.reader.peekline()
        if p.lower().endswith("avdelningen") and len(p.split()) == 2:
            ordinal = p.split()[0]
            return str(self._swedish_ordinal(ordinal))
        elif p.startswith("AVD. ") or p.startswith("AVDELNING "):
            roman = re.split(r'\s+', p)[1]
            if roman.endswith("."):
                roman = roman[:-1]
            if self.re_roman_numeral_matcher(roman):
                return str(util.from_roman(roman))
        elif p.startswith("Avdelning "):
            roman = re.split(r'\s+', p)[1]
            if self.re_roman_numeral_matcher(roman):
                return str(util.from_roman(roman))
        elif p[2:6] == "avd.":
            if p[0].isdigit():
                return p[0]
        elif p.startswith("Avd. "):
            idstr = re.split(r'\s+', p)[1]
            if idstr.isdigit():
                return idstr
        return None

    def isUpphavtKapitel(self):
        match = self.re_ChapterRevoked(self.reader.peekline())
        return match is not None

    def isKapitel(self, p=None):
        return self.idOfKapitel(p) is not None

    def idOfKapitel(self, p=None):
        if not p:
            p = self.reader.peekparagraph().replace("\n", " ")

        # '1 a kap.' -- almost always a headline, regardless if it
        # streches several lines but there are always special cases
        # (1982:713 1 a kap. 7 \xa7)
        #m = re.match(r'^(\d+( \w|)) [Kk]ap.',p)
        m = self.re_ChapterId(p)
        if m:
            # even though something might look like the start of a chapter, it's often just the
            # start of a paragraph in a section that lists the names of chapters. These following
            # attempts to filter these out by looking for some typical line endings
            # for those cases
            if (p.endswith(",") or
                p.endswith(";") or
                # p.endswith(")") or  # but in some cases, a chapter actually ends in ),
                # eg 1932:131
                # in unlucky cases, a chapter heading might span two lines in a way that
                # the first line ends with "och" (eg 1998:808 kap. 3)
                p.endswith(" och") or
                p.endswith(" om") or
                p.endswith(" samt") or
                (p.endswith(".") and not
                 (m.span()[1] == len(p) or  # if the ENTIRE p is eg "6 kap." (like it is in 1962:700)
                  p.endswith(" m.m.") or
                  p.endswith(" m. m.") or
                  p.endswith(" m.fl.") or
                  p.endswith(" m. fl.") or
                  self.re_ChapterRevoked(p)))):  # If the entire chapter's
                                           # been revoked, we still
                                           # want to count it as a
                                           # chapter

                # sys.stdout.write("chapter_id: '%s' failed second check" % p)
                return None

            # sometimes (2005:1207) it's a headline, referencing a
            # specific section somewhere else - if the "1 kap. " is
            # immediately followed by "5 \xa7 " then that's probably the
            # case
            if (p.endswith(" \xa7") or
                p.endswith(" \xa7\xa7") or
                    (p.endswith(" stycket") and " \xa7 " in p)):
                return None

            # Om det ser ut som en tabell är det nog ingen
            # kapitelrubrik -- borttaget, triggade inget
            # regressionstest och orsakade bug 168
            # if self.isTabell(p, requireColumns=True):
            #    return None
            else:
                return m.group(1)
        else:
            # sys.stdout.write("chapter_id: '%s' failed first check" % p[:40])
            return None

    def isRubrik(self, p=None):
        if p is None:
            p = self.reader.peekparagraph()
            indirect = False
        else:
            indirect = True

        self.trace['rubrik'].debug("isRubrik (%s): indirect=%s" % (
            p[:50], indirect))

        if len(p) > 0 and p[0].lower() == p[0] and not p.startswith("/Rubriken"):
            self.trace['rubrik'].debug(
                "isRubrik (%s): starts with lower-case" % (p[:50]))
            return False

        # self.trace['rubrik'].debug("isRubrik: p=%s" % p)
        # it shouldn't be too long, but some headlines are insanely verbose
        if len(p) > 110:
            self.trace['rubrik'].debug("isRubrik (%s): too long" % (p[:50]))
            return False

        # A headline should not look like the start of a paragraph or a numbered list
        if self.isParagraf(p):
            self.trace['rubrik'].debug(
                "isRubrik (%s): looks like para" % (p[:50]))
            return False

        if self.isNumreradLista(p):
            self.trace['rubrik'].debug(
                "isRubrik (%s): looks like numreradlista" % (p[:50]))
            return False

        if self.isStrecksatslista(p):
            self.trace['rubrik'].debug(
                "isRubrik (%s): looks like strecksatslista" % (p[:50]))
            return False

        if (p.endswith(".") and  # a headline never ends with a period, unless it ends with "m.m." or similar
            not (p.endswith("m.m.") or
                 p.endswith("m. m.") or
                 p.endswith("m.fl.") or
                 p.endswith("m. fl."))):
            self.trace['rubrik'].debug(
                "isRubrik (%s): ends with period" % (p[:50]))
            return False

        if (p.endswith(",") or  # a headline never ends with these characters
            p.endswith(":") or
            p.endswith("samt") or
                p.endswith("eller")):
            self.trace['rubrik'].debug(
                "isRubrik (%s): ends with comma/colon etc" % (p[:50]))
            return False

        if self.re_ChangeNote.search(p):  # eg 1994:1512 8 \xa7
            return False

        if p.startswith("/") and p.endswith("./"):
            self.trace['rubrik'].debug(
                "isRubrik (%s): Seems like a comment" % (p[:50]))
            return False

        try:
            nextp = self.reader.peekparagraph(2)
        except IOError:
            nextp = ''

        # finally, it should be followed by a paragraph - but this
        # test is only done if this check is not indirect (to avoid
        # infinite recursion)
        if not indirect:
            if (not self.isParagraf(nextp)) and (not self.isRubrik(nextp)):
                self.trace['rubrik'].debug(
                    "isRubrik (%s): is not followed by a paragraf or rubrik" % (p[:50]))
                return False

        # if this headline is followed by a second headline, that
        # headline and all subsequent headlines should be regardes as
        # sub-headlines
        if (not indirect) and self.isRubrik(nextp):
            self.current_headline_level = 1

        # ok, all tests passed, this might be a headline!
        self.trace['rubrik'].debug(
            "isRubrik (%s): All tests passed!" % (p[:50]))

        return True

    def isUpphavdParagraf(self):
        match = self.re_SectionRevoked(self.reader.peekline())
        return match is not None

    def isParagraf(self, p=None):
        if not p:
            p = self.reader.peekparagraph()
            self.trace['paragraf'].debug(
                "isParagraf: called w/ '%s' (peek)" % p[:30])
        else:
            self.trace['paragraf'].debug("isParagraf: called w/ '%s'" % p[:30])

        paragrafnummer = self.idOfParagraf(p)
        if paragrafnummer is None:
            self.trace['paragraf'].debug(
                "isParagraf: '%s': no paragrafnummer" % p[:30])
            return False
        if paragrafnummer == '1':
            self.trace['paragraf'].debug(
                "isParagraf: paragrafnummer = 1, return true")
            return True
        # now, if this sectionid is less than last section id, the
        # section is probably just a reference and not really the
        # start of a new section. One example of that is
        # /1991:1469#K1P7S1.
        if util.numcmp(paragrafnummer, self.current_section) < 0:
            self.trace['paragraf'].debug(
                "isParagraf: section numbering compare failed (%s <= %s)" % (paragrafnummer, self.current_section))
            return False

        # a similar case exists in 1994:260 and 2007:972, but there
        # the referenced section has a number larger than last section
        # id. Try another way to detect this by looking at the first
        # character in the paragraph - if it's in lower case, it's
        # probably not a paragraph.
        firstcharidx = (len(paragrafnummer) + len(' \xa7 '))
        # print "%r: %s" % (p, firstcharidx)
        if ((len(p) > firstcharidx) and
                (p[len(paragrafnummer) + len(' \xa7 ')].islower())):
            self.trace['paragraf'].debug(
                "isParagraf: section '%s' did not start with uppercase" % p[len(paragrafnummer) + len(' \xa7 '):30])
            return False
        return True

    def idOfParagraf(self, p):
        match = self.re_SectionId.match(p)
        if match:
            return match.group(1)
        else:
            match = self.re_SectionIdOld.match(p)
            if match:
                return match.group(1)
            else:
                return None

    # Om assumeTable är True är testerna något generösare än
    # annars. Den är False för den första raden i en tabell, men True
    # för de efterföljande.
    #
    # Om requireColumns är True krävs att samtliga rader är
    # spaltuppdelade

    def isTabell(self, p=None, assumeTable=False, requireColumns=False):
        shortline = 55
        shorterline = 52
        if not p:
            p = self.reader.peekparagraph()
        # Vissa snedformatterade tabeller kan ha en högercell som går
        # ned en rad för långt gentemot nästa rad, som har en tom
        # högercell:

        # xxx xxx xxxxxx     xxxx xx xxxxxx xx
        # xxxxx xx xx x      xxxxxx xxx xxx x
        #                    xx xxx xxx xxx
        # xxx xx xxxxx xx
        # xx xxx xx x xx

        # dvs något som egentligen är två stycken läses in som
        # ett. Försök hitta sådana fall, och titta i så fall endast på
        # första stycket
        lines = []
        emptyleft = False
        for l in p.split(self.reader.linesep):
            if l.startswith(' '):
                emptyleft = True
                lines.append(l)
            else:
                if emptyleft:
                    self.trace['tabell'].debug(
                        "isTabell('%s'): Snedformatterade tabellrader" % (p[:20]))
                    break
                else:
                    lines.append(l)

        numlines = len(lines)
        # Heuristiken för att gissa om detta stycke är en tabellrad:
        # Om varje rad
        # 1. Är kort (indikerar en tabellrad med en enda vänstercell)
        self.trace['tabell'].debug(
            "assumeTable: %s numlines: %s requireColumns: %s " % (assumeTable, numlines, requireColumns))
        if (assumeTable or numlines > 1) and not requireColumns:
            matches = [l for l in lines if len(l) < shortline]
            if numlines == 1 and '  ' in lines[0]:
                self.trace['tabell'].debug(
                    "isTabell('%s'): Endast en rad, men tydlig kolumnindelning" % (p[:20]))
                return True
            if len(matches) == numlines:
                self.trace['tabell'].debug(
                    "isTabell('%s'): Alla rader korta, undersöker undantag" % (p[:20]))

                # generellt undantag: Om en tabells första rad har
                # enbart vänsterkolumn M\XE5STE den följas av en
                # spaltindelad rad - annars är det nog bara två korta
                # stycken, ett kort stycke följt av kort rubrik, eller
                # liknande.
                try:
                    p2 = self.reader.peekparagraph(2)
                except IOError:
                    p2 = ''
                try:
                    p3 = self.reader.peekparagraph(3)
                except IOError:
                    p3 = ''
                if not assumeTable and not self.isTabell(p2,
                                                         assumeTable=True,
                                                         requireColumns=True):
                    self.trace['tabell'].debug(
                        "isTabell('%s'): generellt undantag från alla rader korta-regeln" % (p[:20]))
                    return False
                elif numlines == 1:
                    # Om stycket har en enda rad *kan* det vara en kort
                    # rubrik -- kolla om den följs av en paragraf, isåfall
                    # är nog tabellen slut
                    # FIXME: Kolla om inte generella undantaget borde
                    # fånga det här. Testfall
                    # regression-tabell-foljd-av-kort-rubrik.txt och
                    # temporal-paragraf-med-tabell.txt
                    if self.isParagraf(p2):
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: följs av Paragraf, inte Tabellrad" % (p[:20]))
                        return False
                    if self.isRubrik(p2) and self.isParagraf(p3):
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: följs av Rubrik och sedan Paragraf, inte Tabellrad" % (p[:20]))
                        return False
                    # Om stycket är *exakt* detta signalerar det nog
                    # övergången från tabell (kanske i slutet på en
                    # bilaga, som i SekrL) till övergångsbestämmelserna
                    if self.isOvergangsbestammelser():
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: Övergångsbestämmelser" % (p[:20]))
                        return False
                    if self.isBilaga():
                        self.trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: Bilaga" % (p[:20]))
                        return False

                # Detta undantag behöves förmodligen inte när genererella undantaget används
                # elif (numlines == 2 and
                #      self.isNumreradLista() and (
                #    lines[1].startswith('Förordning (') or
                #    lines[1].startswith('Lag ('))):
                #
                #        self.trace['tabell'].debug("isTabell('%s'): Specialundantag: ser ut som nummerpunkt följd av ändringsförfattningshänvisning" % (p[:20]))
                #        return False

                # inget av undantagen tillämpliga, huvudregel 1 gäller
                self.trace['tabell'].debug(
                    "isTabell('%s'): %s rader, alla korta" % (p[:20], numlines))
                return True

        # 2. Har mer än ett mellanslag i följd på varje rad (spaltuppdelning)
        matches = [l for l in lines if '  ' in l]
        if numlines > 1 and len(matches) == numlines:
            self.trace['tabell'].debug(
                "isTabell('%s'): %s rader, alla spaltuppdelade" % (p[:20], numlines))
            return True

        # 3. Är kort ELLER har spaltuppdelning
        self.trace['tabell'].debug("test 3")
        if (assumeTable or numlines > 1) and not requireColumns:
            self.trace['tabell'].debug("test 3.1")
            matches = [l for l in lines if '  ' in l or len(l) < shorterline]
            if len(matches) == numlines:
                self.trace['tabell'].debug(
                    "isTabell('%s'): %s rader, alla korta eller spaltuppdelade" % (p[:20], numlines))
                return True

        # 3. Är enrading med TYDLIG tabelluppdelning
        if numlines == 1 and '   ' in l:
            self.trace['tabell'].debug(
                "isTabell('%s'): %s rader, alla spaltuppdelade" % (p[:20], numlines))
            return True

        self.trace['tabell'].debug("isTabell('%s'): %s rader, inga test matchade (aT:%r, rC: %r)" %
                                   (p[:20], numlines, assumeTable, requireColumns))
        return False

    def makeTabell(self):
        pcnt = 0
        t = Tabell()
        autostrip = self.reader.autostrip
        self.reader.autostrip = False
        p = self.reader.readparagraph()
        self.trace['tabell'].debug("makeTabell: 1st line: '%s'" % p[:30])
        (trs, tabstops) = self.makeTabellrad(p)
        t.extend(trs)
        while (not self.reader.eof()):
            (l, upphor, ikrafttrader) = self.andringsDatum(
                self.reader.peekline(), match=True)
            if upphor:
                current_upphor = upphor
                self.reader.readline()
                pcnt = 1
            elif ikrafttrader:
                current_ikrafttrader = ikrafttrader
                current_upphor = None
                self.reader.readline()
                pcnt = -pcnt + 1
            elif self.isTabell(assumeTable=True):
                kwargs = {}
                if pcnt > 0:
                    kwargs['upphor'] = current_upphor
                    pcnt += 1
                elif pcnt < 0:
                    kwargs['ikrafttrader'] = current_ikrafttrader
                    pcnt += 1
                elif pcnt == 0:
                    current_ikrafttrader = None
                p = self.reader.readparagraph()
                if p:
                    (trs, tabstops) = self.makeTabellrad(
                        p, tabstops, kwargs=kwargs)
                    t.extend(trs)
            else:
                self.reader.autostrip = autostrip
                return t

        self.reader.autostrip = autostrip
        return t

    def makeTabellrad(self, p, tabstops=None, kwargs={}):
        # Algoritmen är anpassad för att hantera tabeller där texten inte
        # alltid är så jämnt ordnat i spalter, som fallet är med
        # SFSR-datat (gissningvis på grund av någon trasig
        # tab-till-space-konvertering nånstans).
        def makeTabellcell(text):
            if len(text) > 1:
                text = self.re_dehyphenate("", text)
            return Tabellcell([util.normalize_space(text)])

        cols = ['', '', '', '', '', '', '', '']
        # Ingen tabell kommer nånsin ha mer än åtta kolumner
        if tabstops:
            statictabstops = True  # Använd de tabbstoppositioner vi fick förra raden
        else:
            statictabstops = False  # Bygg nya tabbstoppositioner från scratch
            self.trace['tabell'].debug("rebuilding tabstops")
            tabstops = [0, 0, 0, 0, 0, 0, 0, 0]
        lines = p.split(self.reader.linesep)
        numlines = len([x for x in lines if x])
        potentialrows = len(
            [x for x in lines if x and (x[0].isupper() or x[0].isdigit())])
        linecount = 0
        self.trace['tabell'].debug(
            "numlines: %s, potentialrows: %s" % (numlines, potentialrows))
        if (numlines > 1 and numlines == potentialrows):
            self.trace['tabell'].debug(
                'makeTabellrad: Detta verkar vara en tabellrad-per-rad')
            singlelinemode = True
        else:
            singlelinemode = False

        rows = []
        emptyleft = False
        for l in lines:
            if l == "":
                continue
            linecount += 1
            charcount = 0
            spacecount = 0
            lasttab = 0
            colcount = 0
            if singlelinemode:
                cols = ['', '', '', '', '', '', '', '']
            if l[0] == ' ':
                emptyleft = True
            else:
                if emptyleft:
                    self.trace['tabell'].debug(
                        'makeTabellrad: skapar ny tabellrad pga snedformatering')
                    rows.append(cols)
                    cols = ['', '', '', '', '', '', '', '']
                    emptyleft = False

            for c in l:
                charcount += 1
                if c == ' ':
                    spacecount += 1
                else:
                    if spacecount > 1:  # Vi har stött på en ny tabellcell
                                       # - fyll den gamla
                        # Lägg till en nyrad för att ersätta den vi kapat -
                        # överflödig whitespace trimmas senare
                        cols[colcount] += '\n' + l[
                            lasttab:charcount - (spacecount + 1)]
                        lasttab = charcount - 1

                        # för hantering av tomma vänsterceller
                        if linecount > 1 or statictabstops:
                            # tillåt en ojämnhet om max sju tecken
                            if tabstops[colcount + 1] + 7 < charcount:
                                if len(tabstops) <= colcount + 2:
                                    tabstops.append(0)
                                    cols.append('')
                                self.trace['tabell'].debug(
                                    'colcount is %d, # of tabstops is %d' % (colcount, len(tabstops)))
                                self.trace['tabell'].debug('charcount shoud be max %s, is %s - adjusting to next tabstop (%s)' % (
                                    tabstops[colcount + 1] + 5, charcount, tabstops[colcount + 2]))
                                if tabstops[colcount + 2] != 0:
                                    self.trace['tabell'].debug(
                                        'safe to advance colcount')
                                    colcount += 1
                        colcount += 1
                        if len(tabstops) <= charcount:
                            tabstops.append(0)
                            cols.append('')
                        tabstops[colcount] = charcount
                        self.trace['tabell'].debug(
                            "Tabstops now: %r" % tabstops)
                    spacecount = 0
            cols[colcount] += '\n' + l[lasttab:charcount]
            self.trace['tabell'].debug("Tabstops: %r" % tabstops)
            if singlelinemode:
                self.trace['tabell'].debug(
                    'makeTabellrad: skapar ny tabellrad')
                rows.append(cols)

        if not singlelinemode:
            rows.append(cols)

        self.trace['tabell'].debug(repr(rows))

        res = []
        for r in rows:
            tr = Tabellrad(**kwargs)
            emptyok = True
            for c in r:
                if c or emptyok:
                    tr.append(makeTabellcell(c.replace("\n", " ")))
                    if c.strip() != '':
                        emptyok = False
            res.append(tr)

        return (res, tabstops)

    def isFastbredd(self):
        return False

    def makeFastbredd(self):
        return None

    def isNumreradLista(self, p=None):
        return self.idOfNumreradLista(p) is not None

    def idOfNumreradLista(self, p=None):
        if not p:
            p = self.reader.peekline()
            self.trace['numlist'].debug(
                "idOfNumreradLista: called directly (%s)" % p[:30])
        else:
            self.trace['numlist'].debug(
                "idOfNumreradLista: called w/ '%s'" % p[:30])
        match = self.re_DottedNumber.match(p)

        if match is not None:
            self.trace['numlist'].debug(
                "idOfNumreradLista: match DottedNumber")
            return match.group(1).replace(" ", "")
        else:
            match = self.re_NumberRightPara(p)
            if match is not None:
                self.trace['numlist'].debug(
                    "idOfNumreradLista: match NumberRightPara")
                return match.group(1).replace(" ", "")

        self.trace['numlist'].debug("idOfNumreradLista: no match")
        return None

    def isStrecksatslista(self, p=None):
        if not p:
            p = self.reader.peekline()

        return (p.startswith("- ") or
                p.startswith("\x96 ") or
                p.startswith("--"))

    def isBokstavslista(self):
        return self.idOfBokstavslista() is not None

    def idOfBokstavslista(self):
        p = self.reader.peekline()
        match = self.re_Bokstavslista.match(p)

        if match is not None:
            return match.group(1).replace(" ", "")
        return None

    def isOvergangsbestammelser(self):
        separators = ['Övergångsbestämmelser',
                      'Ikraftträdande- och övergångsbestämmelser',
                      'Övergångs- och ikraftträdandebestämmelser']

        l = self.reader.peekline()
        if l not in separators:
            fuzz = difflib.get_close_matches(l, separators, 1, 0.9)
            if fuzz:
                self.log.warning("%s: Antar att '%s' ska vara '%s'?" %
                                 (self.id, l, fuzz[0]))
            else:
                return False
        try:
            # if the separator "Övergångsbestämmelser" (or similar) is
            # followed by a regular paragraph, it was probably not a
            # separator but an ordinary headline (occurs in a few law
            # texts)
            np = self.reader.peekparagraph(2)
            if self.isParagraf(np):
                return False

        except IOError:
            pass

        return True

    def isOvergangsbestammelse(self):
        return self.re_SimpleSfsId.match(self.reader.peekline())

    def isBilaga(self):
        (line, upphor, ikrafttrader) = self.andringsDatum(
            self.reader.peekline())
        return (line in ("Bilaga", "Bilaga*", "Bilaga *",
                         "Bilaga 1", "Bilaga 2", "Bilaga 3",
                         "Bilaga 4", "Bilaga 5", "Bilaga 6"))

    return self.makeForfattning()
