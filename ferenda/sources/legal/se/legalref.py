# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
"""This module finds references to legal sources (including individual
sections, eg 'Upphovsrättslag (1960:729) 49 a §') in plaintext"""

import sys
import os
import re
import hashlib
import logging
import tempfile
# 3rdparty libs

# needed early
from ferenda import util

external_simpleparse_state = None
try:
    from simpleparse.parser import Parser
    from simpleparse.stt.TextTools.TextTools import tag
except ImportError:
    # Mimic the simpleparse interface (the very few parts we're using)
    # but call external python 2.7 processes behind the scene.

    external_simpleparse_state = tempfile.mkdtemp()
    # external_simpleparse_state = "simpleparse.tmp"
    python_exe = os.environ.get("FERENDA_PYTHON2_FALLBACK",
                                "python2.7")
    buildtagger_script = external_simpleparse_state + os.sep + "buildtagger.py"
    util.writefile(buildtagger_script, """import sys,os
if sys.version_info >= (3,0,0):
    raise OSError("This is python %s, not python 2.6 or 2.7!" % sys.version_info)
declaration = sys.argv[1] # md5 sum of the entire content of declaration
production = sys.argv[2]  # short production name
picklefile = "%s-%s.pickle" % (declaration, production)
from simpleparse.parser import Parser
from simpleparse.stt.TextTools.TextTools import tag
import cPickle as pickle

with open(declaration,"rb") as fp:
    p = Parser(fp.read(), production)
t = p.buildTagger(production)
with open(picklefile,"wb") as fp:
    pickle.dump(t,fp)""")

    tagstring_script = external_simpleparse_state + os.sep + "tagstring.py"
    util.writefile(tagstring_script, """import sys, os
if sys.version_info >= (3,0,0):
    raise OSError("This is python %s, not python 2.6 or 2.7!" % sys.version_info)
pickled_tagger = sys.argv[1] # what buildtagger.py returned -- full path
full_text_path = sys.argv[2] 
text_checksum = sys.argv[3] # md5 sum of text, just the filename
picklefile = "%s-%s.pickle" % (pickled_tagger, text_checksum) 

from simpleparse.stt.TextTools.TextTools import tag

import cPickle as pickle

with open(pickled_tagger) as fp:
    t = pickle.load(fp)
with open(full_text_path, "rb") as fp:
    text = fp.read()
tagged = tag(text, t, 0, len(text))
with open(picklefile,"wb") as fp:
    pickle.dump(tagged,fp)
        """)

    class Parser(object):

        def __init__(self, declaration, root='root', prebuilts=(), definitionSources=[]):
            # 2. dump declaration to a tmpfile read by the script
            c = hashlib.md5()
            c.update(declaration)
            self.declaration_md5 = c.hexdigest()
            declaration_filename = "%s/%s" % (external_simpleparse_state,
                                              self.declaration_md5)
            with open(declaration_filename, "wb") as fp:
                fp.write(declaration)

        def buildTagger(self, production=None, processor=None):
            pickled_tagger = "%s/%s-%s.pickle" % (external_simpleparse_state,
                                                  self.declaration_md5,
                                                  production)
            if not os.path.exists(pickled_tagger):

                #    3. call the script with python 27 and production
                cmdline = "%s %s %s/%s %s" % (python_exe,
                                              buildtagger_script,
                                              external_simpleparse_state,
                                              self.declaration_md5,
                                              production)
                util.runcmd(cmdline, require_success=True)
                #    4. the script builds tagtable and dumps it to a pickle file
                assert os.path.exists(pickled_tagger)
            return pickled_tagger  # filename instead of tagtable struct

    def tag(text, tagtable, sliceleft, sliceright):
        c = hashlib.md5()
        c.update(text)
        text_checksum = c.hexdigest()
        pickled_tagger = tagtable  # remember, not a real tagtable struct
        pickled_tagged = "%s-%s.pickle" % (pickled_tagger, text_checksum)

        if not os.path.exists(pickled_tagged):
            # 2. Dump text as string
            full_text_path = "%s/%s.txt" % (os.path.dirname(pickled_tagger),
                                            text_checksum)
            with open(full_text_path, "wb") as fp:
                fp.write(text)
                # 3. call script (that loads the pickled tagtable + string
                # file, saves tagged text as pickle)
            util.runcmd("%s %s %s %s %s" %
                        (python_exe,
                         tagstring_script,
                         pickled_tagger,
                         full_text_path,
                         text_checksum),
                        require_success=True)
        # 4. load tagged text pickle
        with open(pickled_tagged, "rb") as fp:
            res = pickle.load(fp)
        return res


import six
from six.moves import cPickle as pickle

from rdflib import Graph
from rdflib import Namespace
from rdflib import RDFS

# my own libraries

from ferenda.elements import Link
from ferenda.elements import LinkSubject

# The charset used for the bytestrings that is sent to/from
# simpleparse (which does not handle unicode)
# Choosing utf-8 makes § a two-byte character, which does not work well
SP_CHARSET = 'iso-8859-1'

log = logging.getLogger('lr')


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


class ParseError(Exception):

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)

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
    # KORTLAGRUM | FORESKRIFTER | EGLAGSTIFTNING)
    LAGRUM = 1             # hänvisningar till lagrum i SFS
    KORTLAGRUM = 2         # SFS-hänvisningar på kortform
    FORESKRIFTER = 3       # hänvisningar till myndigheters författningssamlingar
    EGLAGSTIFTNING = 4     # EG-fördrag, förordningar och direktiv
    INTLLAGSTIFTNING = 5   # Fördrag, traktat etc
    FORARBETEN = 6         # proppar, betänkanden, etc
    RATTSFALL = 7          # Rättsfall i svenska domstolar
    MYNDIGHETSBESLUT = 8   # Myndighetsbeslut (JO, ARN, DI...)
    EGRATTSFALL = 9        # Rättsfall i EG-domstolen/förstainstansrätten
    INTLRATTSFALL = 10     # Europadomstolen

    # re_urisegments = re.compile(r'([\w]+://[^/]+/[^\d]*)(\d+:(bih\.
    # |N|)?\d+( s\.\d+|))#?(K(\d+)|)(P(\d+)|)(S(\d+)|)(N(\d+)|)')
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

        self.graph = Graph()
        n3file = os.path.relpath(scriptdir + "/../../../res/etc/sfs-extra.n3")
        # print "loading n3file %s" % n3file
        self.graph.load(n3file, format="n3")
        self.roots = []
        self.uriformatter = {}
        self.decl = ""  # try to make it unicode clean all the way
        self.namedlaws = {}
        self.load_ebnf(scriptdir + "/../../../res/etc/base.ebnf")

        self.args = args
        if self.LAGRUM in args:
            productions = self.load_ebnf(scriptdir + "/../../../res/etc/lagrum.ebnf")
            for p in productions:
                self.uriformatter[p] = self.sfs_format_uri
            self.namedlaws.update(self.get_relations(RDFS.label))
            self.roots.append("sfsrefs")
            self.roots.append("sfsref")

        if self.KORTLAGRUM in args:
            # om vi inte redan laddat lagrum.ebnf måste vi göra det
            # nu, eftersom kortlagrum.ebnf beror på produktioner som
            # definerats där
            if not self.LAGRUM in args:
                self.load_ebnf(scriptdir + "/../../../res/etc/lagrum.ebnf")

            productions = self.load_ebnf(
                scriptdir + "/../../../res/etc/kortlagrum.ebnf")
            for p in productions:
                self.uriformatter[p] = self.sfs_format_uri
            DCT = Namespace("http://purl.org/dc/terms/")
            d = self.get_relations(DCT['alternate'])
            self.namedlaws.update(d)
            # lawlist = [x.encode(SP_CHARSET) for x in list(d.keys())]
            lawlist = list(d.keys())
            # Make sure longer law abbreviations come before shorter
            # ones (so that we don't mistake "3 § MBL" for "3 § MB"+"L")
            # lawlist.sort(cmp=lambda x, y: len(y) - len(x))
            lawlist.sort(key=len, reverse=True)
            lawdecl = "LawAbbreviation ::= ('%s')\n" % "'/'".join(lawlist)
            self.decl += lawdecl
            self.roots.insert(0, "kortlagrumref")

        if self.EGLAGSTIFTNING in args:
            productions = self.load_ebnf(scriptdir + "/../../../res/etc/eglag.ebnf")
            for p in productions:
                self.uriformatter[p] = self.eglag_format_uri
            self.roots.append("eglagref")
        if self.FORARBETEN in args:
            productions = self.load_ebnf(
                scriptdir + "/../../../res/etc/forarbeten.ebnf")
            for p in productions:
                self.uriformatter[p] = self.forarbete_format_uri
            self.roots.append("forarbeteref")
        if self.RATTSFALL in args:
            productions = self.load_ebnf(scriptdir + "/../../../res/etc/rattsfall.ebnf")
            for p in productions:
                self.uriformatter[p] = self.rattsfall_format_uri
            self.roots.append("rattsfallref")
        if self.EGRATTSFALL in args:
            productions = self.load_ebnf(scriptdir + "/../../../res/etc/egratt.ebnf")
            for p in productions:
                self.uriformatter[p] = self.egrattsfall_format_uri
            self.roots.append("ecjcaseref")

        rootprod = "root ::= (%s/plain)+\n" % "/".join(self.roots)
        self.decl += rootprod

        self.parser = Parser(self.decl.encode(SP_CHARSET), "root")
        self.tagger = self.parser.buildTagger("root")
        # util.writefile("tagger.tmp", repr(self.tagger), SP_CHARSET)
        # print "tagger length: %d" % len(repr(self.tagger))
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
        return [x.group(1) for x in re.finditer(r'(\w+(Ref|RefID))\s*::=', content)]

    def get_relations(self, predicate):
        d = {}
        for obj, subj in self.graph.subject_objects(predicate):
            d[six.text_type(subj)] = six.text_type(obj)
        return d

    def parse(self, indata, baseuri="http://rinfo.lagrummet.se/publ/sfs/9999:999#K9P9S9P9", predicate=None):
        assert isinstance(indata, six.text_type)
        if indata == "":
            return indata  # this actually triggered a bug...
        # h = hashlib.sha1()
        # h.update(indata)
        # print "Called with %r (%s) (%s)" % (indata, h.hexdigest(), self.verbose)
        self.predicate = predicate
        self.baseuri = baseuri
        if baseuri:
            m = self.re_urisegments.match(baseuri)
            if m:
                self.baseuri_attributes = {'baseuri': m.group(1),
                                           'law': m.group(2),
                                           'chapter': m.group(6),
                                           'section': m.group(8),
                                           'piece': m.group(10),
                                           'item': m.group(12)}
            else:
                self.baseuri_attributes = {'baseuri': baseuri}
        else:
            self.baseuri_attributes = {}
        # Det är svårt att få EBNF-grammatiken att känna igen
        # godtyckliga ord som slutar på ett givet suffix (exv
        # 'bokföringslagen' med suffixet 'lagen'). Därför förbehandlar
        # vi indatasträngen och stoppar in ett '|'-tecken innan vissa
        # suffix. Vi transformerar även 'Radio- och TV-lagen' till
        # 'Radio-_och_TV-lagen'
        #
        # FIXME: Obviously, this shouldn't be done in a general class,
        # but rather in a subclas or via proxy/adapter

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

            raise ParseError(
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
        # print "Changing %r to a %r" % (m.group(0)[2:-1], unichr(int(m.group(0)[2:-1])))
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
            # Normal error from eglag_format_uri
            return part.text
        except:
            exc = sys.exc_info()
            # If something else went wrong, just return the plaintext
            log.warning("(unknown): Unable to format link for text %s (production %s)" %
                        (part.text, part.tag))
            return part.text

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
            return part.text
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
        # return sfsid.replace('s. ','').replace('s.','') # more advanced normalizations to come...
        return sfsid

    def normalize_lawname(self, lawname):
        lawname = lawname.replace('|', '').replace('_', ' ').lower()
        if lawname.endswith('s'):
            lawname = lawname[:-1]
        return lawname

    def namedlaw_to_sfsid(self, text, normalize=True):
        if normalize:
            text = self.normalize_lawname(text)

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
        if text in nolaw:
            return None

        if text in self.currentlynamedlaws:
            return self.currentlynamedlaws[text]
        elif text in self.namedlaws:
            return self.namedlaws[text]
        else:
            if self.verbose:
                # print "(unknown): I don't know the ID of named law [%s]" % text
                log.warning(
                    "(unknown): I don't know the ID of named law [%s]" % text)
            return None

    def sfs_format_uri(self, attributes):
        piecemappings = {'första': '1',
                         'andra': '2',
                         'tredje': '3',
                         'fjärde': '4',
                         'femte': '5',
                         'sjätte': '6',
                         'sjunde': '7',
                         'åttonde': '8',
                         'nionde': '9'}
        keymapping = {'lawref': 'L',
                      'chapter': 'K',
                      'section': 'P',
                      'piece': 'S',
                      'item': 'N',
                      'itemnumeric': 'N',
                      'element': 'O',
                      'sentence': 'M',  # is this ever used?
                      }
        attributeorder = ['law', 'lawref', 'chapter', 'section',
                          'element', 'piece', 'item', 'itemnumeric', 'sentence']

        if 'law' in attributes:
            if attributes['law'].startswith('http://'):
                res = ''
            else:
                res = 'http://rinfo.lagrummet.se/publ/sfs/'

        else:
            if 'baseuri' in self.baseuri_attributes:
                res = self.baseuri_attributes['baseuri']
            else:
                res = ''
        resolvetobase = True
        addfragment = False
        justincase = None
        for key in attributeorder:
            if key in attributes:
                resolvetobase = False
                val = attributes[key]
            elif (resolvetobase and key in self.baseuri_attributes):
                val = self.baseuri_attributes[key]
            else:
                val = None

            if val:
                if not isinstance(val, six.text_type):
                    val = val.decode(SP_CHARSET)
                if addfragment:
                    res += '#'
                    addfragment = False
                if (key in ['piece', 'itemnumeric', 'sentence'] and val in piecemappings):
                    res += '%s%s' % (
                        keymapping[key], piecemappings[val.lower()])
                else:
                    if key == 'law':
                        val = self.normalize_sfsid(val)
                        val = val.replace(" ", "_")
                        res += val
                        addfragment = True
                    else:
                        if justincase:
                            res += justincase
                            justincase = None
                        val = val.replace(" ", "")
                        val = val.replace("\n", "")
                        val = val.replace("\r", "")
                        res += '%s%s' % (keymapping[key], val)
            else:
                if key == 'piece':
                    justincase = "S1"
        return res

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
        # print "DEBUG: start of format_ExternalRefs; self.currentlaw is %s" % self.currentlaw

        lawrefid_node = self.find_node(root, 'LawRefID')
        if lawrefid_node is None:
            # Ok, no explicit LawRefID found, lets see if this is a named law that we have the ID for
            # namedlaw_node = self.find_node(root, 'NamedLawExternalLawRef')
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

        # print "DEBUG: middle of format_ExternalRefs; self.currentlaw is %s" % self.currentlaw
        if self.lastlaw is None:
            # print "DEBUG: format_ExternalRefs: setting self.lastlaw to %s" % self.currentlaw
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
        # res = self.formatter_dispatch(root.nodes[0]) # was formatter_dispatch(self.root)
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
        if self.baseuri is None:
            sfsid = self.find_node(root, 'LawRefID').data
            baseuri = 'http://rinfo.lagrummet.se/publ/sfs/%s#' % sfsid.decode(SP_CHARSET)
            self.baseuri_attributes = {'baseuri': baseuri}

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
            # print "format_NamedExternalLawRef: self.currentlaw is now %r"  % self.currentlaw

        # print "format_NamedExternalLawRef: self.baseuri is %r" % self.baseuri
        if self.currentlaw is None:  # if we can't find a ID for this law, better not <link> it
            res = [root.text]
        else:
            res = [self.format_generic_link(root)]

        # print "format_NamedExternalLawRef: self.baseuri is %r" % self.baseuri
        if self.baseuri is None and self.currentlaw is not None:
            # print "format_NamedExternalLawRef: setting baseuri_attributes"
            # use this as the new baseuri_attributes
            m = self.re_urisegments.match(self.currentlaw)
            if m:
                self.baseuri_attributes = {'baseuri': m.group(1),
                                           'law': m.group(2),
                                           'chapter': m.group(6),
                                           'section': m.group(8),
                                           'piece': m.group(10),
                                           'item': m.group(12)}
            else:
                self.baseuri_attributes = {
                    'baseuri': 'http://rinfo.lagrummet.se/publ/sfs/' + self.currentlaw + '#'}

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

    #
    # KOD FÖR FORARBETEN
    def forarbete_format_uri(self, attributes):
        # res = self.baseuri_attributes['baseuri']
        res = 'http://rinfo.lagrummet.se/'
        resolvetobase = True
        addfragment = False

        for key, val in list(attributes.items()):
            if key == 'prop':
                res += "publ/prop/%s" % val
            elif key == 'bet':
                res += "publ/bet/%s" % val
            elif key == 'skrivelse':
                res += "publ/rskr/%s" % val
            elif key == 'celex':
                if len(val) == 8:  # incorrectly formatted, uses YY instead of YYYY
                    val = val[0] + '19' + val[1:]
                res += "ext/eur-lex/%s" % val
        if 'sidnr' in attributes:
            res += "#s%s" % attributes['sidnr']

        return res

    def format_ChapterSectionRef(self, root):
        assert(root.nodes[0].nodes[0].tag == 'ChapterRefID')
        self.currentchapter = root.nodes[0].nodes[0].text.strip()
        return [self.format_generic_link(root)]

    #
    # KOD FÖR EGLAGSTIFTNING
    def eglag_format_uri(self, attributes):
        res = 'http://rinfo.lagrummet.se/ext/celex/'
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
        if 'ar' in attributes and 'lopnummer' in attributes:
            sektor = '3'
            rattslig_form = {'direktiv': 'L',
                             'förordning': 'R'}

            if len(attributes['ar']) == 2:
                attributes['ar'] = '19' + attributes['ar']
            res += "%s%s%s%04d" % (sektor, attributes['ar'],
                                   rattslig_form[attributes['akttyp']],
                                   int(attributes['lopnummer']))
        else:
            if not self.baseuri_attributes['baseuri'].startswith(res):
                # FIXME: should we warn about this?
                # print "Relative reference, but base context %s is not a celex context" %
                # self.baseuri_attributes['baseuri']
                return None

        if 'artikel' in attributes:
            res += "#%s" % attributes['artikel']
            if 'underartikel' in attributes:
                res += ".%s" % attributes['underartikel']

        return res

    #
    # KOD FÖR RATTSFALL
    def rattsfall_format_uri(self, attributes):
        # Listan härledd från containers.n3/rattsfallsforteckningar.n3 i
        # rinfoprojektets källkod - en ambitiösare lösning vore att läsa
        # in de faktiska N3-filerna i en rdflib-graf.
        containerid = {'NJA': '/publ/rattsfall/nja/',
                       'RH': '/publ/rattsfall/rh/',
                       'MÖD': '/publ/rattsfall/mod/',
                       'RÅ': '/publ/rattsfall/ra/',
                       'RK': '/publ/rattsfall/rk/',
                       'MIG': '/publ/rattsfall/mig/',
                       'AD': '/publ/rattsfall/ad/',
                       'MD': '/publ/rattsfall/md/',
                       'FÖD': '/publ/rattsfall/fod/'}

        # res = self.baseuri_attributes['baseuri']
        if 'nja' in attributes:
            attributes['domstol'] = attributes['nja']

        assert 'domstol' in attributes, "No court provided"
        assert attributes[
            'domstol'] in containerid, "%s is an unknown court" % attributes['domstol']
        res = "http://rinfo.lagrummet.se" + containerid[attributes['domstol']]

        if 'lopnr' in attributes and ":" in attributes['lopnr']:
            (attributes['ar'], attributes['lopnr']) = lopnr.split(":", 1)

        if attributes['domstol'] == 'NJA':
            # FIXME: URIs should be based on publikationsordinal, not
            # pagenumber (which this in effect is) - but this requires
            # a big lookup table/database/graph with
            # pagenumber-to-ordinal-mappings
            res += '%ss%s' % (attributes['ar'], attributes['sidnr'])
        else:
            res += '%s:%s' % (attributes['ar'], attributes['lopnr'])

        return res

    #
    # KOD FÖR EGRÄTTSFALL
    def egrattsfall_format_uri(self, attributes):
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

        serial = '%04d' % int(attributes['serial'])
        descriptor = descriptormap[attributes['decision']]
        uri = "http://lagen.nu/ext/celex/6%s%s%s" % (year, descriptor, serial)
        return uri
