# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

import difflib
from datetime import datetime
import re

from ferenda import util
from .elements import *

re_SimpleSfsId = re.compile(r'(\d{4}:\d+)\s*$')
re_SearchSfsId = re.compile(r'\((\d{4}:\d+)\)').search
re_ChangeNote = re.compile(r'(Lag|Förordning) \(\d{4}:\d+\)\.?$')
re_ChapterId = re.compile(r'^(\d+( \w|))\s[Kk][Aa][Pp]\.').match
re_DivisionId = re.compile(r'^AVD. ([IVX]*)').match
re_SectionId = re.compile(
   r'^(\d+ ?\w?) \xa7[ \.]')  # used for both match+sub
re_SectionIdOld = re.compile(
    r'^\xa7 (\d+ ?\w?).')     # as used in eg 1810:0926
re_NumberRightPara = re.compile(r'^(\d+)\) ').match

# NOTE: If we need to change these (used by idOfNumreradLista and
# idOfBokstavslista) to allow other separators than '.' and ')', we
# need to store the separator in the resulting Listelement objects
re_DottedNumber = re.compile(r'^(\d+ ?\w?)\. ')
re_Bokstavslista = re.compile(r'^(\w)\) ')
re_Strecksatslista = re.compile(r'^(- |\x96 |\u2013 |--)')

re_ElementId = re.compile(
    r'^(\d+) mom\.')        # used for both match+sub
re_ChapterRevoked = re.compile(
    r'^(\d+( \w|)) [Kk]ap. (upphävd|[Hh]ar upphävts) genom (förordning|lag) \([\d\:\. s]+\)\.?$').match
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

# use this custom matcher to ensure any strings you intend to convert
# are legal roman numerals (simpler than having from_roman throwing
# an exception)
re_roman_numeral_matcher = re.compile(
    '^M?M?M?(CM|CD|D?C?C?C?)(XC|XL|L?X?X?X?)(IX|IV|V?I?I?I?)$').match

state = {'current_section': '0',
         'current_headline_level': 0}  # 0 = unknown, 1 = normal, 2 = sub

swedish_ordinal_list = ('f\xf6rsta', 'andra', 'tredje', 'fj\xe4rde',
                        'femte', 'sj\xe4tte', 'sjunde', '\xe5ttonde',
                        'nionde', 'tionde', 'elfte', 'tolfte')

swedish_ordinal_dict = dict(list(zip(
    swedish_ordinal_list, list(range(1, len(swedish_ordinal_list) + 1)))))

def _swedish_ordinal(s):
    """'första' => '1'"""
    sl = s.lower()
    if sl in swedish_ordinal_dict:
        return swedish_ordinal_dict[sl]
    return None

def make_parser(reader, basefile, log, trace):
    state['current_avdelning'] = '0'    # avdelning
    state['current_chapter'] = '0' 
    state['current_section'] = '0' # paragraf
    state['fake_chapter'] = '0'
    state['current_headline_level'] = 0
    state['basefile'] = basefile

    def parse(the_reader):
        global reader
        reader = the_reader
        return makeForfattning()

    def makeForfattning():
        while reader.peekline() == "":
            reader.readline()

        log.debug('Första raden \'%s\'' % reader.peekline())
        (line, upphor, ikrafttrader) = andringsDatum(
            reader.peekline())
        if ikrafttrader:
            log.debug(
                'Författning med ikraftträdandedatum %s' % ikrafttrader)

            b = Forfattning(ikrafttrader=ikrafttrader, uri=None)
            reader.readline()
        else:
            log.debug('Författning utan ikraftträdandedatum')
            b = Forfattning(uri=None)

        while not reader.eof():
            state_handler = guess_state()
            # special case - if a Overgangsbestammelse is encountered
            # without the preceeding headline (which would normally
            # set state_handler to makeOvergangsbestammelser (notice
            # the plural)
            if state_handler == makeOvergangsbestammelse:
                res = makeOvergangsbestammelser(rubrik_saknas=True)
            else:
                res = state_handler()
            if res is not None:
                b.append(res)
        return b

    def makeAvdelning():
        global state
        avdelningsnummer = idOfAvdelning()
        state['current_avdelning'] = avdelningsnummer
        p = Avdelning(rubrik=reader.readline(),
                      ordinal=avdelningsnummer,
                      underrubrik=None)
        if (reader.peekline(1) == "" and
            reader.peekline(3) == "" and
                not (isKapitel(reader.peekline(2)) or
                     isUnderavdelning(reader.peekline(2)))):
            reader.readline()
            p.underrubrik = reader.readline()

        log.debug("  Ny avdelning: '%s...'" % p.rubrik[:30])

        while not reader.eof():
            state_handler = guess_state()

            if state_handler in (makeAvdelning,  # Strukturer som signalerar att denna avdelning är slut
                                 makeOvergangsbestammelser,
                                 makeBilaga):
                log.debug("  Avdelning %s färdig" % p.ordinal)
                return p
            else:
                res = state_handler()
                if res is not None:
                    p.append(res)
        # if eof is reached
        return p

    def makeUnderavdelning():
        para = reader.readparagraph()
        ordinal, rubrik = para.split(" ", 1)
        if ordinal.strip().endswith("."):
            ordinal = ordinal.strip()[:-1]
        p = Underavdelning(rubrik=rubrik,
                           ordinal=ordinal)
        log.debug("  Ny underavdelning: '%s...'" % p.rubrik[:30])
        while not reader.eof():
            state_handler = guess_state()
            if state_handler in (makeUnderavdelning,  # Strukturer som signalerar att denna underavdelning är slut
                                 makeAvdelning, 
                                 makeOvergangsbestammelser,
                                 makeBilaga):
                log.debug("  Underavdelning %s färdig" % p.ordinal)
                return p
            else:
                res = state_handler()
                if res is not None:
                    p.append(res)
        # if eof is reached
        return p

    def makeUpphavtKapitel():
        kapitelnummer = idOfKapitel()
        c = UpphavtKapitel(reader.readline(),
                           ordinal=kapitelnummer)
        log.debug("  Upphävt kapitel: '%s...'" % c[:30])

        return c

    def makeKapitel():
        global state
        kapitelnummer = idOfKapitel()
        para = reader.readparagraph()
        (line, upphor, ikrafttrader) = andringsDatum(para)

        kwargs = {'rubrik': util.normalize_space(line),
                  'ordinal': kapitelnummer}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        k = Kapitel(**kwargs)
        state['current_headline_level'] = 0
        state['current_section'] = '0'
        state['fake_chapter'] = '0'
        state['current_chapter'] = kapitelnummer
        log.debug("    Nytt kapitel: '%s...'" % line[:30])

        while not reader.eof():
            state_handler = guess_state()

            if state_handler in (makeKapitel,  # Strukturer som signalerar slutet på detta kapitel
                                 makeUpphavtKapitel,
                                 makeUnderavdelning,
                                 makeAvdelning,
                                 makeOvergangsbestammelser,
                                 makeBilaga):
                log.debug("    Kapitel %s färdigt" % k.ordinal)
                return (k)
            else:
                res = state_handler()
                if res is not None:
                    k.append(res)
        # if eof is reached
        return k

    def makeRubrik():
        global state
        para = reader.readparagraph()
        (line, upphor, ikrafttrader) = andringsDatum(para)
        log.debug("      Ny rubrik: '%s...'" % para[:30])

        kwargs = {}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        if state['current_headline_level'] == 2:
            kwargs['type'] = 'underrubrik'
        elif state['current_headline_level'] == 1:
            state['current_headline_level'] = 2

        h = Rubrik(line, **kwargs)
        return h

    def makeUpphavdParagraf():
        paragrafnummer = idOfParagraf(reader.peekline())
        p = UpphavdParagraf(reader.readline(),
                            ordinal=paragrafnummer)
        state['current_section'] = paragrafnummer
        log.debug("      Upphävd paragraf: '%s...'" % p[:30])
        return p

    def makeParagraf():
        firstline = reader.peekline()
        paragrafnummer = idOfParagraf(reader.peekparagraph())
        state['current_section'] = paragrafnummer
        log.debug("      Ny paragraf: '%s...'" % firstline[:30])
        # Läs förbi paragrafnumret:
        reader.read(len(paragrafnummer) + len(' \xa7 '))

        # some really old laws have sections split up in "elements"
        # (moment), eg '1 \xa7 1 mom.', '1 \xa7 2 mom.' etc
        match = re_ElementId.match(firstline)
        if re_ElementId.match(firstline):
            momentnummer = match.group(1)
            reader.read(len(momentnummer) + len(' mom. '))
        else:
            momentnummer = None

        (fixedline, upphor, ikrafttrader) = andringsDatum(firstline)
        # Läs förbi '/Upphör [...]/' och '/Ikraftträder [...]/'-strängarna
        reader.read(len(firstline) - len(fixedline))
        kwargs = {'ordinal': paragrafnummer}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader

        if momentnummer:
            kwargs['moment'] = momentnummer

        p = Paragraf(**kwargs)

        state_handler = makeStycke
        res = makeStycke()
        p.append(res)

        while not reader.eof():
            state_handler = guess_state()
            if state_handler in (makeParagraf,
                                 makeUpphavdParagraf,
                                 makeKapitel,
                                 makeUpphavtKapitel,
                                 makeUnderavdelning,
                                 makeAvdelning,
                                 makeRubrik,
                                 makeOvergangsbestammelser,
                                 makeBilaga):
                log.debug("      Paragraf %s färdig" % paragrafnummer)
                return p
            elif state_handler == blankline:
                state_handler()  # Bara att slänga bort
            elif state_handler == makeOvergangsbestammelse:
                log.debug("      Paragraf %s färdig" % paragrafnummer)
                log.warning(
                    "%s: Avskiljande rubrik saknas mellan författningstext och övergångsbestämmelser" % state['basefile'])
                return p
            else:
                assert state_handler == makeStycke, "guess_state returned %s, not makeStycke" % state_handler.__name__
                # if state_handler != makeStycke:
                #    log.warning("behandlar '%s...' som stycke, inte med %s" % (reader.peekline()[:30], state_handler.__name__))
                res = makeStycke()
                p.append(res)

        # eof occurred
        return p

    def makeStycke():
        log.debug(
            "        Nytt stycke: '%s...'" % reader.peekline()[:30])
        s = Stycke([util.normalize_space(reader.readparagraph())])
        while not reader.eof():
            #log.debug("            makeStycke: calling guess_state ")
            state_handler = guess_state()
            #log.debug("            makeStycke: guess_state returned %s " % state_handler.__name__)
            if state_handler in (makeNumreradLista,
                                 makeBokstavslista,
                                 makeStrecksatslista,
                                 makeTabell):
                res = state_handler()
                s.append(res)
            elif state_handler == blankline:
                state_handler()  # Bara att slänga bort
            else:
                #log.debug("            makeStycke: ...we're done")
                return s
        return s

    def makeNumreradLista():
        n = NumreradLista()
        while not reader.eof():
            # Utgå i första hand från att nästa stycke är ytterligare
            # en listpunkt (vissa tänkbara stycken kan även matcha
            # tabell m.fl.)
            if isNumreradLista():
                state_handler = makeNumreradLista
            else:
                state_handler = guess_state()
            if state_handler not in (blankline,
                                     makeNumreradLista,
                                     makeBokstavslista,
                                     makeStrecksatslista):
                return n
            elif state_handler == blankline:
                state_handler()
            else:
                if state_handler == makeNumreradLista:
                    log.debug("          Ny punkt: '%s...'" %
                                   reader.peekline()[:30])
                    listelement_ordinal = idOfNumreradLista()
                    li = Listelement(ordinal=listelement_ordinal)
                    # remove the ordinal from the string since we have
                    # it as the ordinal attribute
                    p = re_DottedNumber.sub('', reader.readparagraph())
                    li.append(p)
                    n.append(li)
                else:
                    # this must be a sublist
                    res = state_handler()
                    n[-1].append(res)
                log.debug(
                    "          Punkt %s avslutad" % listelement_ordinal)
        return n

    def makeBokstavslista():
        n = Bokstavslista()
        while not reader.eof():
            state_handler = guess_state()
            if state_handler not in (blankline, makeBokstavslista):
                return n
            elif state_handler == blankline:
                state_handler()
            else:
                log.debug("            Ny underpunkt: '%s...'" %
                               reader.peekline()[:30])
                listelement_ordinal = idOfBokstavslista()
                li = Listelement(ordinal=listelement_ordinal)
                p = re_Bokstavslista.sub('', reader.readparagraph())
                li.append(p)
                n.append(li)
                log.debug("            Underpunkt %s avslutad" %
                               listelement_ordinal)
        return n

    def makeStrecksatslista():
        n = Strecksatslista()
        cnt = 0
        while not reader.eof():
            state_handler = guess_state()
            if state_handler not in (blankline, makeStrecksatslista):
                return n
            elif state_handler == blankline:
                state_handler()
            else:
                log.debug("            Ny strecksats: '%s...'" %
                               reader.peekline()[:60])
                cnt += 1
                p = re_Strecksatslista.sub('', reader.readparagraph())
                li = Listelement(ordinal=str(cnt))
                li.append(p)
                n.append(li)
                log.debug("            Strecksats #%s avslutad" % cnt)
        return n

    def blankline():
        reader.readline()
        return None

    def eof():
        return None

    # svenska: övergångsbestämmelser
    def makeOvergangsbestammelser(rubrik_saknas=False):
        # det kan diskuteras om dessa ska ses som en del av den
        # konsoliderade lagtexten öht, men det verkar vara kutym att
        # ha med åtminstone de som kan ha relevans för gällande rätt
        log.debug("    Ny Övergångsbestämmelser")
        if rubrik_saknas:
            rubrik = "[Övergångsbestämmelser]"
        else:
            rubrik = reader.readparagraph()
        obs = Overgangsbestammelser(rubrik=rubrik)

        while not reader.eof():
            state_handler = guess_state()
            if state_handler == makeBilaga:
                return obs

            res = state_handler()
            if res is not None:
                if state_handler != makeOvergangsbestammelse:
                    # assume these are the initial Övergångsbestämmelser
                    if 'basefile' in state:
                        sfsnr = state['basefile']
                        log.warning(
                            "%s: Övergångsbestämmelsen saknar SFS-nummer - antar %s" % (state['basefile'], sfsnr))
                    else:
                        sfsnr = '0000:000'
                        log.warning(
                            "(unknown): Övergångsbestämmelsen saknar ett SFS-nummer - antar %s" % (sfsnr))

                    obs.append(Overgangsbestammelse([res], sfsnr=sfsnr))
                else:
                    obs.append(res)

        return obs

    def makeOvergangsbestammelse():
        p = reader.readline()
        log.debug("      Ny Övergångsbestämmelse: %s" % p)
        ob = Overgangsbestammelse(sfsnr=p)
        while not reader.eof():
            state_handler = guess_state()
            if state_handler in (makeOvergangsbestammelse,
                                 makeBilaga):
                return ob
            res = state_handler()
            if res is not None:
                ob.append(res)

        return ob

    def makeBilaga():  # svenska: bilaga
        rubrik = reader.readparagraph()
        (rubrik, upphor, ikrafttrader) = andringsDatum(rubrik)

        kwargs = {'rubrik': rubrik}
        if upphor:
            kwargs['upphor'] = upphor
        if ikrafttrader:
            kwargs['ikrafttrader'] = ikrafttrader
        b = Bilaga(**kwargs)
        log.debug("    Ny bilaga: %s" % rubrik)
        while not reader.eof():
            state_handler = guess_state()
            if state_handler in (makeBilaga,
                                 makeOvergangsbestammelser):
                return b
            res = state_handler()
            if res is not None:
                b.append(res)
        return b

    def andringsDatum(line, match=False):
        # Hittar ändringsdatumdirektiv i line. Om match, matcha från strängens
        # början, annars sök i hela strängen.
        dates = {'ikrafttrader': None,
                 'upphor': None}

        for (regex, key) in list({re_RevokeDate: 'upphor',
                                  re_RevokeAuthorization: 'upphor',
                                  re_EntryIntoForceDate: 'ikrafttrader',
                                  re_EntryIntoForceAuthorization: 'ikrafttrader'}.items()):
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

    def guess_state():
        try:
            if reader.peekline() == "":
                handler = blankline
            elif isAvdelning():
                handler = makeAvdelning
            elif isUnderavdelning():
                handler = makeUnderavdelning
            elif isUpphavtKapitel():
                handler = makeUpphavtKapitel
            elif isUpphavdParagraf():
                handler = makeUpphavdParagraf
            elif isKapitel():
                handler = makeKapitel
            elif isParagraf():
                handler = makeParagraf
            elif isTabell():
                handler = makeTabell
            elif isOvergangsbestammelser():
                handler = makeOvergangsbestammelser
            elif isOvergangsbestammelse():
                handler = makeOvergangsbestammelse
            elif isBilaga():
                handler = makeBilaga
            elif isNumreradLista():
                handler = makeNumreradLista
            elif isStrecksatslista():
                handler = makeStrecksatslista
            elif isBokstavslista():
                handler = makeBokstavslista
            elif isRubrik():
                handler = makeRubrik
            else:
                handler = makeStycke
        except IOError:
            handler = eof
        # sys.stdout.write("%r\n" % handler)
        return handler

    def isAvdelning():
        global state
        p = reader.peekparagraph()
        if p.count("\n") > 2:
            # An Avdelning heading should not be more than max three lines (AVD VII in 2009:400 has 3 lines)
            return False
        ordinal = idOfAvdelning()
        # can't have a avdelning with equal or less ordinal to the
        # current. also, current chapter should not be '1' otherwise
        # it's probably a TOC excerpt (see 2011:1244)
        return (ordinal and
                util.numcmp(ordinal, state['current_avdelning']) > 0 and
                state['current_chapter'] != '1')

    def isUnderavdelning(p=None):
        global state
        if state['basefile'] in ("1942:740", "2010:110"):  # only SFS that uses this structural element
            if p is None:
                p = reader.peekparagraph()
            # if it has max 2 lines, starts with a roman
            # numeral+space+sentence, and ends like a proper header,
            # it's probably an underavdelning.
            if p.count("\n") < 2 and re.match("^[IVX]+\.? +[A-ZÅÄÖ]", p) and (not p.endswith(".") or p.endswith("m.m.")):
                return True

    def idOfAvdelning():
        # There are six main styles of parts ("Avdelning") in swedish law
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
        # 2010:110: "AVD. A ÖVERGRIPANDE BESTÄMMELSER"
        # 
        # and also "1 avd." (in 1959:287 (revoked), 1959:420 (revoked)
        #
        #  The below code checks for all these patterns in turn
        #
        # The variant "Avdelning 1" has also been found, but only in
        # appendixes
        p = reader.peekline()
        if p.lower().endswith("avdelningen") and len(p.split()) == 2:
            ordinal = p.split()[0]
            return str(_swedish_ordinal(ordinal))
        elif p.startswith("AVD. ") or p.startswith("AVDELNING "):
            roman = re.split(r'\s+', p)[1]
            if roman.endswith("."):
                roman = roman[:-1]
            if re_roman_numeral_matcher(roman) and state['basefile'] != '2010:110': # avoid mismatch and subsequent conversion from roman on "AVD. C" and "AVD. D"
                return str(util.from_roman(roman))
            elif roman in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']:  # single chars used in 2010:110
                return roman
        elif p.startswith("Avdelning "):
            roman = re.split(r'\s+', p)[1]
            if re_roman_numeral_matcher(roman):
                return str(util.from_roman(roman))
        elif p[2:6] == "avd.":
            if p[0].isdigit():
                return p[0]
        elif p.startswith("Avd. "):
            idstr = re.split(r'\s+', p)[1]
            if idstr.isdigit():
                return idstr
        return None

    def isUpphavtKapitel():
        match = re_ChapterRevoked(reader.peekline())
        return match is not None

    def isKapitel(p=None):
        global state
        ordinal = idOfKapitel(p)
        if ordinal:
            # It might be OK if the current chapter is equal to this
            # one, since it might mean a title change for the chapter
            # in question (see
            # integrationSFS.Parse.test_temporal_kapitelrubriker):
            if util.numcmp(ordinal, state['current_chapter']) >= 0:
                if state['current_chapter'] == '1' and state['current_section'] == '1':
                    # if we've only seen a single § in the current
                    # (first) chapter, this is probably not a legit
                    # new chapter (more likely some form of toc).
                    if util.numcmp(ordinal, state['fake_chapter']) < 0:
                        # the new chapter is smaller than the last
                        # non-legit chapter we saw, this might mean
                        # that it is legit (and that the first chapter
                        # had a single § containing the toc,
                        # cf. 2011:1244)
                        return True
                    else:
                        state['fake_chapter'] = ordinal
                        return False
                else:
                    # chapter is bigger than the one we're currently
                    # on AND the current chapter had multiple §§'s,
                    # this is probably a new legit chapter
                    return True
            else:
                # chapter is smaller or equal to the one we're
                # currently on, probably not legit.
                pass
        return False

    def idOfKapitel(p=None):
        if not p:
            p = reader.peekparagraph().replace("\n", " ")

        # '1 a kap.' -- almost always a headline, regardless if it
        # streches several lines but there are always special cases
        # (1982:713 1 a kap. 7 \xa7)
        #m = re.match(r'^(\d+( \w|)) [Kk]ap.',p)
        m = re_ChapterId(p)
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
                  re_ChapterRevoked(p)))):  # If the entire chapter's
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
            # if isTabell(p, requireColumns=True):
            #    return None
            else:
                return m.group(1)
        else:
            # sys.stdout.write("chapter_id: '%s' failed first check" % p[:40])
            return None

    def isRubrik(p=None):
        global state
        if p is None:
            p = reader.peekparagraph()
            indirect = False
        else:
            indirect = True

        trace['rubrik'].debug("isRubrik (%s): indirect=%s" % (
            p[:50], indirect))

        if len(p) > 0 and p[0].lower() == p[0] and not p.startswith("/Rubriken"):
            trace['rubrik'].debug(
                "isRubrik (%s): starts with lower-case" % (p[:50]))
            return False

        # trace['rubrik'].debug("isRubrik: p=%s" % p)
        # it shouldn't be too long, but some headlines are insanely verbose
        if len(p) > 110:
            trace['rubrik'].debug("isRubrik (%s): too long" % (p[:50]))
            return False

        # A headline should not look like the start of a paragraph or a numbered list
        if isParagraf(p):
            trace['rubrik'].debug(
                "isRubrik (%s): looks like para" % (p[:50]))
            return False

        if isNumreradLista(p):
            trace['rubrik'].debug(
                "isRubrik (%s): looks like numreradlista" % (p[:50]))
            return False

        if isStrecksatslista(p):
            trace['rubrik'].debug(
                "isRubrik (%s): looks like strecksatslista" % (p[:50]))
            return False

        if (p.endswith(".") and  # a headline never ends with a period, unless it ends with "m.m." or similar
            not (p.endswith("m.m.") or
                 p.endswith("m. m.") or
                 p.endswith("m.fl.") or
                 p.endswith("m. fl."))):
            trace['rubrik'].debug(
                "isRubrik (%s): ends with period" % (p[:50]))
            return False

        if (p.endswith(",") or  # a headline never ends with these characters
            p.endswith(":") or
            p.endswith("samt") or
                p.endswith("eller")):
            trace['rubrik'].debug(
                "isRubrik (%s): ends with comma/colon etc" % (p[:50]))
            return False

        if re_ChangeNote.search(p):  # eg 1994:1512 8 \xa7
            return False

        if p.startswith("/") and p.endswith("./"):
            trace['rubrik'].debug(
                "isRubrik (%s): Seems like a comment" % (p[:50]))
            return False

        try:
            nextp = reader.peekparagraph(2)
        except IOError:
            nextp = ''

        # finally, it should be followed by a paragraph - but this
        # test is only done if this check is not indirect (to avoid
        # infinite recursion)
        if not indirect:
            if (not isParagraf(nextp)) and (not isRubrik(nextp)):
                trace['rubrik'].debug(
                    "isRubrik (%s): is not followed by a paragraf or rubrik" % (p[:50]))
                return False

        # if this headline is followed by a second headline, that
        # headline and all subsequent headlines should be regardes as
        # sub-headlines
        if (not indirect) and isRubrik(nextp):
            state['current_headline_level'] = 1

        # ok, all tests passed, this might be a headline!
        trace['rubrik'].debug(
            "isRubrik (%s): All tests passed!" % (p[:50]))

        return True

    def isUpphavdParagraf():
        match = re_SectionRevoked(reader.peekline())
        return match is not None

    def isParagraf(p=None):
        global state
        if not p:
            p = reader.peekparagraph()
            trace['paragraf'].debug(
                "isParagraf: called w/ '%s' (peek)" % p[:30])
        else:
            trace['paragraf'].debug("isParagraf: called w/ '%s'" % p[:30])
        paragrafnummer = idOfParagraf(p)
        if paragrafnummer is None:
            trace['paragraf'].debug(
                "isParagraf: '%s': no paragrafnummer" % p[:30])
            return False
        if paragrafnummer == '1':
            trace['paragraf'].debug(
                "isParagraf: paragrafnummer = 1, return true")
            return True
        # now, if this sectionid is less than last section id, the
        # section is probably just a reference and not really the
        # start of a new section. One example of that is
        # /1991:1469#K1P7S1.
        if util.numcmp(paragrafnummer, state['current_section']) < 0:
            trace['paragraf'].debug(
                "isParagraf: section numbering compare failed (%s <= %s)" % (paragrafnummer, state['current_section']))
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
            trace['paragraf'].debug(
                "isParagraf: section '%s' did not start with uppercase" % p[len(paragrafnummer) + len(' \xa7 '):30])
            return False
        return True

    def idOfParagraf(p):
        match = re_SectionId.match(p)
        if match:
            return match.group(1)
        else:
            match = re_SectionIdOld.match(p)
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

    def isTabell(p=None, assumeTable=False, requireColumns=False):
        shortline = 55
        shorterline = 52
        if not p:
            p = reader.peekparagraph()
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
        for l in p.split(reader.linesep):
            if l.startswith(' '):
                emptyleft = True
                lines.append(l)
            else:
                if emptyleft:
                    trace['tabell'].debug(
                        "isTabell('%s'): Snedformatterade tabellrader" % (p[:20]))
                    break
                else:
                    lines.append(l)

        numlines = len(lines)
        # Heuristiken för att gissa om detta stycke är en tabellrad:
        # Om varje rad
        # 1. Är kort (indikerar en tabellrad med en enda vänstercell)
        trace['tabell'].debug(
            "assumeTable: %s numlines: %s requireColumns: %s " % (assumeTable, numlines, requireColumns))
        if (assumeTable or numlines > 1) and not requireColumns:
            matches = [l for l in lines if len(l) < shortline]
            if numlines == 1 and '  ' in lines[0]:
                trace['tabell'].debug(
                    "isTabell('%s'): Endast en rad, men tydlig kolumnindelning" % (p[:20]))
                return True
            if len(matches) == numlines:
                trace['tabell'].debug(
                    "isTabell('%s'): Alla rader korta, undersöker undantag" % (p[:20]))

                # generellt undantag: Om en tabells första rad har
                # enbart vänsterkolumn M\XE5STE den följas av en
                # spaltindelad rad - annars är det nog bara två korta
                # stycken, ett kort stycke följt av kort rubrik, eller
                # liknande.
                try:
                    p2 = reader.peekparagraph(2)
                except IOError:
                    p2 = ''
                try:
                    p3 = reader.peekparagraph(3)
                except IOError:
                    p3 = ''
                if not assumeTable and not isTabell(p2,
                                                         assumeTable=True,
                                                         requireColumns=True):
                    trace['tabell'].debug(
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
                    if isParagraf(p2):
                        trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: följs av Paragraf, inte Tabellrad" % (p[:20]))
                        return False
                    if isRubrik(p2) and isParagraf(p3):
                        trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: följs av Rubrik och sedan Paragraf, inte Tabellrad" % (p[:20]))
                        return False
                    # Om stycket är *exakt* detta signalerar det nog
                    # övergången från tabell (kanske i slutet på en
                    # bilaga, som i SekrL) till övergångsbestämmelserna
                    if isOvergangsbestammelser():
                        trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: Övergångsbestämmelser" % (p[:20]))
                        return False
                    if isBilaga():
                        trace['tabell'].debug(
                            "isTabell('%s'): Specialundantag: Bilaga" % (p[:20]))
                        return False

                # Detta undantag behöves förmodligen inte när genererella undantaget används
                # elif (numlines == 2 and
                #      isNumreradLista() and (
                #    lines[1].startswith('Förordning (') or
                #    lines[1].startswith('Lag ('))):
                #
                #        trace['tabell'].debug("isTabell('%s'): Specialundantag: ser ut som nummerpunkt följd av ändringsförfattningshänvisning" % (p[:20]))
                #        return False

                # inget av undantagen tillämpliga, huvudregel 1 gäller
                trace['tabell'].debug(
                    "isTabell('%s'): %s rader, alla korta" % (p[:20], numlines))
                return True

        # 2. Har mer än ett mellanslag i följd på varje rad (spaltuppdelning)
        matches = [l for l in lines if '  ' in l]
        if numlines > 1 and len(matches) == numlines:
            trace['tabell'].debug(
                "isTabell('%s'): %s rader, alla spaltuppdelade" % (p[:20], numlines))
            return True

        # 3. Är kort ELLER har spaltuppdelning
        trace['tabell'].debug("test 3")
        if (assumeTable or numlines > 1) and not requireColumns:
            trace['tabell'].debug("test 3.1")
            matches = [l for l in lines if '  ' in l or len(l) < shorterline]
            if len(matches) == numlines:
                trace['tabell'].debug(
                    "isTabell('%s'): %s rader, alla korta eller spaltuppdelade" % (p[:20], numlines))
                return True

        # 3. Är enrading med TYDLIG tabelluppdelning
        if numlines == 1 and '   ' in l:
            trace['tabell'].debug(
                "isTabell('%s'): %s rader, alla spaltuppdelade" % (p[:20], numlines))
            return True

        trace['tabell'].debug("isTabell('%s'): %s rader, inga test matchade (aT:%r, rC: %r)" %
                                   (p[:20], numlines, assumeTable, requireColumns))
        return False

    def makeTabell():
        pcnt = 0
        t = Tabell()
        autostrip = reader.autostrip
        reader.autostrip = False
        p = reader.readparagraph()
        trace['tabell'].debug("makeTabell: 1st line: '%s'" % p[:30])
        (trs, tabstops) = makeTabellrad(p)
        t.extend(trs)
        while (not reader.eof()):
            (l, upphor, ikrafttrader) = andringsDatum(
                reader.peekline(), match=True)
            if upphor:
                current_upphor = upphor
                reader.readline()
                pcnt = 1
            elif ikrafttrader:
                current_ikrafttrader = ikrafttrader
                current_upphor = None
                reader.readline()
                pcnt = -pcnt + 1
            elif isTabell(assumeTable=True):
                kwargs = {}
                if pcnt > 0:
                    kwargs['upphor'] = current_upphor
                    pcnt += 1
                elif pcnt < 0:
                    kwargs['ikrafttrader'] = current_ikrafttrader
                    pcnt += 1
                elif pcnt == 0:
                    current_ikrafttrader = None
                p = reader.readparagraph()
                if p:
                    (trs, tabstops) = makeTabellrad(
                        p, tabstops, kwargs=kwargs)
                    t.extend(trs)
            else:
                reader.autostrip = autostrip
                return t

        reader.autostrip = autostrip
        return t

    def makeTabellrad(p, tabstops=None, kwargs={}):
        # Algoritmen är anpassad för att hantera tabeller där texten inte
        # alltid är så jämnt ordnat i spalter, som fallet är med
        # SFSR-datat (gissningvis på grund av någon trasig
        # tab-till-space-konvertering nånstans).
        def makeTabellcell(text):
            if len(text) > 1:
                text = re_dehyphenate("", text)
            return Tabellcell([util.normalize_space(text)])

        cols = ['', '', '', '', '', '', '', '']
        # Ingen tabell kommer nånsin ha mer än åtta kolumner
        if tabstops:
            statictabstops = True  # Använd de tabbstoppositioner vi fick förra raden
        else:
            statictabstops = False  # Bygg nya tabbstoppositioner från scratch
            trace['tabell'].debug("rebuilding tabstops")
            tabstops = [0, 0, 0, 0, 0, 0, 0, 0]
        lines = p.split(reader.linesep)
        numlines = len([x for x in lines if x])
        potentialrows = len(
            [x for x in lines if x and (x[0].isupper() or x[0].isdigit())])
        linecount = 0
        trace['tabell'].debug(
            "numlines: %s, potentialrows: %s" % (numlines, potentialrows))
        if (numlines > 1 and numlines == potentialrows):
            trace['tabell'].debug(
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
                    trace['tabell'].debug(
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
                                trace['tabell'].debug(
                                    'colcount is %d, # of tabstops is %d' % (colcount, len(tabstops)))
                                trace['tabell'].debug('charcount shoud be max %s, is %s - adjusting to next tabstop (%s)' % (
                                    tabstops[colcount + 1] + 5, charcount, tabstops[colcount + 2]))
                                if tabstops[colcount + 2] != 0:
                                    trace['tabell'].debug(
                                        'safe to advance colcount')
                                    colcount += 1
                        colcount += 1
                        if len(tabstops) <= charcount:
                            tabstops.append(0)
                            cols.append('')
                        tabstops[colcount] = charcount
                        trace['tabell'].debug(
                            "Tabstops now: %r" % tabstops)
                    spacecount = 0
            cols[colcount] += '\n' + l[lasttab:charcount]
            trace['tabell'].debug("Tabstops: %r" % tabstops)
            if singlelinemode:
                trace['tabell'].debug(
                    'makeTabellrad: skapar ny tabellrad')
                rows.append(cols)

        if not singlelinemode:
            rows.append(cols)

        trace['tabell'].debug(repr(rows))

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

    def isFastbredd():
        return False

    def makeFastbredd():
        return None

    def isNumreradLista(p=None):
        return idOfNumreradLista(p) is not None

    def idOfNumreradLista(p=None):
        if not p:
            p = reader.peekline()
        match = re_DottedNumber.match(p)

        if match is not None:
            trace['numlist'].debug(
                "idOfNumreradLista: match DottedNumber")
            return match.group(1).replace(" ", "")
        else:
            match = re_NumberRightPara(p)
            if match is not None:
                trace['numlist'].debug(
                    "idOfNumreradLista: match NumberRightPara")
                return match.group(1).replace(" ", "")

        trace['numlist'].debug("idOfNumreradLista: no match")
        return None

    def isStrecksatslista(p=None):
        if not p:
            p = reader.peekline()
        return re_Strecksatslista.match(p) != None

    def isBokstavslista():
        return idOfBokstavslista() is not None

    def idOfBokstavslista():
        p = reader.peekline()
        match = re_Bokstavslista.match(p)

        if match is not None:
            return match.group(1).replace(" ", "")
        return None

    def isOvergangsbestammelser():
        separators = ['Övergångsbestämmelser',
                      'Ikraftträdande- och övergångsbestämmelser',
                      'Övergångs- och ikraftträdandebestämmelser']

        l = reader.peekline()
        if l not in separators:
            fuzz = difflib.get_close_matches(l, separators, 1, 0.9)
            if fuzz:
                log.warning("%s: Antar att '%s' ska vara '%s'?" %
                                 (state['basefile'], l, fuzz[0]))
            else:
                return False
        try:
            # if the separator "Övergångsbestämmelser" (or similar) is
            # followed by a regular paragraph, it was probably not a
            # separator but an ordinary headline (occurs in a few law
            # texts)
            np = reader.peekparagraph(2)
            if isParagraf(np):
                return False
        except IOError:
            pass
        return True

    def isOvergangsbestammelse():
        return re_SimpleSfsId.match(reader.peekline())

    def isBilaga():
        (line, upphor, ikrafttrader) = andringsDatum(
            reader.peekline())
        return (line in ("Bilaga", "Bilaga*", "Bilaga *",
                         "Bilaga 1", "Bilaga 2", "Bilaga 3",
                         "Bilaga 4", "Bilaga 5", "Bilaga 6"))

    return parse
