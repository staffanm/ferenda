# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import logging
import os
import re

# thirdparty
try:
    from simpleparse.parser import Parser
    from simpleparse.stt.TextTools.TextTools import tag
except ImportError:
    from ._simpleparseFallback import Parser, tag
import six
from six import text_type as str
from rdflib import Graph, Namespace, Literal, BNode, RDFS, RDF, URIRef
from rdflib.namespace import DCTERMS, SKOS
COIN = Namespace("http://purl.org/court/def/2009/coin#")

# my own libraries
from ferenda import ResourceLoader
from ferenda import util
from ferenda.elements import Link, LinkSubject
from ferenda.thirdparty.coin import URIMinter
from . import RPUBL, RINFOEX

# The charset used for the bytestrings that is sent to/from
# simpleparse (which does not handle unicode)
# Choosing utf-8 makes § a two-byte character, which does not work well
SP_CHARSET = 'iso-8859-1'
log = logging.getLogger('lr')


# Lite om hur det hela funkar: Att hitta referenser i löptext är en
# tvåstegsprocess.
#
# I det första steget skapar simpleparse en nodstruktur från indata
# och en lämplig ebnf-grammatik. Väldigt lite kod i den här modulen
# hanterar första steget, simpleparse gör det tunga
# jobbet. Nodstrukturen kommer ha noder med samma namn som de
# produktioner som definerats i ebnf-grammatiken.
#
# I andra steget gås nodstrukturen igenom och omvandlas till en lista
# av omväxlande unicode- och Link-objekt. Att skapa Link-objekten är
# det svåra, och det mesta jobbet görs av formatter_dispatch. Den
# tittar på varje nod och försöker hitta ett lämpligt sätt att
# formattera den till ett Link-objekt med en uri-property. Eftersom
# vissa produktioner ska resultera i flera länkar och vissa bara i en
# kan detta inte göras av en enda formatteringsfunktion. För de enkla
# fallen räcker den generiska formatteraren format_tokentree till, men
# för svårare fall skrivs separata formatteringsfunktioner. Dessa har
# namn som matchar produktionerna (exv motsvaras produktionen
# ChapterSectionRefs av funktionen format_ChapterSectionRefs).
#
# Koden är tänkt att vara generell för all sorts referensigenkänning i
# juridisk text. Eftersom den växt från kod som bara hanterade rena
# lagrumsreferenser är det ganska mycket kod som bara är relevant för
# igenkänning av just svenska lagrumsänvisningar så som de förekommer
# i SFS. Sådana funktioner/avsnitt är markerat med "SFS-specifik
# [...]" eller "KOD FÖR LAGRUM"

class LegalRef:
    # Kanske detta borde vara 1,2,4,8 osv, så att anroparen kan be om
    # LAGRUM | FORESKRIFTER, och så vi kan definera samlingar av
    # vanliga kombinationer (exv ALL_LAGSTIFTNING = LAGRUM |
    # KORTLAGRUM | FORESKRIFTER | EULAGSTIFTNING)
    LAGRUM = 1             # hänvisningar till lagrum i SFS
    KORTLAGRUM = 2         # SFS-hänvisningar på kortform
    FORESKRIFTER = 3       # hänvisningar till myndigheters författningssamlingar
    EULAGSTIFTNING = 4     # EU-fördrag, förordningar och direktiv
    INTLLAGSTIFTNING = 5   # Fördrag, traktat etc
    FORARBETEN = 6         # proppar, betänkanden, etc
    RATTSFALL = 7          # Rättsfall i svenska domstolar
    MYNDIGHETSBESLUT = 8   # Myndighetsbeslut (JO, ARN, DI...)
    EURATTSFALL = 9        # Rättsfall i EU-domstolen/förstainstansrätten
    INTLRATTSFALL = 10     # Europadomstolen
    DOMSTOLSAVGORANDEN = 11# Underliggande beslut i ett rättsfallsreferat


    re_urisegments = re.compile(
        r'([\w]+://[^/]+/[^\d]*)(\d+:(bih\.[_ ]|N|)?\d+([_ ]s\.\d+|))#?(K([a-z0-9]+)|)(P([a-z0-9]+)|)(S(\d+)|)(N(\d+)|)')
    re_escape_compound = re.compile(
        r'\b(\w+-) (och) (\w+-?)(lagen|förordningen)\b', re.UNICODE)
    re_escape_named = re.compile(
        r'\B(lagens?|balkens?|förordningens?|formens?|ordningens?|kungörelsens?|stadgans?)\b', re.UNICODE)

    re_descape_compound = re.compile(
        r'\b(\w+-)_(och)_(\w+-?)(lagen|förordningen)\b', re.UNICODE)
    re_descape_named = re.compile(
        r'\|(lagens?|balkens?|förordningens?|formens?|ordningens?|kungörelsens?|stadgans?)')
    re_xmlcharref = re.compile("&#\d+;")

    def __init__(self, *args):
        if not os.path.sep in __file__:
            scriptdir = os.getcwd()
        else:
            scriptdir = os.path.dirname(__file__)
        resourceloader = ResourceLoader(scriptdir)
        fname = resourceloader.filename
        
        self.roots = []
        self.uriformatter = {}
        self.decl = ""
        self.namedlaws = {}
        self.namedseries = {}
        self.lawlist = []
        
        self.load_ebnf(fname("res/ebnf/base.ebnf"))

        self.args = args
        if self.LAGRUM in args:
            productions = self.load_ebnf(fname("res/ebnf/lagrum.ebnf"))
            for p in productions:
                self.uriformatter[p] = self.sfs_format_uri
            self.roots.append("sfsrefs")
            self.roots.append("sfsref")

        if self.KORTLAGRUM in args:
            # om vi inte redan laddat lagrum.ebnf måste vi göra det
            # nu, eftersom kortlagrum.ebnf beror på produktioner som
            # definerats där
            if not self.LAGRUM in args:
                self.load_ebnf(fname("res/ebnf/lagrum.ebnf"))

            productions = self.load_ebnf(fname("res/ebnf/kortlagrum.ebnf"))
            for p in productions:
                self.uriformatter[p] = self.sfs_format_uri
            self.roots.insert(0, "kortlagrumref")  # must be the first root

        if self.EULAGSTIFTNING in args:
            productions = self.load_ebnf(fname("res/ebnf/eulag.ebnf"))
            for p in productions:
                self.uriformatter[p] = self.eulag_format_uri
            self.roots.append("eulagref")

        if self.FORARBETEN in args:
            productions = self.load_ebnf(fname("res/ebnf/forarbeten.ebnf"))
            for p in productions:
                self.uriformatter[p] = self.forarbete_format_uri
            self.roots.append("forarbeteref")

        if self.RATTSFALL in args:
            productions = self.load_ebnf(fname("res/ebnf/rattsfall.ebnf"))
            for p in productions:
                self.uriformatter[p] = self.rattsfall_format_uri
            self.roots.append("rattsfallref")

        if self.EURATTSFALL in args:
            productions = self.load_ebnf(fname("res/ebnf/euratt.ebnf"))
            for p in productions:
                self.uriformatter[p] = self.eurattsfall_format_uri
            self.roots.append("ecjcaseref")

        rootprod = "root ::= (%s/plain)+\n" % "/".join(self.roots)
        self.decl += rootprod
        # if KORTLAGRUM, delay the construction of teh parser until we
        # can construct the LawAbbreviation production (see parse())
        if self.KORTLAGRUM not in self.args:
            self.tagger = Parser(self.decl.encode(
                SP_CHARSET), "root").buildTagger("root")
        self.verbose = False
        self.depth = 0

        # SFS-specifik kod
        self.currentlaw = None
        self.currentchapter = None
        self.currentsection = None
        self.currentpiece = None
        self.lastlaw = None
        self.currentlynamedlaws = {}

    def load_ebnf(self, file):
        """Laddar in produktionerna i den angivna filen i den
        EBNF-deklaration som används, samt returnerar alla
        *Ref och *RefId-produktioner"""
        # base.ebnf contains 0x1A, ie the EOF character on windows,
        # therefore we need to read it in binary mode

        f = open(file, 'rb')
        # assume our ebnf files use the same charset
        content = f.read(os.stat(file).st_size).decode(SP_CHARSET)
        self.decl += content
        f.close()
        return [x.group(1) for x in re.finditer(r'(\w+(Ref|RefID))\s*::=',
                                                content)]

    def get_relations(self, predicate, graph):
        d = {}
        for obj, subj in graph.subject_objects(predicate):
            d[six.text_type(subj)] = six.text_type(obj)
        return d

    def parse(self, 
              indata,
              minter,
              metadata_graph=None,
              baseuri_attributes=None,
              predicate=None,
              allow_relative=True):
        assert isinstance(indata, str)
        assert isinstance(minter, URIMinter)
        assert isinstance(metadata_graph, Graph)
        if indata == "":
            return indata  # this actually triggered a bug...
        self.predicate = predicate
        self.minter = minter
        self.metadata_graph = metadata_graph if metadata_graph else Graph()
        self.allow_relative = allow_relative

        if ((self.LAGRUM in self.args or
             self.KORTLAGRUM in self.args) and not self.namedlaws):
                self.namedlaws.update(self.get_relations(RDFS.label,
                                                         self.metadata_graph))

        if self.KORTLAGRUM in self.args and not self.lawlist:
            d = self.get_relations(DCTERMS.alternate, self.metadata_graph)
            self.namedlaws.update(d)
            self.lawlist = list(d.keys())
            # Make sure longer law abbreviations come before shorter
            # ones (so that we don't mistake "3 § MBL" for "3 § MB"+"L")
            self.lawlist.sort(key=len, reverse=True)

            # re-do the parser now that we have all law abbrevs (which
            # must be present in the supplied graph)
            lawdecl = "LawAbbreviation ::= ('%s')\n" % "'/'".join(
                self.lawlist)
            self.decl += lawdecl
            self.tagger = Parser(self.decl.encode(
                SP_CHARSET), "root").buildTagger("root")
            
        if self.RATTSFALL in self.args and not self.namedseries:
            self.namedseries.update(self.get_relations(SKOS.altLabel,
                                                       self.metadata_graph))

        if baseuri_attributes is None:
            self.baseuri_attributes = {"law": "9999:999",
                                       "chapter": "9",
                                       "section": "9",
                                       "piece": "9",
                                       "items": "9"}
        else:
            self.baseuri_attributes = baseuri_attributes

        if self.baseuri_attributes == {}:
            self.nobaseuri = True
        else:
            self.nobaseuri = False

        # Det är svårt att få EBNF-grammatiken att känna igen
        # godtyckliga ord som slutar på ett givet suffix (exv
        # 'bokföringslagen' med suffixet 'lagen'). Därför förbehandlar
        # vi indatasträngen och stoppar in ett '|'-tecken innan vissa
        # suffix. Vi transformerar även 'Radio- och TV-lagen' till
        # 'Radio-_och_TV-lagen'
        fixedindata = indata  # FIXME: Nonsensical
        if self.LAGRUM in self.args:
            fixedindata = self.re_escape_compound.sub(
                r'\1_\2_\3\4', fixedindata)
            fixedindata = self.re_escape_named.sub(r'|\1', fixedindata)
        # print "After: %r" % type(fixedindata)

        # SimpleParse har inget stöd för unicodesträngar, så vi
        # konverterar intdatat till en bytesträng. Tyvärr får jag inte
        # det hela att funka med UTF8, så vi kör xml character
        # references istället
        fixedindata = fixedindata.encode(SP_CHARSET, 'xmlcharrefreplace')

        # Parsea texten med TextTools.tag - inte det enklaste sättet
        # att göra det, men om man gör enligt
        # Simpleparse-dokumentationen byggs taggertabellen om för
        # varje anrop till parse()
        if self.verbose:
            print(("calling tag with '%s'" % (fixedindata.decode(SP_CHARSET))))
        # print "tagger length: %d" % len(repr(self.tagger))
        taglist = tag(fixedindata, self.tagger, 0, len(fixedindata))

        result = []

        root = NodeTree(taglist, fixedindata)
        for part in root.nodes:
            if part.tag != 'plain' and self.verbose:
                sys.stdout.write(self.prettyprint(part))
            if part.tag in self.roots:
                self.clear_state()
                # self.verbose = False
                result.extend(self.formatter_dispatch(part))
            else:
                assert part.tag == 'plain', "Tag is %s" % part.tag
                result.append(part.text)

            # clear state
            if self.currentlaw is not None:
                self.lastlaw = self.currentlaw
            self.currentlaw = None

        if taglist[-1] != len(fixedindata):
            log.error('Problem (%d:%d) with %r / %r' % (
                taglist[-1] - 8, taglist[-1] + 8, fixedindata, indata))

            raise RefParseError(
                "parsed %s chars of %s (...%s...)" % (taglist[-1], len(indata),
                                                      indata[(taglist[-1] - 2):taglist[-1] + 3]))

        # Normalisera resultatet, dvs konkatenera intilliggande
        # textnoder, och ta bort ev '|'-tecken som vi stoppat in
        # tidigare.
        normres = []
        for i in range(len(result)):
            if not self.re_descape_named.search(result[i]):
                node = result[i]
            else:
                if self.LAGRUM in self.args:
                    text = self.re_descape_named.sub(r'\1', result[i])
                    text = self.re_descape_compound.sub(r'\1 \2 \3\4', text)
                if isinstance(result[i], Link):
                    # Eftersom Link-objekt är immutable måste vi skapa
                    # ett nytt och kopiera dess attribut
                    if hasattr(result[i], 'predicate'):
                        node = LinkSubject(text, predicate=result[i].predicate,
                                           uri=result[i].uri)
                    else:
                        node = Link(text, uri=result[i].uri)
                else:
                    node = text
            if (len(normres) > 0
                and not isinstance(normres[-1], Link)
                    and not isinstance(node, Link)):
                normres[-1] += node
            else:
                normres.append(node)

        # and finally...
        for i in range(len(normres)):
            if isinstance(normres[i], Link):
                # deal with these later
                pass
            else:
                normres[i] = self.re_xmlcharref.sub(
                    self.unescape_xmlcharref, normres[i])
        return normres

    def unescape_xmlcharref(self, m):
        return chr(int(m.group(0)[2:-1]))

    def find_attributes(self, parts, extra={}):
        """recurses through a parse tree and creates a dictionary of
        attributes"""
        d = {}

        self.depth += 1
        if self.verbose:
            print(
                (". " * self.depth + "find_attributes: starting with %s" % d))
        if extra:
            d.update(extra)

        for part in parts:
            current_part_tag = part.tag.lower()
            if current_part_tag.endswith('refid'):
                if ((current_part_tag == 'singlesectionrefid') or
                        (current_part_tag == 'lastsectionrefid')):
                    current_part_tag = 'sectionrefid'
                d[current_part_tag[:-5]] = part.text.strip()
                if self.verbose:
                    print((". " * self.depth +
                           "find_attributes: d is now %s" % d))

            if part.nodes:
                d.update(self.find_attributes(part.nodes, d))
        if self.verbose:
            print((". " * self.depth + "find_attributes: returning %s" % d))
        self.depth -= 1

        if self.currentlaw and 'law' not in d:
            d['law'] = self.currentlaw
        if self.currentchapter and 'chapter' not in d:
            d['chapter'] = self.currentchapter
        if self.currentsection and 'section' not in d:
            d['section'] = self.currentsection
        if self.currentpiece and 'piece' not in d:
            d['piece'] = self.currentpiece

        return d

    def find_node(self, root, nodetag):
        """Returns the first node in the tree that has a tag matching nodetag. The search is depth-first"""
        if root.tag == nodetag:  # base case
            return root
        else:
            for node in root.nodes:
                x = self.find_node(node, nodetag)
                if x is not None:
                    return x
            return None

    def find_nodes(self, root, nodetag):
        if root.tag == nodetag:
            return [root]
        else:
            res = []
            for node in root.nodes:
                res.extend(self.find_nodes(node, nodetag))
            return res

    def flatten_tokentree(self, part, suffix):
        """returns a 'flattened' tokentree ie for the following tree and the suffix 'RefID'
           foo->bar->BlahongaRefID
              ->baz->quux->Blahonga2RefID
                         ->Blahonga3RefID
              ->Blahonga4RefID

           this should return [BlahongaRefID, Blahonga2RefID, Blahonga3RefID, Blahonga4RefID]"""
        l = []
        if part.tag.endswith(suffix):
            l.append(part)
        if not part.nodes:
            return l

        for subpart in part.nodes:
            l.extend(self.flatten_tokentree(subpart, suffix))
        return l

    def formatter_dispatch(self, part):
        # print "Verbositiy: %r" % self.verbose
        self.depth += 1
        # Finns det en skräddarsydd formatterare?
        if "format_" + part.tag in dir(self):
            formatter = getattr(self, "format_" + part.tag)
            if self.verbose:
                print(
                    ((". " * self.depth) + "formatter_dispatch: format_%s defined, calling it" % part.tag))
            res = formatter(part)
            assert res is not None, "Custom formatter for %s didn't return anything" % part.tag
        else:
            if self.verbose:
                print(
                    ((". " * self.depth) + "formatter_dispatch: no format_%s, using format_tokentree" % part.tag))
            res = self.format_tokentree(part)

        if res is None:
            print(((". " * self.depth) +
                   "something wrong with this:\n" + self.prettyprint(part)))
        self.depth -= 1
        return res

    def format_tokentree(self, part):
        # This is the default formatter. It converts every token that
        # ends with a RefID into a Link object. For grammar
        # productions like SectionPieceRefs, which contain
        # subproductions that also end in RefID, this is not a good
        # function to use - use a custom formatter instead.

        res = []

        if self.verbose:
            print(((". " * self.depth) +
                   "format_tokentree: called for %s" % part.tag))
        # this is like the bottom case, or something
        if (not part.nodes) and (not part.tag.endswith("RefID")):
            res.append(part.text)
        else:
            if part.tag.endswith("RefID"):
                res.append(self.format_generic_link(part))
            elif part.tag.endswith("Ref"):
                res.append(self.format_generic_link(part))
            else:
                for subpart in part.nodes:
                    if self.verbose and part.tag == 'LawRef':
                        print(
                            ((". " * self.depth) + "format_tokentree: part '%s' is a %s" % (subpart.text, subpart.tag)))
                    res.extend(self.formatter_dispatch(subpart))
        if self.verbose:
            print(
                ((". " * self.depth) + "format_tokentree: returning '%s' for %s" % (res, part.tag)))
        return res

    def prettyprint(self, root, indent=0):
        res = "%s'%s': '%s'\n" % (
            "    " * indent, root.tag, re.sub(r'\s+', ' ', root.text))
        if root.nodes is not None:
            for subpart in root.nodes:
                res += self.prettyprint(subpart, indent + 1)
            return res
        else:
            return ""

    def format_generic_link(self, part, uriformatter=None):
        try:
            uri = self.uriformatter[part.tag](self.find_attributes([part]))
        except KeyError:
            if uriformatter:
                uri = uriformatter(self.find_attributes([part]))
            else:
                uri = self.sfs_format_uri(self.find_attributes([part]))
        except AttributeError:
            # Normal error from eulag_format_uri
            return part.text
#        except Exception as e:
#            # FIXME: We should maybe not swallow all other errors...
#            # If something else went wrong, just return the plaintext
#            log.warning("(unknown): Unable to format link for text %s (production %s): %s: %s" %
#                        (part.text, part.tag, type(e).__name__, e))
#            return part.text
#
        if self.verbose:
            print((
                (". " * self.depth) + "format_generic_link: uri is %s" % uri))
        if not uri:
            # the formatting function decided not to return a URI for
            # some reason (maybe it was a partial/relative reference
            # without a proper base uri context
            return part.text
        elif self.predicate:
            return LinkSubject(part.text, uri=uri, predicate=self.predicate)
        else:
            return Link(part.text, uri=uri)

    # FIXME: unify this with format_generic_link
    def format_custom_link(self, attributes, text, production):
        try:
            uri = self.uriformatter[production](attributes)
        except KeyError:
            uri = self.sfs_format_uri(attributes)

        if not uri:
            # the formatting function decided not to return a URI for
            # some reason (maybe it was a partial/relative reference
            # without a proper base uri context
            return text
        elif self.predicate:
            return LinkSubject(text, uri=uri, predicate=self.predicate)
        else:
            return Link(text, uri=uri)

    #
    # KOD FÖR LAGRUM
    def clear_state(self):
        self.currentlaw = None
        self.currentchapter = None
        self.currentsection = None
        self.currentpiece = None

    def normalize_sfsid(self, sfsid):
        # sometimes '1736:0123 2' is given as '1736:0123 s. 2' or
        # '1736:0123.2'. This fixes that.
        sfsid = re.sub(r'(\d+:\d+)\.(\d)', r'\1 \2', sfsid)
        sfsid = sfsid.replace("\n", " ")
        # return sfsid.replace('s. ','').replace('s.','') # more advanced
        # normalizations to come...
        return sfsid

    def normalize_lawname(self, lawname):
        lawname = lawname.replace('|', '').replace('_', ' ').lower()
        if lawname.endswith('s'):
            lawname = lawname[:-1]
        return lawname

    nolaw = [
        'aktieslagen',
        'anordningen',
        'anordningen',
        'anslagen',
        'arbetsordningen',
        'associationsformen',
        'avfallsslagen',
        'avslagen',
        'avvittringsutslagen',
        'bergslagen',
        'beskattningsunderlagen',
        'bolagen',
        'bolagsordningen',
        'bolagsordningen',
        'dagordningen',
        'djurslagen',
        'dotterbolagen',
        'emballagen',
        'energislagen',
        'ersättningsformen',
        'ersättningsslagen',
        'examensordningen',
        'finansbolagen',
        'finansieringsformen',
        'fissionsvederlagen',
        'flygbolagen',
        'fondbolagen',
        'förbundsordningen',
        'föreslagen',
        'företrädesordningen',
        'förhandlingsordningen',
        'förlagen',
        'förmånsrättsordningen',
        'förmögenhetsordningen',
        'förordningen',
        'förslagen',
        'försäkringsaktiebolagen',
        'försäkringsbolagen',
        'gravanordningen',
        'grundlagen',
        'handelsplattformen',
        'handläggningsordningen',
        'inkomstslagen',
        'inköpssamordningen',
        'kapitalunderlagen',
        'klockslagen',
        'kopplingsanordningen',
        'låneformen',
        'mervärdesskatteordningen',
        'nummerordningen',
        'omslagen',
        'ordalagen',
        'pensionsordningen',
        'renhållningsordningen',
        'representationsreformen',
        'rättegångordningen',
        'rättegångsordningen',
        'rättsordningen',
        'samordningen',
        'samordningen',
        'skatteordningen',
        'skatteslagen',
        'skatteunderlagen',
        'skolformen',
        'skyddsanordningen',
        'slagen',
        'solvärmeanordningen',
        'storslagen',
        'studieformen',
        'stödformen',
        'stödordningen',
        'stödordningen',
        'säkerhetsanordningen',
        'talarordningen',
        'tillslagen',
        'tivolianordningen',
        'trafikslagen',
        'transportanordningen',
        'transportslagen',
        'trädslagen',
        'turordningen',
        'underlagen',
        'uniformen',
        'uppställningsformen',
        'utvecklingsbolagen',
        'varuslagen',
        'verksamhetsformen',
        'vevanordningen',
        'vårdformen',
        'ägoanordningen',
        'ägoslagen',
        'ärendeslagen',
        'åtgärdsförslagen',
    ]
    def namedlaw_to_sfsid(self, text, normalize=True):
        if normalize:
            text = self.normalize_lawname(text)

        if text in self.nolaw:
            return None

        if text in self.currentlynamedlaws:
            return self.currentlynamedlaws[text]
        elif text in self.namedlaws:
            # make sure this doesn't return a URI but rather just a SFS id
            sfsid = self.namedlaws[text]
            if sfsid.startswith("http"):
                sfsid = sfsid.rsplit("/", 1)[1]
            return sfsid
        else:
            if self.verbose:
                # print "(unknown): I don't know the ID of named law [%s]" % text
                log.warning(
                    "(unknown): I don't know the ID of named law [%s]" % text)
            return None

    attributemap = {"year": RPUBL.arsutgava,
                    "no": RPUBL.lopnummer,
                    "lawref": RINFOEX.andringsforfattningnummer,
                    "chapter": RPUBL.kapitelnummer,
                    "section": RPUBL.paragrafnummer,
                    "element": RINFOEX.momentnummer,
                    "piece": RINFOEX.styckenummer,
                    "item": RINFOEX.punktnummer,
                    "itemnumeric": RINFOEX.punktnummer,
                    "sentence": RINFOEX.meningnummer,
                    "celex": RPUBL.celexNummer,
                    "artikel": RINFOEX.artikelnummer,
                    "sidnr": RPUBL.sidnummer,
                    "type": RDF.type,
                    "lopnr": RPUBL.lopnummer,
                    "rattsfallspublikation": RPUBL.rattsfallspublikation,
                    "ar": RPUBL.arsutgava,
                    }

    def attributes_to_resource(self, attributes, rest=()):
        g = Graph()
        b = BNode()
        current = b

        # firstly first, clean some degenerate attribute values
        for k in attributes:
            if not isinstance(attributes[k], URIRef):
                v = attributes[k]
                v = v.replace("\xa0", "") # Non-breakable space
                v = v.replace("\n", "")
                v = v.replace("\r", "")
                attributes[k] = v

        # then, try to create any needed sub-nodes representing
        # fragments of a document, starting with the most fine-grained
        # object. It is this subnode that we'll return in the end
        for k in ("sentence", "item", "itemnumeric", "piece",
                  "element", "section", "chapter", "lawref"):
            if k in attributes:
                p = self.attributemap[k]
                leaf = util.uri_leaf(p)
                rel = URIRef(str(p).replace("nummer", ""))
                g.add((current, p, Literal(attributes[k])))
                del attributes[k]
                new = BNode()
                g.add((new, rel, current))
                current = new

        # now, the remaining metadata must be attached to a top-level
        # object (representing a whole document)
        for k, v in attributes.items():
            if k in self.attributemap:
                if not isinstance(v, URIRef):
                    v = Literal(v)
                g.add((current, self.attributemap[k], v))
            else:
                log.error("Can't map attribute %s to RDF predicate" % k)

        # add any extra stuff
        for (p, o) in rest:
            g.add((current, p, o))
        return g.resource(b)

    
    def sfs_format_uri(self, attributes):
        if 'law' not in attributes and not self.allow_relative:
            return None
        piecemappings = {'första': '1',
                         'andra': '2',
                         'tredje': '3',
                         'fjärde': '4',
                         'femte': '5',
                         'sjätte': '6',
                         'sjunde': '7',
                         'åttonde': '8',
                         'nionde': '9'}

        attributeorder = ['law', 'chapter', 'section', 'element',
                          'piece', 'item', 'itemnumeric', 'sentence']

        # possibly complete attributes with data from
        # baseuri_attributes as needed
        if self.allow_relative:
            specificity = False
            for a in attributeorder:
                if a in attributes:
                    specificity = True  # don't complete further than this
                elif (not specificity) and a in self.baseuri_attributes:
                    attributes[a] = self.baseuri_attributes[a]
        # munge attributes a little further to be able to map to RDF
        if 'item' in attributes and 'piece' not in attributes:
            attributes['piece'] = '1'
        if "law" in attributes:
            attributes["year"], attributes["no"] = attributes["law"].split(":")
            del attributes["law"]
            if "s" in attributes["no"]:
                attributes["no"], attributes["sidnr"] = re.split("\s*s\.?\s*", attributes["no"])
        for k in attributes:
            if attributes[k] in piecemappings:
                attributes[k] = piecemappings[attributes[k]]

        # need also to add a rpubl:forfattningssamling triple -- i
        # think this is the place to do it. Problem is how we get
        # access to the URI for SFS -- it can be
        # <https://lagen.nu/dataset/sfs> or
        # <http://rinfo.lagrummet.se/serie/fs/sfs>. The information is
        # available in the config graph, which isn't easily
        # retrievable from self.minter. So we do it the hard way.
        rg = self.minter.space.templates[0].resource.graph
        # get the abbrSlug subproperty. FIXME: do this properly
        abbrSlug = rg.value(predicate=RDF.type, object=RDF.Property)
        fsuri = rg.value(predicate=abbrSlug, object=Literal("sfs"))
        assert fsuri, "Couldn't find URI for forfattningssamling 'sfs'"
        rest = [(RPUBL.forfattningssamling, fsuri)]
        res = self.attributes_to_resource(attributes, rest)
        return self.minter.space.coin_uri(res)

    def format_ChapterSectionRefs(self, root):
        assert(root.tag == 'ChapterSectionRefs')
        assert(len(root.nodes) == 3)  # ChapterRef, wc, SectionRefs

        part = root.nodes[0]
        self.currentchapter = part.nodes[0].text.strip()

        if self.currentlaw:
            res = [self.format_custom_link({'law': self.currentlaw,
                                            'chapter': self.currentchapter},
                                           part.text,
                                           part.tag)]
        else:
            res = [self.format_custom_link({'chapter': self.currentchapter},
                                           part.text,
                                           part.tag)]

        res.extend(self.formatter_dispatch(root.nodes[1]))
        res.extend(self.formatter_dispatch(root.nodes[2]))
        self.currentchapter = None
        return res

    def format_ChapterSectionPieceRefs(self, root):
        assert(root.nodes[0].nodes[0].tag == 'ChapterRefID')
        self.currentchapter = root.nodes[0].nodes[0].text.strip()
        res = []
        for node in root.nodes:
            res.extend(self.formatter_dispatch(node))
        return res

    def format_LastSectionRef(self, root):
        # the last section ref is a bit different, since we want the
        # ending double section mark to be part of the link text
        assert(root.tag == 'LastSectionRef')
        assert(len(root.nodes) == 3)  # LastSectionRefID, wc, DoubleSectionMark
        sectionrefid = root.nodes[0]
        sectionid = sectionrefid.text

        return [self.format_generic_link(root)]

    def format_SectionPieceRefs(self, root):
        assert(root.tag == 'SectionPieceRefs')
        self.currentsection = root.nodes[0].nodes[0].text.strip()

        res = [self.format_custom_link(self.find_attributes([root.nodes[2]]),
                                       "%s %s" % (root.nodes[0]
                                                  .text, root.nodes[2].text),
                                       root.tag)]
        for node in root.nodes[3:]:
            res.extend(self.formatter_dispatch(node))

        self.currentsection = None
        return res

    def format_SectionPieceItemRefs(self, root):
        assert(root.tag == 'SectionPieceItemRefs')
        self.currentsection = root.nodes[0].nodes[0].text.strip()
        self.currentpiece = root.nodes[2].nodes[0].text.strip()

        res = [self.format_custom_link(self.find_attributes([root.nodes[2]]),
                                       "%s %s" % (root.nodes[0]
                                                  .text, root.nodes[2].text),
                                       root.tag)]

        for node in root.nodes[3:]:
            res.extend(self.formatter_dispatch(node))

        self.currentsection = None
        self.currentpiece = None
        return res

    # This is a special case for things like '17-29 och 32 §§ i lagen
    # (2004:575)', which picks out the LawRefID first and stores it in
    # .currentlaw, so that find_attributes finds it
    # automagically. Although now it seems to be branching out and be
    # all things to all people.
    def format_ExternalRefs(self, root):
        assert(root.tag == 'ExternalRefs')
        # print "DEBUG: start of format_ExternalRefs; self.currentlaw is %s" %
        # self.currentlaw

        lawrefid_node = self.find_node(root, 'LawRefID')
        if lawrefid_node is None:
            # Ok, no explicit LawRefID found, lets see if this is a
            # named law that we have the ID for namedlaw_node =
            # self.find_node(root, 'NamedLawExternalLawRef')
            namedlaw_node = self.find_node(root, 'NamedLaw')
            if namedlaw_node is None:
                # As a last chance, this might be a reference back to a previously
                # mentioned law ("...enligt 4 § samma lag")
                samelaw_node = self.find_node(root, 'SameLaw')
                assert(samelaw_node is not None)
                if self.lastlaw is None:
                    log.warning(
                        "(unknown): found reference to \"{samma,nämnda} {lag,förordning}\", but self.lastlaw is not set")
                self.currentlaw = self.lastlaw
            else:
                # the NamedLaw case
                self.currentlaw = self.namedlaw_to_sfsid(namedlaw_node.text)
                if self.currentlaw is None:
                    # unknow law name - in this case it's better to
                    # bail out rather than resolving chapter/paragraph
                    # references relative to baseuri (which is almost
                    # certainly wrong)
                    return [root.text]
        else:
            self.currentlaw = lawrefid_node.text
            if self.find_node(root, 'NamedLaw'):
                namedlaw = self.normalize_lawname(
                    self.find_node(root, 'NamedLaw').text)
                # print "remember that %s is %s!" % (namedlaw, self.currentlaw)
                self.currentlynamedlaws[namedlaw] = self.currentlaw

        # print "DEBUG: middle of format_ExternalRefs; self.currentlaw is %s" %
        # self.currentlaw
        if self.lastlaw is None:
            # print "DEBUG: format_ExternalRefs: setting self.lastlaw to %s" %
            # self.currentlaw
            self.lastlaw = self.currentlaw

        # if the node tree only contains a single reference, it looks
        # better if the entire expression, not just the
        # chapter/section part, is linked. But not if it's a
        # "anonymous" law ('1 § i lagen (1234:234) om blahonga')
        if (len(self.find_nodes(root, 'GenericRefs')) == 1 and
            len(self.find_nodes(root, 'SectionRefID')) == 1 and
                len(self.find_nodes(root, 'AnonymousExternalLaw')) == 0):
            res = [self.format_generic_link(root)]
        else:
            res = self.format_tokentree(root)

        return res

    def format_SectionItemRefs(self, root):
        assert(root.nodes[0].nodes[0].tag == 'SectionRefID')
        self.currentsection = root.nodes[0].nodes[0].text.strip()
        res = self.format_tokentree(root)
        self.currentsection = None
        return res

    def format_PieceItemRefs(self, root):
        self.currentpiece = root.nodes[0].nodes[0].text.strip()
        res = [self.format_custom_link(
            self.find_attributes([root.nodes[2].nodes[0]]),
               "%s %s" % (root.nodes[0].text, root.nodes[2].nodes[0].text),
               root.tag)]
        for node in root.nodes[2].nodes[1:]:
            res.extend(self.formatter_dispatch(node))

        self.currentpiece = None
        return res

    def format_ChapterSectionRef(self, root):
        assert(root.nodes[0].nodes[0].tag == 'ChapterRefID')
        self.currentchapter = root.nodes[0].nodes[0].text.strip()
        return [self.format_generic_link(root)]

    def format_AlternateChapterSectionRefs(self, root):
        assert(root.nodes[0].nodes[0].tag == 'ChapterRefID')
        self.currentchapter = root.nodes[0].nodes[0].text.strip()
        # print "Self.currentchapter is now %s" % self.currentchapter
        res = self.format_tokentree(root)
        self.currentchapter = None
        return res

    def format_ExternalLaw(self, root):
        self.currentchapter = None
        return self.formatter_dispatch(root.nodes[0])

    def format_ChangeRef(self, root):
        id = self.find_node(root, 'LawRefID').data
        return [self.format_custom_link({'lawref': id},
                                        root.text,
                                        root.tag)]

    def format_SFSNr(self, root):
        if self.nobaseuri:
            sfsid = self.find_node(root, 'LawRefID').data
            self.baseuri_attributes = {'law': sfsid}
        return self.format_tokentree(root)

    def format_NamedExternalLawRef(self, root):
        resetcurrentlaw = False
        # print "format_NamedExternalLawRef: self.currentlaw is %r"  % self.currentlaw
        if self.currentlaw is None:
            resetcurrentlaw = True
            lawrefid_node = self.find_node(root, 'LawRefID')
            if lawrefid_node is None:
                self.currentlaw = self.namedlaw_to_sfsid(root.text)
            else:
                self.currentlaw = lawrefid_node.text
                namedlaw = self.normalize_lawname(
                    self.find_node(root, 'NamedLaw').text)
                # print "remember that %s is %s!" % (namedlaw, self.currentlaw)
                self.currentlynamedlaws[namedlaw] = self.currentlaw
            # print "format_NamedExternalLawRef: self.currentlaw is now %r"  %
            # self.currentlaw

        # print "format_NamedExternalLawRef: self.baseuri is %r" % self.baseuri
        # if we can't find a ID for this law, better not <link> it
        if self.currentlaw is None:
            res = [root.text]
        else:
            res = [self.format_generic_link(root)]

        # print "format_NamedExternalLawRef: self.baseuri is %r" % self.baseuri
        if self.nobaseuri and self.currentlaw is not None:
            self.baseuri_attributes = {'law': self.currentlaw,
                                       'chapter': self.currentchapter,
                                       'section': self.currentsection,
                                       'piece': self.currentpiece}
            # remove keys whose value are None or otherwise falsy
            for k in list(self.baseuri_attributes.keys()):
                if not self.baseuri_attributes[k]:
                    del self.baseuri_attributes[k]
        if resetcurrentlaw:
            if self.currentlaw is not None:
                self.lastlaw = self.currentlaw
            self.currentlaw = None
        return res

    #
    # KOD FÖR KORTLAGRUM
    def format_AbbrevLawNormalRef(self, root):
        lawabbr_node = self.find_node(root, 'LawAbbreviation')
        self.currentlaw = self.namedlaw_to_sfsid(
            lawabbr_node.text, normalize=False)
        res = [self.format_generic_link(root)]
        if self.currentlaw is not None:
            self.lastlaw = self.currentlaw
        self.currentlaw = None
        return res

    def format_AbbrevLawShortRef(self, root):
        assert(root.nodes[0].tag == 'LawAbbreviation')
        assert(root.nodes[2].tag == 'ShortChapterSectionRef')
        self.currentlaw = self.namedlaw_to_sfsid(
            root.nodes[0].text, normalize=False)
        shortsection_node = root.nodes[2]
        assert(shortsection_node.nodes[0].tag == 'ShortChapterRefID')
        assert(shortsection_node.nodes[2].tag == 'ShortSectionRefID')
        self.currentchapter = shortsection_node.nodes[0].text
        self.currentsection = shortsection_node.nodes[2].text

        res = [self.format_generic_link(root)]

        self.currentchapter = None
        self.currentsection = None
        self.currentlaw = None
        return res


    # KOD FÖR FORARBETEN
    def forarbete_format_uri(self, attributes):
        a = attributes
        for key, val in list(a.items()):
            if key == 'prop':
                a['type'] = RPUBL.Proposition
                a['year'], a['no'] = val.split(":")
                del a[key]
            elif key == 'bet':
                a['type'] = RINFOEX.Utskottsbetankande
                a['year'], a['no'] = val.split(":")
                del a[key]
            elif key == 'skrivelse':
                # NB: this is different from rpubl:Skrivelse
                a['type'] = RINFOEX.Riksdagsskrivelse
                a['year'], a['no'] = val.split(":")
                del a[key]
            elif key == 'celex':
                if len(val) == 8:  # badly formatted, uses YY instead of YYYY
                    a[key] = val[0] + '19' + val[1:]
        res = self.attributes_to_resource(a)
        return self.minter.space.coin_uri(res)

    def format_ChapterSectionRef(self, root):
        assert(root.nodes[0].nodes[0].tag == 'ChapterRefID')
        self.currentchapter = root.nodes[0].nodes[0].text.strip()
        return [self.format_generic_link(root)]

    #
    # KOD FÖR EULAGSTIFTNING
    def eulag_format_uri(self, attributes):
        # this is a bit simplistic -- we just compute the CELEX number
        # and be done with it. The logic to compute CELEX numbers
        # could be done using coin, but...
        if not 'akttyp' in attributes:
            if 'forordning' in attributes:
                attributes['akttyp'] = 'förordning'
            elif 'direktiv' in attributes:
                attributes['akttyp'] = 'direktiv'
        if 'akttyp' not in attributes:
            raise AttributeError("Akttyp saknas")
        # Om hur CELEX-nummer konstrueras
        # https://www.infotorg.sema.se/infotorg/itweb/handbook/rb/hlp_celn.htm
        # https://www.infotorg.sema.se/infotorg/itweb/handbook/rb/hlp_celf.htm
        # Om hur länkning till EURLEX ska se ut:
        # http://eur-lex.europa.eu/sv/tools/help_syntax.htm
        # Absolut URI?
        fixed = {}
        if 'ar' in attributes and 'lopnummer' in attributes:
            sektor = '3'
            rattslig_form = {'direktiv': 'L',
                             'förordning': 'R'}

            if len(attributes['ar']) == 2:
                attributes['ar'] = '19' + attributes['ar']
            fixed['celex'] = "%s%s%s%04d" % (sektor, attributes['ar'],
                                             rattslig_form[attributes['akttyp']],
                                             int(attributes['lopnummer']))
        else:
            if not self.baseuri_attributes['baseuri'].startswith(res):
                # FIXME: should we warn about this?
                # print "Relative reference, but base context %s is not a celex context" %
                # self.baseuri_attributes['baseuri']
                return None

        if 'artikel' in attributes:
            fixed['artikel'] = attributes['artikel']
            if 'underartikel' in attributes:
                fixed['artikel'] += ".%s" % attributes['underartikel']

        res = self.attributes_to_resource(fixed)
        return self.minter.space.coin_uri(res)


    # KOD FÖR RATTSFALL
    def rattsfall_format_uri(self, attributes):
        if 'nja' in attributes:
            attributes['domstol'] = attributes['nja']
        attributes['rattsfallspublikation'] = URIRef(
            self.namedseries[attributes['domstol']])
        for crap in ('nja', 'njarattsfall', 'rattsfall', 'domstol'):
            if crap in attributes:
                del attributes[crap]
        res = self.attributes_to_resource(attributes)
        return self.minter.space.coin_uri(res)

    #
    # KOD FÖR EURÄTTSFALL
    def eurattsfall_format_uri(self, attributes):
        descriptormap = {'C': 'J',  # Judgment of the Court
                         'T': 'A',  # Judgment of the Court of First Instance
                         'F': 'W',  # Judgement of the Civil Service Tribunal
                         }
        # FIXME: Change this before the year 2054 (as ECJ will
        # hopefully have fixed their case numbering by then)
        if len(attributes['year']) == 2:
            if int(attributes['year']) < 54:
                year = "20" + attributes['year']
            else:
                year = "19" + attributes['year']
        else:
            year = attributes['year']

        attributes['year'] = year
        attributes['serial'] = '%04d' % int(attributes['serial'])
        attributes['descriptor'] = descriptormap[attributes['decision']]
        res = self.attributes_to_resource(attributes)
        return self.minter.coin_uri(res)

class NodeTree:
    """Encapsuates the node structure from mx.TextTools in a tree oriented interface"""
    def __init__(self, root, data, offset=0, isRoot=True):
        self.data = data
        self.root = root
        self.isRoot = isRoot
        self.offset = offset

    def __getattr__(self, name):
        if name == "text":
            return self.data.decode(SP_CHARSET)
        elif name == "tag":
            return (self.isRoot and 'root' or self.root[0])
        elif name == "nodes":
            res = []
            l = (self.isRoot and self.root[1] or self.root[3])
            if l:
                for p in l:
                    res.append(NodeTree(p, self.data[p[1] -
                                                     self.offset:p[2] - self.offset], p[1], False))
            return res
        else:
            raise AttributeError


class RefParseError(Exception):
    pass
