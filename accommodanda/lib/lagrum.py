"""Recognize references to legal sources in document body text.

Lark-based replacement for the old legalref.py. Like the old
`LegalRef(*parse_types)`, the recognizer is configured with a *set* of
parse types and only compiles the grammar, root alternatives and trigger
patterns those types need -- so an SFS context (LAGRUM + EULAGSTIFTNING)
does not pay for the abbreviated-ref or EU-caselaw machinery a court
decision wants. Ported so far: LAGRUM, KORTLAGRUM, EULAGSTIFTNING (the
remaining old types -- FORARBETEN, RATTSFALL, EURATTSFALL, ENKLALAGRUM,
MYNDIGHETSBESLUT -- plug in via the ROOTS / RULES / TRIGGER_SRC tables).

Differences from the old engine:

- No preprocessing escapes: the old simpleparse grammar could not match
  "any word ending in -lagen", so the input was mangled with '|' markers
  and "X- och Y-lagen" was rewritten to "X-_och_Y-lagen" (which sometimes
  leaked into output). Lark regex terminals match the suffixes directly.
- Context for relative references ("tredje stycket 4") comes from the
  structural position of the text node, not from regex-decomposing a
  previously minted URI.
- URIs are formatted directly instead of via COIN template minting.

Scanning works like the old root production root ::= (ref/plain)+ did:
a trigger regex proposes candidate start positions, and at each trigger
the Lark parser matches the longest reference expression anchored there
(retrying on a truncated window when trailing text does not parse).
Matched spans are consumed; the rest is plain text.

URI fragment letters (as produced by the old COIN templates, observed in
the golden corpus): K kapitel, P paragraf, O mom., S stycke, N punkt,
M mening, L ändringsförfattning.
"""

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple

from lark import Lark, Token, Tree
from lark.exceptions import UnexpectedInput

from . import datasets
from .coe_ids import article_fragment as coe_article_fragment
from .util import fold_swedish

# --- parse-type configuration ---------------------------------------
#
# The recognizer is configured with a *set* of parse types (the old
# LegalRef bitflags, as names). Only the requested types' grammar rules,
# root alternatives and trigger patterns are compiled, so a context that
# only needs SFS refs does not pay for the EU or förarbete machinery
# (smaller grammar, cheaper trigger scan). New parse types plug in by
# adding an entry to ROOTS / RULES / TRIGGER_SRC (and DEPENDS if they
# reuse another type's productions); the formatter methods already
# dispatch by node name.

# the EU-act namespace, spelled once. An act's uri is CELEX_BASE + its CELEX;
# CELEX_LOCAL is the prefix `catalog.local()` leaves in front of it. This module
# owns it because `celex_uri` mints these uris, and it is low enough in the
# import graph for every consumer to reach (rule:second-use-goes-to-lib -- it
# had reached seven hand-written copies).
CELEX_LOCAL = 'ext/celex/'
CELEX_BASE = 'https://lagen.nu/' + CELEX_LOCAL

# the Strasbourg case-law namespace, spelled once for the same reason: a case's
# uri is ECHR_BASE + its HUDOC item id. The hudoc source mints these, and the
# wiki resolves a commentary's `annotates:` onto them, so neither side can own
# the constant (a source never imports a sibling source) and both would
# otherwise hand-write it -- with the annotation silently matching no host if
# either copy moved.
ECHR_BASE = 'https://lagen.nu/dom/echr/'

# printed författningssamling designation -> the slug its documents live under
# ("ÅFS" -> aafs, "ELSÄK-FS" -> elsakfs). The hand-edited registry
# (foreskrift/data/series.json via lib.datasets) is the one source: the grammar
# terminal and this lookup are built from it together, so the two cannot drift
# and the engine mints no uri for a series the corpus does not know.
FS_SLUG = {entry["designation"]: slug
           for slug, entry in datasets.load_fs_series().items()}
FS_DESIGNATIONS = sorted(FS_SLUG)

LAGRUM = 'LAGRUM'                  # SFS references ("3 kap. 2 § lagen …")
KORTLAGRUM = 'KORTLAGRUM'          # abbreviated SFS refs ("3 § MBL", "JB 22:2")
EULAGSTIFTNING = 'EULAGSTIFTNING'  # EU treaties/regulations/directives
RATTSFALL = 'RATTSFALL'            # Swedish case law ("NJA 1994 s. 12")
FORARBETEN = 'FORARBETEN'          # prop./bet./rskr./SOU/Ds/celex + page refs
EURATTSFALL = 'EURATTSFALL'        # CJEU case law ("mål C-176/09")
MYNDIGHETSBESLUT = 'MYNDIGHETSBESLUT'  # JO/JK/ARN decisions (by diarienummer)
VAGLEDNING = 'VAGLEDNING'          # EDPB guidance ("Riktlinjer 05/2020", "WP 248")
FORESKRIFT = 'FORESKRIFT'          # agency regulations ("PMFS 2022:1", "ELSÄK-FS 2008:1")
ENKLALAGRUM = 'ENKLALAGRUM'        # absolute-only SFS refs (förarbete-safe)

# deterministic assembly order; kortlagrum first so its roots take
# precedence in the ?ref alternation (an abbreviated form must win over a
# bare generic ref that would leave the abbreviation unconsumed)
TYPE_ORDER = [KORTLAGRUM, ENKLALAGRUM, LAGRUM, EULAGSTIFTNING, RATTSFALL,
              FORARBETEN, EURATTSFALL, MYNDIGHETSBESLUT, VAGLEDNING, FORESKRIFT]

# The "everything" configuration for verticals that link every reference
# flavour (dv, forarbete, avg, wiki): all parse types except ENKLALAGRUM,
# which is the deliberately restricted *alternative* to LAGRUM, never
# combined with it. Import this instead of copying the list.
ALL_PARSE_TYPES = [LAGRUM, KORTLAGRUM, EULAGSTIFTNING, RATTSFALL,
                   FORARBETEN, EURATTSFALL, MYNDIGHETSBESLUT, VAGLEDNING,
                   FORESKRIFT]

# types each requested type pulls in (kortlagrum/enklalagrum reuse the
# generic_ref / external_law / piece_ref productions defined by lagrum)
DEPENDS = {KORTLAGRUM: [LAGRUM], ENKLALAGRUM: [LAGRUM]}

# root-rule alternatives each parse type contributes to ?ref
ROOTS = {
    KORTLAGRUM: ['kortlagrum_short', 'kortlagrum_normal'],
    LAGRUM: ['change_ref', 'external_refs', 'external_ref',
             'multiple_generic_refs', 'sfs_nr', 'named_external_law_ref',
             'piece_item_refs', 'piece_item_ref', 'piece_and_item_refs'],
    EULAGSTIFTNING: ['eu_ref'],
    RATTSFALL: ['nja_notis', 'court_notis', 'nja_referat', 'court_referat'],
    FORARBETEN: ['forarb_refs', 'anon_prop_refs', 'avsnitt_external',
                 'avsnitt_list', 'forarb_doc'],
    EURATTSFALL: ['ecj_ref'],
    MYNDIGHETSBESLUT: ['arn_refs', 'jo_refs', 'jk_refs'],
    VAGLEDNING: ['riktlinje_ref', 'rekommendation_ref', 'wp_ref'],
    FORESKRIFT: ['foreskrift_ref'],
    # absolute SFS forms only -- a bare relative ref ("3 §") has no root,
    # so it stays unlinked (the point of the förarbete-safe subset)
    ENKLALAGRUM: ['external_refs', 'external_ref', 'named_external_law_ref',
                  'sfs_nr'],
}

LAGRUM_RULES = r"""
// "Lag (2021:952)." -- change note; links to #L2021:952 on the base act
change_ref.10: CHANGE_WORD _W sfs_nr DOT?

// "5 § andra stycket lagen (1998:204) om ..." / "4 § samma lag"
external_ref.6: generic_ref _W external_law
// "17-29 och 32 §§ i lagen (2004:575)"
external_refs.7: multiple_generic_refs _W (IN _W)? external_law

?external_law: anonymous_external_law | named_external_law_ref | same_law
anonymous_external_law: (IN _W)? LAW_SYNONYM _W sfs_nr
named_external_law_ref: NAMED_LAW (_W sfs_nr)?
same_law: SAME_LAW

sfs_nr: LPAR _W? law_ref_id _W? RPAR
law_ref_id: LAW_REF_ID

// --- generic (chapter/section/piece/item/sentence) references ---

multiple_generic_refs.4: generic_unit ((COMMA _W AND _W | COMMA _W | _W_AND_OR_W) generic_unit)*

?generic_unit: individual_chapter_section_refs
             | chapter_section_refs
             | chapter_section_piece_refs
             | section_refs
             | section_piece_item_range
             | section_piece_refs
             | section_item_refs
             | generic_ref

// "8 kap. 1 §, 2 § och 6 §" -- one combined chapter+section link, then
// per-section links sharing the (sticky, never reset) chapter
individual_chapter_section_refs.5: chapter_ref _W section_ref (COMMA _W section_ref)* _W AND _W section_ref

// one link carrying every collected attribute
generic_ref: chapter_ref _W section_anatomy
           | section_anatomy
           | chapter_ref

section_anatomy: section_ref (_W piece_ref)? (_W item_ref)? (_W sentence_ref)? (_W element_ref)?
               | section_ref _W itemnumeric_ref _W piece_ref

// "8 kap. 2, 4-6 och 8 §§" / "9 kap. 15 eller 16 §"
chapter_section_refs: chapter_ref _W section_refs
                    | chapter_ref _W alternate_section_refs

// "2, 4-6 och 8 §§" -- one link per endpoint
section_refs: sec_item ((COMMA _W | _W_AND_OR_W) sec_item)* _W DSM

// "15 eller 16 §" -- single section mark, only valid after a chapter
// and with a final "eller" (otherwise "2 kap. 5 §" would match here
// instead of as one combined generic_ref)
alternate_section_refs: (sec_item COMMA _W)* sec_item _W_OR_W section_ref

?sec_item: interval_section | single_section_ref
interval_section: single_section_ref _W? HYP HYP? _W? single_section_ref
single_section_ref: section_ref_id

// "2 § första och tredje styckena" / "1 § första eller andra stycket" /
// "3 § fjärde stycket 2 eller femte stycket" (non-final pieces may be
// bare ordinals or piece+item, like the old PieceRefID/PieceItemRef)
section_piece_refs: section_ref _W (piece_unit COMMA _W)* piece_unit _W_AND_OR_W (piece_item_unit | piece_ref)
?piece_unit: piece_item_unit | piece_ref | bare_piece_ref
piece_item_unit: piece_ref _W item_ref
bare_piece_ref: piece_ref_id

// "3 § andra stycket 2-4" -- piece link plus both item endpoints
section_piece_item_range: section_ref _W piece_ref _W item_ref _W? HYP _W? item_ref

// "2 kap. 1 § första eller andra stycket" -- emits a chapter link and
// keeps the chapter sticky for following units (the old
// format_ChapterSectionPieceRefs never reset currentchapter)
chapter_section_piece_refs: chapter_ref _W section_piece_refs

// "6 § 1 och 2" -- a section link plus one link per item
section_item_refs: section_ref _W item_ref _W_AND_OR_W item_ref

// "första stycket 4" -- one link; "första stycket och 3" -- two links.
// The reversed "26 första stycket" (ItemNumericRef PieceRef) makes one
// item-link -- the old engine read "§ 26 första stycket" that way
piece_item_ref: piece_ref _W item_ref
              | itemnumeric_ref _W piece_ref
              | piece_ref
piece_and_item_refs: piece_ref _W_AND_OR_W item_ref

// "tredje stycket 2, 3 eller 4 b" -- one link per item, sharing the piece
piece_item_refs: piece_ref _W item_ref (COMMA _W item_ref)* _W_AND_OR_W item_ref

chapter_ref: chapter_ref_id KAP
chapter_ref_id: NUMBER _W (CHAPTER_CHAR _W)?
section_ref: section_ref_id _W SM
section_ref_id: NUMBER (_W SECTION_CHAR)?
piece_ref: piece_ref_id _W PIECE_WORD
piece_ref_id: ORDINAL_WORD | PIECE_DIGIT
sentence_ref: sentence_ref_id _W SENTENCE_WORD
sentence_ref_id: ORDINAL_WORD | PIECE_DIGIT
element_ref: element_ref_id _W MOM
element_ref_id: NUMBER
item_ref: ANVISNINGARNA? item_ref_id DOT? RPAR?
        | itemnumeric_ref_id _W PUNKTEN
item_ref_id: NUMBER (_W ITEM_CHAR)? | ITEM_CHAR
itemnumeric_ref: ANVISNINGARNA? itemnumeric_ref_id DOT? RPAR?
itemnumeric_ref_id: ORDINAL_WORD | NUMBER
"""

EU_RULES = r"""
// --- EU legislation (eulag.ebnf) ---

eu_ref: artikel_part _W IN _W rattsakt_part
      | skal_part _W IN _W rattsakt_part
      | skal_part _W_AND_OR_W artikel_part _W IN _W rattsakt_part
      | rattsakt_part
      | artikel_part

// a recital ("skäl 108"), which is where an act states the reasoning its
// articles enact and is cited for exactly that -- "i skäl 108 och artikel 46.1
// i allmänna dataskyddsförordningen föreskrivs att …". It links only where an
// act is named or in focus: unlike an article, a bare "skäl 12" is not
// anaphora-linked, because these documents number their own paragraphs the same
// way and a bare number is far likelier to be one of those than a recital of
// whatever act was last mentioned.
//
// The coordinated form is its own alternative because Swedish hangs one "i
// <akt>" off both halves, and the recital must take the act the article names
// rather than go unlinked. Only that order: "skäl … och artikel … i X" is
// grammar, "artikel … och skäl … i X" is not, and in the corpus the recital
// leads. The reverse still links the recital and the act, losing only the
// article's pinpoint. A range follows `_asep` like an article range, so
// "skälen 108-110" links the endpoints, not the span.
skal_part: SKAL _W skal_item (_asep skal_item)*
skal_item: skal_ref_id
skal_ref_id: NUMBER

// one or more articles: a single "artikel N(.M)", a coordinated list
// ("artiklarna 101 och 102", "artiklarna 12, 13 och 14") or a range
// ("artiklarna 12–15", whose endpoints each link). Each item is its own link.
artikel_part: (ARTIKEL | ARTIKLARNA) _W artikel_item (_asep artikel_item)*
// The letter is part of the item, with or without a sub-article ("6.1 c",
// "3 a"). Gating it on a preceding sub-article was tried and reverted: refusing
// the token made the whole reference fail to match on the named-act and treaty
// paths -- "artikel 6 c i Europakonventionen" returned nothing at all, losing
// the treaty link rather than merely the pinpoint. See `_article_specs` for why
// the sub-article-less form is read as a point.
// The stycke (sub-paragraph) sits between the sub-article and the lettered
// point, which is the order the acts use: "artikel 3.1 tredje stycket a" is 460
// of the 496 sampled orderings. It anchors `#9.2.S2`, the id the eurlex parser
// mints for that stycke -- but only where no letter follows, since a point is
// anchored by its paragraph whichever stycke holds it. Without this production
// the whole reference used to fail, losing the *article* too and degrading to an
// act-level link.
artikel_item: artikel_ref_id (DOT underartikel_ref_id)? (_W stycke_ref)? (_W punkt_ref_id (_asep punkt_ref_id)*)?
stycke_ref: stycke_ref_id _W PIECE_WORD
stycke_ref_id: ORDINAL_WORD
_asep: _W_AND_OR_W | HYP | COMMA _W
artikel_ref_id: NUMBER
underartikel_ref_id: NUMBER
punkt_ref_id: PUNKT_LETTER

rattsakt_part: institution _W akttyp _W (direktiv_part | forordning_part) (_W av_datum)?
             | akttyp _W (direktiv_part | forordning_part) (_W av_datum)?
             | direktiv_part
             | forordning_part
institution: RADETS | EP_RADETS | KOMMISSIONENS
akttyp: DIREKTIV | FORORDNING | REKOMMENDATION | BESLUT
direktiv_part: ar_ref_id SLASH lopnummer_ref_id SLASH samarbete_ref_id
forordning_part: LPAR samarbete_ref_id RPAR (_W NR)? _W lopnummer_ref_id SLASH ar_ref_id
ar_ref_id: NUMBER
lopnummer_ref_id: NUMBER
samarbete_ref_id: SAMARBETE
av_datum: AV _W DEN _W datum_ref_id
datum_ref_id: DATUM
"""

# The English-surface EU rules: same nonterminal names as EU_RULES, so
# fmt_eu_ref and celex_uri need no dispatch -- only the word order and the
# sub-article convention differ ("Article 29 (5) of Directive 71/305/EEC";
# the parenthesised punkt is the pre-Interinstitutional-style-guide form the
# old judgments use, alongside the modern "Article 5(2)"). The Swedish
# treaty/named-act extensions are not loaded for English: a bare "Article 177
# of the EEC Treaty" must simply refuse to anaphora-link (the of-guard in
# fmt_eu_ref), which is correct-but-unlinked rather than mis-pinned.
EU_RULES_ENG = r"""
eu_ref: artikel_part _W OF _W rattsakt_part
      | skal_part _W OF _W rattsakt_part
      | skal_part _W_AND_OR_EN artikel_part _W OF _W rattsakt_part
      | rattsakt_part
      | artikel_part

skal_part: SKAL _W skal_item (_asep skal_item)*
skal_item: skal_ref_id
skal_ref_id: NUMBER

artikel_part: (ARTIKEL | ARTIKLARNA) _W artikel_item (_asep artikel_item)*
artikel_item: artikel_ref_id (DOT underartikel_ref_id
                              | _W? LPAR underartikel_ref_id RPAR)?
_asep: _W_AND_OR_EN | HYP | COMMA _W
artikel_ref_id: NUMBER
underartikel_ref_id: NUMBER

rattsakt_part: institution _W akttyp _W (direktiv_part | forordning_part) (_W av_datum)?
             | akttyp _W (direktiv_part | forordning_part) (_W av_datum)?
             | direktiv_part
             | forordning_part
             | eu_generic
institution: RADETS | EP_RADETS | KOMMISSIONENS
akttyp: DIREKTIV | FORORDNING | REKOMMENDATION | BESLUT
direktiv_part: ar_ref_id SLASH lopnummer_ref_id SLASH samarbete_ref_id
forordning_part: LPAR samarbete_ref_id RPAR (_W NO_EN)? _W lopnummer_ref_id SLASH ar_ref_id
ar_ref_id: NUMBER
lopnummer_ref_id: NUMBER
samarbete_ref_id: SAMARBETE
av_datum: OF _W datum_ref_id
datum_ref_id: DATUM_EN
eu_generic: EU_DET _W EU_GENERIC
EU_DET: "the"
EU_GENERIC: "directive" | "regulation"
"""

TERMINALS = r"""
// --- terminals ---

_W: " "
DOT: "."
COMMA: ","
LPAR: "("
RPAR: ")"
SLASH: "/"
DSM: "§§"
SM: "§"
HYP: /[-–—]/
IN: "i"
AND.2: "och"
_W_AND_OR_W: / (?:och|eller|samt) /
_W_OR_W: / eller /
NUMBER: /\d+/
PIECE_DIGIT: /[1-9](?!\d)/
SECTION_CHAR: /[a-n](?![\wåäöA-ZÅÄÖ])/
CHAPTER_CHAR: /[a-zåäö](?![\wåäöA-ZÅÄÖ])/
ORDINAL_WORD.3: /första|andra|tredje|fjärde|femte|sjätte|sjunde|åttonde|nionde/
PIECE_WORD.3: /styckena|stycket|st\.|st(?= )/
SENTENCE_WORD.3: /meningarna|meningen/
KAP.3: /[Kk]ap\.?/
PUNKTEN.3: /punkten/
ANVISNINGARNA.4: /anvisningarna punkt /
MOM.3: /mom\./
ITEM_CHAR: /[a-hj-z](?![\wåäöA-ZÅÄÖ])/
LAW_REF_ID: /\d{4}:(?:bih\. ?)?\d+(?:\.\d)?(?: ?s\. ?\d+)?/
CHANGE_WORD.4: /Lag|Förordning|lag|förordning/
LAW_SYNONYM.4: /lagens?|balkens?|förordningens?|formens?|ordningens?|kungörelsens?|stadgans?|lag|förordning/
NAMED_LAW.5: /[\wåäö]+- (?:och|eller) [\wåäö]+-?(?:lagens?|förordningens?)(?![\wåäö])|[\wåäö-]*[\wåäö](?:lagens?|balkens?|förordningens?|formens?|(?<!för)ordningens?|kungörelsens?|stadgans?)(?![\wåäö])/
SAME_LAW.5: /samma lag|nämnda lag|samma förordning|nämnda förordning/
NR: /nr/
DATUM: /\d{1,2} (?:januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december) \d{4}/
COLON: ":"
"""

# The EU-legislation terminals are the one language-dependent piece of the
# grammar: CELLAR holds no Swedish text for pre-accession case law, so those
# documents are parsed from their English manifestation, where the same act
# citation reads "Article 29 (5) of Directive 71/305/EEC". Both blocks define
# the *same* terminal names, so every rule and formatter works unchanged; the
# parser picks a block by the document's language.
EU_TERMINALS = {
    "swe": r"""
ARTIKEL.3: /[Aa]rtikel/
ARTIKLARNA.3: /[Aa]rtiklarna/
// "skäl 108", "skälen 108 och 109", "skälet 108". Not "skäl" as the everyday
// noun: the terminal only ever reaches the parser followed by a number, since
// `skal_part` requires one and the trigger below fires on the pair.
SKAL.3: /[Ss]käl(?:et|en)?/
RADETS: /rådets/
EP_RADETS: /Europaparlamentets och rådets/
KOMMISSIONENS: /kommissionens/
DIREKTIV: /direktiv/
FORORDNING: /förordning/
REKOMMENDATION: /rekommendation/
BESLUT: /beslut/
SAMARBETE: /EEG|EG|EU/
AV: /av/
DEN: /den/
// the lettered point of a sub-article ("artikel 6.1 c"). "i" is excluded, and
// that exclusion is load-bearing: it is also the preposition that introduces
// the act ("artikel 6.1 i dataskyddsförordningen"), and the named-act rule
// admits the instrument with no preposition at all, so a letter terminal that
// accepted "i" would parse that reference as point (i) of article 6.1 and pin
// the citation one level too deep. Point (i) in a list that long is rare; a
// silently mis-pinned everyday citation is not.
PUNKT_LETTER: /(?!i\b)[a-z]\b/
""",
    "eng": r"""
ARTIKEL.3: /[Aa]rticle/
ARTIKLARNA.3: /[Aa]rticles/
SKAL.3: /[Rr]ecitals?/
RADETS: /Council/
EP_RADETS: /European Parliament and (?:of the )?Council/
KOMMISSIONENS: /Commission/
DIREKTIV: /[Dd]irective/
FORORDNING: /[Rr]egulation/
REKOMMENDATION: /[Rr]ecommendation/
BESLUT: /[Dd]ecision/
SAMARBETE: /EEC|EC|EU|Euratom/
NO_EN: /No\.?/
OF: "of"
_W_AND_OR_EN: / (?:and|or) /
DATUM_EN: /\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4}/
""",
}

# KORTLAGRUM (abbreviated lagrum: "3 § MBL", "TF 2:3", "10 kap. 1 § ÄB") --
# the old kortlagrum.ebnf. Reuses generic_ref / piece_ref / NUMBER from
# LAGRUM (hence DEPENDS); the LAW_ABBREV terminal is a data-driven
# alternation of the dcterms:alternative labels, sorted longest-first so
# "MBL" is not read as "MB"+"L". Both productions require structure (a
# generic ref or the n:n short form), so a bare abbreviation in running
# prose ("enligt TF så gäller …") never links -- by construction.
KORTLAGRUM_RULES = r"""
kortlagrum_normal.9: generic_ref _W LAW_ABBREV
                   | LAW_ABBREV _W generic_ref
kortlagrum_short.9: LAW_ABBREV _W NUMBER COLON NUMBER (_W piece_ref)?
"""

# RATTSFALL (Swedish case law: "NJA 1994 s. 12", "RÅ 2009 ref. 5",
# "AD 1993 nr 28", "RH 2007:108", "MIG 2011 not 4") -- the old
# rattsfall.ebnf. Self-contained refs (no structural context, no law
# state), minted straight to dom/{court}/… URIs. NJA is split out because
# its referat form uses a page number ("s. 14" -> .../nja/1994s14) while
# every other court uses a running number; the "not" (notisfall) forms of
# both go to .../{court}/{year}/not/{n}. NJA has no "year:nr" form, so a
# bare "NJA 2003:16" stays unlinked (it matches no production).
RATTSFALL_RULES = r"""
nja_referat: NJA _W year_ref_id S_SEP sidnr_ref_id
nja_notis:   NJA _W year_ref_id NOT_SEP notnr_ref_id
court_referat: court_ref_id _W year_ref_id RF_SEP rf_lopnr_ref_id
court_notis:   court_ref_id _W year_ref_id NOT_SEP notnr_ref_id
court_ref_id: COURT
year_ref_id: NUMBER
sidnr_ref_id: NUMBER
rf_lopnr_ref_id: NUMBER
notnr_ref_id: NUMBER

NJA: "NJA"
COURT: /PMÖD|MÖD|MMD|MIG|HFD|RÅ|AD|RH|RK|MD/
S_SEP: / s\.? ?/
NOT_SEP: / not\.? ?/
RF_SEP: /[ -](?:ref|nr)\.? ?| ?[-:] ?/
"""

# FORARBETEN (preparatory works: propositioner, betänkanden,
# riksdagsskrivelser, SOU, Ds, plus CELEX numbers) -- the old
# forarbeten.ebnf. A document ref (prop./bet./rskr./SOU/Ds/celex) may be
# followed by a page list ("s. 51 och 62"); each page becomes its own
# .../doc#sid{n} link sharing the document. "a. prop." ("anförd
# proposition") resolves to the last proposition seen (document state).
# "avsnitt N" links into the current document (from context), or into the
# committee report ("i kommitténs betänkande") when that marker follows.
# prop. has four historical number forms (std "1999/2000:100", sub-riksmöte
# "1958:B 6", old "nr 212/1949" and "1952 nr 187"); the 'A' sub-riksmöte is
# dropped ("1958:A 30" -> 1958:30) as the old formatter did.
FORARBETEN_RULES = r"""
forarb_refs.5: forarb_doc sidor
anon_prop_refs.5: A_PROP sidor
avsnitt_external.6: avsnitt_list _W I_KOMM
avsnitt_list.3: AVSNITT _W avsnitt_ref_id ((COMMA _W | _W_AND_OR_W) avsnitt_ref_id)*

forarb_doc: prop_ref | bet_ref | skrivelse_ref | sou_ref | ds_ref | celex_ref
prop_ref: PROP_PREFIX _W? prop_body
?prop_body: prop_std | prop_x | prop_y | prop_z
prop_std: riksmote_ref_id COLON _W? lopnr_ref_id
prop_x: riksmote_ref_id COLON subriksmote_ref_id _W? lopnr_ref_id
prop_y: NR _W lopnr_ref_id SLASH riksmote_ref_id
prop_z: riksmote_ref_id _W NR _W lopnr_ref_id
bet_ref: BET_PREFIX _W riksmote_ref_id COLON bet_no_ref_id
skrivelse_ref: SKR_PREFIX _W riksmote_ref_id COLON lopnr_ref_id
sou_ref: SOU_PREFIX _W riksmote_ref_id COLON lopnr_ref_id
ds_ref: DS_PREFIX _W riksmote_ref_id COLON lopnr_ref_id
celex_ref: CELEX

riksmote_ref_id: NUMBER (SLASH NUMBER)?
lopnr_ref_id: NUMBER
subriksmote_ref_id: SUBRIKSMOTE
bet_no_ref_id: BETNO
avsnitt_ref_id: AVSNITTNR

sidor: sida (HYP sida_num)? ((COMMA _W | _W_AND_OR_W) sida_num (HYP sida_num)?)*
sida: COMMA? _W SID _W sida_num
sida_num: NUMBER

PROP_PREFIX: /[Pp]rop\./
BET_PREFIX: "bet."
SKR_PREFIX: "rskr."
SOU_PREFIX: "SOU"
DS_PREFIX: "Ds"
A_PROP: /a\. prop\./
AVSNITT: "avsnitt"
I_KOMM: /i kommitténs betänkande/
SID: /s\.?/
SUBRIKSMOTE: /[ABU]/
BETNO: /[A-Za-zÅÄÖåäö]{2,3}\d+/
CELEX: /3\d\d(?:\d\d)?L\d{4}/
AVSNITTNR: /\d+(?:\.\d+){1,3}/
"""

# EURATTSFALL (CJEU case law: "Case C-176/09", "mål T-201/04") -- the old
# euratt.ebnf. Minted to a celex number "6{year}C{descriptor}{serial}"
# (descriptor C->J Court, T->A General Court, F->W Civil Service Tribunal).
# Accepts the English "Case" and Swedish "mål" prefix (optional) and the
# hyphen variants real EU texts use (incl. U+2011 non-breaking hyphen).
EURATTSFALL_RULES = r"""
ecj_ref: (CASE _W)? ecj_decision ECJHYP ecj_serial SLASH ecj_year
       | CASE _W ecj_serial SLASH ecj_year

// the second alternative is the pre-1989 numbering ("Case 31/87", "mål
// 31/87"): no court letter existed before the Court of First Instance, so
// the marker word is required and the court defaults to the ECJ (fmt_ecj_ref)
ecj_decision: DECISION
ecj_serial: NUMBER
ecj_year: NUMBER

CASE: /Case|[Mm]ål/
DECISION: /[CTF]/
ECJHYP: /[-‑‐–—]/
"""

# MYNDIGHETSBESLUT (authority decisions: ARN, JO, JK) -- the old avg.ebnf.
# The reference is a diarienummer, anchored by a marker so a bare number
# pair is not mistaken for one: ARN by "avgörande <ISO date>;" or "ARN:s
# änr"; JO by "JO YYYY/YY s. N, dnr"; JK by "dnr"/"ärende nr". The
# diarienummer string is the URI tail (avg/{arn,jo,jk}/{dnr}). A JK number
# that also reads as a plausible date (NNNN-MM-DD, ordinal a recent year)
# is treated as a date and left unlinked, as the old formatter did. The
# old "unknown" fallback never produced a URI, so it is dropped.
MYNDIGHETSBESLUT_RULES = r"""
arn_refs.5: arn_pre (arn_ref_id arn_conn)* arn_ref_id
?arn_pre: AVGORANDE_W ISODATE SEMI _W | ARN_PREAMBLE
arn_conn: SEMI _W | COMMA _W | _W_AND_OR_W
arn_ref_id: ARN_ID

jo_refs.5: jo_pre JO_DNR (jo_ref_id _W_AND_OR_W)* jo_ref_id
?jo_pre: JO_LABEL NUMBER SLASH NUMBER JO_SID NUMBER | JO_BESLUT DATUM
jo_ref_id: JO_ID

jk_refs.5: jk_marker (jk_ref_id _W_AND_OR_W)* jk_ref_id
?jk_marker: DNR_W | ARENDE_NR
jk_ref_id: JK_ID

AVGORANDE_W: "avgörande "
ARN_PREAMBLE: "ARN:s änr "
SEMI: ";"
ISODATE: /\d{4}-\d{2}-\d{2}/
ARN_ID: /\d{4}-\d{4,}/
JO_LABEL: "JO "
JO_SID: / s\. /
JO_BESLUT: "JO:s beslut den "
JO_DNR: /, dnr /
JO_ID: /\d+-\d{4}/
DNR_W: /[Dd]nr /
ARENDE_NR: "ärende nr "
JK_ID: /\d+-\d{2}-\d{2}/
"""

# VAGLEDNING (EDPB guidance) -- soft law over the allmänna
# dataskyddsförordningen, cited by the number its issuer gave it and by nothing
# else: no CELEX, no diarienummer. Three surfaces, because there are three
# series (see `edpb/series.py`):
#
#   * "Riktlinjer 05/2020", "riktlinje 3/2019" -- and the definite "riktlinjerna
#     05/2020" Swedish prose writes as often. The EDPB pads the löpnummer in
#     some years and not others, and a citation copies whichever it saw, so the
#     number normalises on the way into the URI (`fmt_riktlinje_ref`) and one
#     document has one address however it was written.
#   * "Rekommendation 01/2020" / "Rekommendationer 02/2020" -- the EDPB itself
#     alternates singular and plural, so both are matched.
#   * "WP 248", "WP248", "wp248 rev.01" -- the artikel 29-gruppens own
#     numbering, which is how its endorsed vägledningar are cited to this day.
#
# The WP form mints for *any* number rather than only the seven vägledningar
# the site hosts: the working party numbered its yttranden in the same series
# (WP 187, WP 259), a guideline's prose is full of them, and an unhosted lagen.nu
# uri already renders as plain text rather than a dead link (page.render_runs)
# -- which is the right reading of "we know exactly what this is and do not
# publish it yet". The one number that is *not* a document is 29 itself: "WP29"
# is what everyone calls the group, and `fmt_wp_ref` drops it the way
# `_jk_is_date` drops a diarienummer that is really a date.
VAGLEDNING_RULES = r"""
riktlinje_ref.5: RIKTLINJE_W vl_id
rekommendation_ref.5: REKOMMENDATION_W vl_id
wp_ref.5: WP_LABEL wp_id

vl_id: VL_ID
wp_id: WP_ID

RIKTLINJE_W.4: /[Rr]iktlinje(?:n|r|rna)?\s+(?:nr\.?\s*)?/
REKOMMENDATION_W.4: /[Rr]ekommendation(?:en|er|erna)?\s+(?:nr\.?\s*)?/
WP_LABEL.4: /WP\s?/
VL_ID: /\d{1,2}\/(?:19|20)\d{2}/
WP_ID: /\d{2,3}(?![\d\/])/
"""

# FORESKRIFT (myndighetsföreskrifter: "PMFS 2022:1", "ELSÄK-FS 2008:1").
#
# An agency regulation is cited by its författningssamling designation and
# number, in running text ("Se vidare MSBFS 2020:7") or, most often, inside the
# parenthesis of its full name ("Säkerhetspolisens föreskrifter (PMFS 2022:1) om
# säkerhetsskydd"). Neither form was recognised at all, so a förarbete, a dom or
# a sibling föreskrift naming one produced no reference -- the corpus held
# exactly zero inbound references to any of its 12,936 föreskrifter, which read
# as a fact about Swedish legal writing and was an absent production.
#
# FS_DESIGNATION is built at parser() time from the författningssamling
# registry, like EU_NAMNAKT is from the EU act names: only a registered series
# matches, so an unknown "XYZFS 2020:1" mints nothing rather than a dangling
# uri, and the printed designation maps to its own slug (ÅFS -> aafs, not afs,
# which is Arbetsmiljöverkets samling).
FORESKRIFT_RULES = r"""
foreskrift_ref.6: FS_DESIGNATION _W? FS_NUMBER
FS_NUMBER: /\d{4}:\d+/
"""

# fires at a registered designation followed by a number. Built from the same
# registry as the grammar terminal, longest-first so a designation is not
# shadowed by a shorter one it starts with. Spelling the shape by hand instead
# ("[A-ZÅÄÖ]…FS") missed the two series that are not FS-shaped at all (LBS,
# RA-MS) while the terminal accepted them, so the production existed and simply
# never fired -- silently, which is the failure this whole type was added to
# end.
FORESKRIFT_TRIGGER_SRC = r"""
    \b(?:%s)\ ?\d{4}:\d+
""" % "|".join(re.escape(d)
               for d in sorted(FS_DESIGNATIONS, key=len, reverse=True))

# grammar rule fragments per parse type (LAW_ABBREV is appended at build
# time from the supplied abbreviations, see parser())
RULES = {LAGRUM: LAGRUM_RULES, EULAGSTIFTNING: EU_RULES,
         KORTLAGRUM: KORTLAGRUM_RULES, RATTSFALL: RATTSFALL_RULES,
         FORARBETEN: FORARBETEN_RULES, EURATTSFALL: EURATTSFALL_RULES,
         MYNDIGHETSBESLUT: MYNDIGHETSBESLUT_RULES,
         VAGLEDNING: VAGLEDNING_RULES,
         FORESKRIFT: FORESKRIFT_RULES}

# words ending in a law suffix that are not law names (ported verbatim)
NOLAW = {
    'aktieslagen', 'anordningen', 'anslagen', 'arbetsordningen',
    'associationsformen', 'avfallsslagen', 'avslagen',
    'avvittringsutslagen', 'bergslagen', 'beskattningsunderlagen',
    'bolagen', 'bolagsordningen', 'dagordningen', 'djurslagen',
    'dotterbolagen', 'emballagen', 'energislagen', 'ersättningsformen',
    'ersättningsslagen', 'examensordningen', 'finansbolagen',
    'finansieringsformen', 'fissionsvederlagen', 'flygbolagen',
    'fondbolagen', 'förbundsordningen', 'föreslagen',
    'företrädesordningen', 'förhandlingsordningen', 'förlagen',
    'förmånsrättsordningen', 'förmögenhetsordningen', 'förordningen',
    'förslagen', 'försäkringsaktiebolagen', 'försäkringsbolagen',
    'gravanordningen', 'grundlagen', 'handelsplattformen',
    'handläggningsordningen', 'inkomstslagen', 'inköpssamordningen',
    'kapitalunderlagen', 'klockslagen', 'kopplingsanordningen',
    'låneformen', 'mervärdesskatteordningen', 'nummerordningen',
    'omslagen', 'ordalagen', 'pensionsordningen',
    'renhållningsordningen', 'representationsreformen',
    'rättegångordningen', 'rättegångsordningen', 'rättsordningen',
    'samordningen', 'skatteordningen', 'skatteslagen',
    'skatteunderlagen', 'skolformen', 'skyddsanordningen', 'slagen',
    'solvärmeanordningen', 'storslagen', 'studieformen', 'stödformen',
    'stödordningen', 'säkerhetsanordningen', 'talarordningen',
    'tillslagen', 'tivolianordningen', 'trafikslagen',
    'transportanordningen', 'transportslagen', 'trädslagen',
    'turordningen', 'underlagen', 'uniformen', 'uppställningsformen',
    'utvecklingsbolagen', 'varuslagen', 'verksamhetsformen',
    'vevanordningen', 'vårdformen', 'ägoanordningen', 'ägoslagen',
    'ärendeslagen', 'åtgärdsförslagen',
}

LAW_SYNONYMS = {'lag', 'balk', 'förordning', 'form', 'ordning',
                'kungörelse', 'stadga',
                'lagen', 'balken', 'förordningen', 'formen', 'ordningen',
                'kungörelsen', 'stadgan'}

ORDINALS = {'första': '1', 'andra': '2', 'tredje': '3', 'fjärde': '4',
            'femte': '5', 'sjätte': '6', 'sjunde': '7', 'åttonde': '8',
            'nionde': '9'}


# Trigger patterns propose candidate start positions for the parser
# (mirroring what the old char-by-char PEG root could match), one source
# fragment per parse type. build_trigger() ORs together the fragments of
# the enabled types into a single re.X regex. Fragments are written with
# no leading "|" so they compose in any order.
LAGRUM_TRIGGER_SRC = r"""
    \b\d+(?:\ ?[a-n]\b)?
        (?:(?:\ ?,\ ?|\ och\ |\ eller\ |\ samt\ |\ ?[-–—]{1,2}\ ?)
           \d+(?:\ ?[a-n]\b)?){0,50}\ §       # section (lists/intervals)
  | \b\d+\ (?:[a-zåäö]\ )?[Kk]ap\b            # chapter
  | \b(?:\d+|första|andra|tredje|fjärde|femte|sjätte|sjunde|åttonde|nionde)
        (?:\ (?:första|andra|tredje|fjärde|femte|sjätte|sjunde|åttonde|nionde))?
        \ (?:stycket|styckena|meningen|meningarna)\b   # relative piece
                                              # (incl. "26 första stycket",
                                              # "första tredje styckena",
                                              # "1-3 styckena")
  | \(\d{4}:                                  # (1998:204)
  | \b[\wåäö-]*[\wåäö]
        (?:lagens?|balkens?|förordningens?|formens?|(?<!för)ordningens?
          |kungörelsens?|stadgans?)\b         # named law
  | \b(?:Lag|Förordning|lag|förordning)\ \(\d{4}:   # change note
"""

EU_TRIGGER_SRC = r"""
    \b[Aa]rtik(?:el|larna)\ \d                # EU article/articles (also initial)
  | \b[Ss]käl(?:et|en)?\ \d                   # recital ("skäl 108")
  | \b(?:rådets|kommissionens|Europaparlamentets\ och\ rådets)\b
  | \b\d+/\d+/E(?:EG|G|U)\b                   # 95/46/EG
  | \bdirektiv\ (?=\(E(?:EG|G|U)\))           # bare "direktiv (EU) 2022/2555"
  | \(E(?:EG|G|U)\)\ (?:nr\ )?\d+/\d+         # (EEG) nr 2092/91
  | \b(?:EUF-fördraget|FEUF|EU-fördraget|EU-stadgan|EKMR)\b   # treaty named first
  | \bfördraget\ om\ Europeiska\ union        # ("<treaty>, särskilt artikel N")
  | \b(?:rättighetsstadgan|europakonventionen)\b
"""

# abbreviation-*first* KORTLAGRUM forms ("TF 2:3", "TF 3 §", "ÄB 10 kap.
# 1 §"); the abbreviation-last form ("3 § MBL") already fires the LAGRUM
# section/chapter triggers (KORTLAGRUM always pulls LAGRUM in via DEPENDS).
# The lookahead keeps the trigger span to just the candidate word -- the
# LAW_ABBREV terminal then rejects any word that is not a known
# abbreviation, so prose is not mis-scanned (loose trigger, strict term).
KORTLAGRUM_TRIGGER_SRC = r"""
    \b[A-ZÅÄÖ][A-Za-zÅÄÖåäö]{0,7}
        (?=\ \d+(?:\ ?[a-n]\b)?\ §|\ \d+\ (?:[a-zåäö]\ )?[Kk]ap\b|\ \d+:\d+)
"""

# fires at a court code / NJA immediately followed by a year
RATTSFALL_TRIGGER_SRC = r"""
    \b(?:NJA|PMÖD|MÖD|MMD|MIG|HFD|RÅ|AD|RH|RK|MD)\ \d{3,4}\b
"""

FORARBETEN_TRIGGER_SRC = r"""
    \b[Pp]rop\.                                # propositions
  | \bbet\.                                    # utskottsbetänkanden
  | \brskr\.                                   # riksdagsskrivelser
  | \bSOU\                                     # statens offentliga utredningar
  | \bDs\                                      # departementsserien
  | \ba\.\ prop\.                              # "a. prop." (anförd proposition)
  | \bavsnitt\ \d                              # section ref
  | \b3\d\d(?:\d\d)?L\d{4}\b                   # bare CELEX (392L0100)
"""

# fires at "(Case|mål)? [CTF]<hyphen><serial>/<year>"
EURATTSFALL_TRIGGER_SRC = r"""
    \b(?:Case|[Mm]ål)\ [CTF][-‑‐–—]\d
  | \b[CTF][-‑‐–—]\d+/\d
  | \b(?:Case|[Mm]ål)\ \d+/\d                  # pre-1989 numbering ("Case 31/87")
"""

# the English-surface EU trigger, mirroring EU_TRIGGER_SRC for the terminals
# EU_TERMINALS["eng"] defines (no treaty/named-act forms: not loaded for eng)
EU_TRIGGER_SRC_ENG = r"""
    \b[Aa]rticles?\ \d                          # Article 29 (5) / Articles 20 and 26
  | \b[Rr]ecitals?\ \d                          # recital ("recital 108")
  | \b(?:Council|Commission|European\ Parliament)\b
  | \b\d+/\d+/E(?:EC|C|U|uratom)\b              # 71/305/EEC
  | \b[DdRr](?:irective|egulation)\ (?=\(E(?:EC|C|U)\))
  | \(E(?:EC|C|U)\)\ (?:No\.?\ )?\d+/\d+        # (EEC) No 2092/91
"""

# fires at each authority-decision marker (ARN/JO/JK)
MYNDIGHETSBESLUT_TRIGGER_SRC = r"""
    \bavgörande\ \d{4}-\d{2}-\d{2}              # ARN "avgörande <date>;"
  | \bARN:s\ änr\ \d                            # ARN "ARN:s änr"
  | \bJO\ \d                                    # JO "JO YYYY/YY"
  | \bJO:s\ beslut\ den\ \d                     # JO "JO:s beslut den …"
  | \b(?:[Dd]nr|ärende\ nr)\ \d+-\d+-\d+        # JK diarienummer (3 parts)
"""

# fires at each EDPB/WP29 guidance marker
VAGLEDNING_TRIGGER_SRC = r"""
    \b[Rr]iktlinje(?:n|r|rna)?\ (?:nr\.?\ ?)?\d{1,2}/(?:19|20)\d{2}
  | \b[Rr]ekommendation(?:en|er|erna)?\ (?:nr\.?\ ?)?\d{1,2}/(?:19|20)\d{2}
  | \bWP\ ?\d{2,3}
"""

TRIGGER_SRC = {LAGRUM: LAGRUM_TRIGGER_SRC, EULAGSTIFTNING: EU_TRIGGER_SRC,
               FORESKRIFT: FORESKRIFT_TRIGGER_SRC,
               KORTLAGRUM: KORTLAGRUM_TRIGGER_SRC,
               RATTSFALL: RATTSFALL_TRIGGER_SRC,
               FORARBETEN: FORARBETEN_TRIGGER_SRC,
               EURATTSFALL: EURATTSFALL_TRIGGER_SRC,
               MYNDIGHETSBESLUT: MYNDIGHETSBESLUT_TRIGGER_SRC,
               VAGLEDNING: VAGLEDNING_TRIGGER_SRC}


def _expand_types(types):
    """Add each requested type's dependencies (one level is enough)."""
    out = set(types)
    for t in types:
        out.update(DEPENDS.get(t, ()))
    return frozenset(out)


def build_trigger(types, lang="swe"):
    src = dict(TRIGGER_SRC)
    if lang == "eng":
        src[EULAGSTIFTNING] = EU_TRIGGER_SRC_ENG
    parts = [src[t].strip() for t in TYPE_ORDER if t in types and t in src]
    return re.compile("\n  | ".join(parts), re.X)

# The old SwedishCitationParser.FILTER_LAW pre-filter, ported verbatim.
# It gated which text nodes got parsed at all in the *pipeline* (the
# legalref engine itself, and its test suite, ran unfiltered) -- applied
# by the projection layer, not by LagrumParser. Behaviorally significant,
# not just an optimization: \bstycket\b does not match "styckena" and
# \bLag \( does not match lowercase "lag (", so e.g. "har upphävts genom
# lag (1988:1556)" stayed completely unlinked in parsed documents.
FILTER_LAW = re.compile(
    r'(§§?|\bkap\b|\bstycket\b|[Ll]agens?\b|\bLag \(\b|[Ff]örordningens?\b'
    r'|\bFörordning \(|balkens?\b|\(EG\)|\(EEG\)|\(EU\))')

# how far a single reference expression can reasonably stretch
WINDOW = 220

FRAGMENT = re.compile(
    r'^(?:K([0-9a-z]+))?(?:P([0-9a-z]+))?(?:S(\d+))?(?:N(\d+))?')

ATTRIBUTE_ORDER = ['law', 'chapter', 'section', 'element', 'piece',
                   'item', 'itemnumeric', 'sentence']

FRAGMENT_LETTERS = [('chapter', 'K'), ('section', 'P'), ('element', 'O'),
                    ('piece', 'S'), ('item', 'N'), ('itemnumeric', 'N'),
                    ('sentence', 'M')]

EU_KEYS = ('ar', 'artikel', 'akttyp')

# a "bare" EU article ref -- one or more article numbers with no instrument named
# (no treaty/act/generic noun). It self-refers inside an EU act, else anaphora-
# links the last named act (fmt_eu_ref).
BARE_PARTS = frozenset(('eu_ref', 'artikel_part', 'artikel_item',
                        'artikel_ref_id', 'underartikel_ref_id', 'punkt_ref_id',
                        'stycke_ref', 'stycke_ref_id'))


class Pinpoint(NamedTuple):
    """What one EU article reference pinpoints inside its act: the article, its
    numbered sub-article, the stycke (sub-paragraph) and the lettered point.
    Passed whole rather than as four positionals, so adding a level does not
    thread through every uri builder."""
    artikel: str | None = None
    underartikel: str | None = None
    stycke: str | None = None
    punkt: str | None = None


# the act itself, pinpointing nothing -- the default every uri builder falls back
# to. A module singleton because ruff's B008 forbids the constructor call in an
# argument default (harmless for an immutable NamedTuple, but the rule is on)
NO_PINPOINT = Pinpoint()


def eu_fragment(pin):
    """An EU act's uri fragment for `pin` -- the dotted grammar the eurlex parser
    mints for the anchor it targets: `6.1`, `6.1.c`, `9.2.S2`, `56.7.S2.a`. ''
    when no article is named.

    A stycke is used only where no lettered point is named: the act names a point
    by its paragraph whichever stycke holds it ("artikel 11 a" for a point in the
    second stycke), so the point is both the finer pinpoint and the only form
    written -- and it is the anchor the eurlex parser mints."""
    if not pin.artikel:
        return ''
    frag = '.'.join(p for p in (pin.artikel, pin.underartikel) if p)
    if pin.punkt:
        return frag + '.' + pin.punkt
    return frag + ('.S' + pin.stycke if pin.stycke else '')


@dataclass
class Ref:
    start: int
    end: int
    text: str
    predicate: str
    uri: str
    kind: str | None = None    # link flavour for the renderer (e.g. "term")


def yield_overlaps(uses, cites):
    """Drop each `uses` Ref whose span overlaps any `cites` Ref: a term-use
    link yields to a citation, the stronger cross-document link. Returns the
    surviving uses (cites are returned unchanged by the caller). interleave
    requires disjoint spans, so merging a term-use list with a citation list
    must resolve overlaps here first, not silently inside interleave."""
    return [u for u in uses
            if not any(u.start < c.end and c.start < u.end for c in cites)]


def _styled(text, start, end, styles):
    """`text[start:end]` as runs, split where the emphasis changes: a plain
    `str` where the document set nothing, a ``{"text", "style"}`` dict where it
    did. Unstyled text stays a bare string, which is the overwhelming majority
    of the corpus and keeps the artifact's shape unchanged for it."""
    if not styles:
        return [text[start:end]] if start < end else []
    cuts = sorted({start, end} | {p for a, b, _ in styles for p in (a, b)
                                  if start < p < end})
    out = []
    for lo, hi in zip(cuts, cuts[1:], strict=False):
        style = "".join(sorted({c for a, b, s in styles
                                if a <= lo and hi <= b for c in s}))
        out.append({"text": text[lo:hi], "style": style} if style
                   else text[lo:hi])
    return out


def interleave(text, refs, styles=()):
    """Splice `refs` (Ref objects with disjoint [start, end) spans) into
    `text`, returning the inline-run list the artifact stores: plain `str`
    runs interleaved with {"predicate", "uri", "text"} link dicts, in
    document order. Text with no refs is a single-element list `[text]`;
    empty text is `[]`.

    `styles` are (start, end, flags) spans of emphasis the document set --
    "i", "b", "bi" -- in the same coordinates. Plain text is split where the
    emphasis changes; a link takes a style only where one covers the whole of
    it, since a half-italic citation would otherwise have to become two
    separate links, which is worse than losing the emphasis on it.

    Spans must be disjoint -- parse_text consumes matched spans, and the
    one caller merging two ref lists (eurlex cites + term uses) filters
    overlaps first -- so an overlap is an upstream bug, not a case to
    resolve silently by dropping a link (rule:fail-fast)."""
    out, pos = [], 0
    for ref in sorted(refs, key=lambda r: r.start):
        assert ref.start >= pos, (
            "overlapping ref spans: %r [%d:%d] overlaps text already "
            "consumed up to %d" % (ref.text, ref.start, ref.end, pos))
        out += _styled(text, pos, ref.start, styles)
        run = {"predicate": ref.predicate, "uri": ref.uri, "text": ref.text}
        if ref.kind:
            run["kind"] = ref.kind
        covering = "".join(sorted({c for a, b, s in styles
                                   if a <= ref.start and ref.end <= b
                                   for c in s}))
        if covering:
            run["style"] = covering
        out.append(run)
        pos = ref.end
    return out + _styled(text, pos, len(text), styles)


@dataclass
class _DocState:
    """Reference-parser state with document lifetime."""
    lastlaw: str | None = None
    # law names learned in-document: normalized lawname -> SFS id
    namedlaws: dict[str, str] = field(default_factory=dict)
    # abbreviations the document defined for itself ("lagen (1994:1564) om
    # alkoholskatt, förkortad LAS"): abbrev -> SFS id, shadowing the global
    # table for the rest of the document (see _learn_abbrev). `abbrev_shadows`
    # keeps what the table would have said, but only where they disagree --
    # the entries whose links an unshadowed parse would have minted wrong;
    # `abbrev_uses` counts resolutions through each local binding.
    abbrevs: dict[str, str] = field(default_factory=dict)
    abbrev_shadows: dict[str, str | None] = field(default_factory=dict)
    abbrev_uses: dict[str, int] = field(default_factory=dict)
    last_forarbete: str | None = None  # base URI of last prop ("a. prop.")
    last_eu_act: str | None = None     # CELEX of the last named EU act (anaphora)
    # CELEX of the document being parsed, when it is itself an EU act. Set so a
    # bare "artikel N" in an EU regulation self-refers to it rather than
    # anaphora-pinning onto some external act named earlier (e.g. a recital's
    # "artikel N i förordning (EG) nr 45/2001").
    self_eu_act: str | None = None


class NoLink(Exception):
    """The match is consumed but yields no links (unknown named law,
    or an EU reference too incomplete for a celex number)."""


# The formula a document uses to declare its own abbreviation for an act it
# just named: "lagen (1994:1564) om alkoholskatt, förkortad LAS" (prop.
# 2021/22:61), ", nedan LAS" (Fi2021/00144), "(LAS)" right after the name.
# Matched with .match(text, ref_end) anchored at the end of a resolved law
# reference -- the link span stops at the SFS number, so the pattern first
# crosses the closing paren and the unconsumed name tail ("om alkoholskatt").
# A parenthesis or sentence boundary in the tail aborts the match: a formula
# past those belongs to some other construction, not to this law.
RE_ABBREV_DEF = re.compile(
    r'\)?(?:\s+om\s+[^,.;:()§]{0,60}?)?'
    r'(?:,\s*(?:förkorta[dst]|nedan(?:\s+kallad)?'
    r'|i\s+det\s+följande(?:\s+(?:benämnd|kallad))?|benämnd|kallad)'
    r'\s+(?P<kw>[A-ZÅÄÖ][A-Za-zÅÄÖåäö]{1,9})\b'
    r'|\s*\((?:förkorta[dst]\s+)?(?P<paren>[A-ZÅÄÖ][A-Za-zÅÄÖåäö]{1,9})\))')


RE_BASEFILE_LAW = re.compile(r'\d+:(?:bih\.[_ ]?|N)?\d+(?:[_ ]s\.\d+|[_ ]\d+)?')


def _fragment_context(basefile, fragment):
    """Decompose a minted fragment id into baseuri attributes, like the
    old re_urisegments did with the node's URI. Bilaga fragments yield
    law-only context (the old regex never matched past a B segment). A bare
    numeric suffix (the 1734 års lag balkar: "1736:0123 1" = byggningabalken,
    "1736:0123 2" = handelsbalken) is *kept*, so relative references resolve
    against the full basefile -- e.g. "1736:0123_1#K9P2", not the old pipeline's
    "1736:0123#…" (which collapsed both balkar to the same law and is corrected
    here, a deliberate divergence from the golden's truncation)."""
    m = RE_BASEFILE_LAW.match(basefile.replace(' ', '_'))
    ctx = {'law': (m.group(0) if m else basefile).replace('_', ' ')}
    if fragment:
        m = FRAGMENT.match(fragment)
        assert m
        for key, value in zip(('chapter', 'section', 'piece', 'item'),
                              m.groups(), strict=True):
            if value:
                ctx[key] = value
    return ctx


def _normalize_sfsid(sfsid):
    # the 1734 års lag balkar are often cited with a spurious "s."
    # ("handelsbalken (1736:0123 s. 2)") their registry id ("1736:0123 2")
    # never had -- drop it so the minted URI hits the catalog document (the
    # normalization legacy legalref.py:611 left as a commented-out TODO)
    sfsid = re.sub(r'^(1736:0123) ?s\.? ?', r'\1 ', sfsid)
    return re.sub(r'(\d+:\d+)\.(\d)', r'\1 \2', sfsid).replace('\n', ' ')


def _normalize_lawname(lawname):
    lawname = lawname.lower()
    return lawname[:-1] if lawname.endswith('s') else lawname


RE_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class NamedLaws(dict):
    """Name -> SFS id, as a plain dict of the law *currently* carrying each name,
    plus `at(name, when)` for the law that carried it on a given date.

    A name outlives the act holding it: "socialtjänstlagen" meant 2001:453 until
    2025-07-01 and 2025:400 after it, and a 2010 decision citing "11 kap. 1 §
    socialtjänstlagen" means the former. Resolved against today's law, every one
    of those citations lands on a statute that did not exist when it was written
    -- 5 rättsfall and 100+ myndighetsbeslut on 11 kap. 1 § alone, all predating
    the law they were filed under.

    Kept a dict subclass rather than a new type because the flat mapping is what
    a dozen call sites already pass around and what the grammar builds its
    NAMED_LAW terminal from: the keys are unchanged, so only a caller that has a
    date to offer needs to know this is more than a dict."""

    def __init__(self, current, history):
        super().__init__(current)
        # name -> ((from|None, until|None, sfsid), …) oldest first. Only names
        # that have moved between acts appear; the rest resolve straight off the
        # dict, which is why this stays empty for 154 of the 203 named laws.
        self._history = {name: tuple(spans)
                         for name, spans in dict(history).items()}

    def at(self, name, when=None):
        """The SFS id `name` denoted on `when` (an ISO date, or None for today's
        act). A name with no recorded history, and an undated caller, get the
        current act -- so nothing that never asks a date changes behaviour.

        `None` where the date falls before every recorded window. The name meant
        *some* earlier act then and this table does not know which, so there is
        no link to mint: answering with today's act would be the very thing this
        exists to stop, and answering with the oldest one on record would assert
        a succession nobody established. A missing link is visibly missing; a
        wrong one reads as adjudicated (rule:fail-fast)."""
        spans = self._history.get(name)
        if not (when and spans):
            return self.get(name)
        when = when.isoformat() if hasattr(when, "isoformat") else str(when)
        assert RE_ISO_DAY.fullmatch(when), (
            "`written` must be an ISO day (lib.util.approximate_date turns a "
            "partial date into one); got %r" % (when,))
        for start, until, lawid in spans:
            if (start is None or start <= when) and (until is None or when < until):
                return lawid
        earliest = next((s[0] for s in spans if s[0]), None)
        return None if earliest and when < earliest else self.get(name)


def _named_at(mapping, name, when):
    """`mapping[name]` as of `when`, for a mapping that may or may not carry
    dates. Plain dicts are what tests and hand-built vocabularies pass, and they
    answer for today -- the dating is an enrichment of the dataset, not a new
    requirement on every caller."""
    at = getattr(mapping, "at", None)
    return at(name, when) if at else mapping.get(name)


def load_namedlaws(path):
    """Map each named law ("brottsbalken", "miljöbalken", …) to its SFS id, from
    the hand-editable named-law dataset (law id -> {label?, abbr?, from?, until?}).

    Where several acts have carried one name, `from`/`until` say when each did;
    the returned :class:`NamedLaws` answers for today by default and for any date
    on request. An act with neither is current and unambiguous."""
    return _named_index(json.loads(Path(path).read_text(encoding='utf-8')), "label")


def _named_index(data, key):
    """`NamedLaws` over one naming key of the dataset -- `label` for the spelled
    names, `abbr` for the acronyms, which move between acts with them (SoL named
    2001:453 before 2025:400 and the new act after). A law may carry several of
    either; each is stored as a str or a list."""
    spans: dict[str, list] = {}
    for lawid, entry in data.items():
        val = entry.get(key)
        for name in ([val] if isinstance(val, str) else val or []):
            spans.setdefault(name, []).append(
                (entry.get("from"), entry.get("until"), lawid.replace('_', ' ')))
    current = {}
    for name, ss in spans.items():
        ss.sort(key=lambda s: (s[0] or "", s[1] or "9999-99-99"))
        # Exactly one act may be open-ended: two would mean the dataset says two
        # acts carry the name today, and the flat mapping every undated caller
        # reads would silently take the *earlier* of them (`next` scans ascending
        # by `from`). That is a dataset error, not a case to resolve.
        open_ended = [s[2] for s in ss if s[1] is None]
        assert len(open_ended) <= 1, (
            "%r is recorded as still carried by %s -- only one act can hold a "
            "name today" % (name, ", ".join(open_ended)))
        # failing that (a name no act carries today) the last to have held it
        current[name] = open_ended[0] if open_ended else ss[-1][2]
    return NamedLaws(current, {k: v for k, v in spans.items() if len(v) > 1})


def load_abbreviations(path):
    """Map each law abbreviation (JB, RB, BrB, …) to its SFS id -- the data
    the old KORTLAGRUM LawAbbreviation terminal was built from. A law may have
    several (its `abbr` is then a list); all of them resolve to the same law.

    A `NamedLaws`, like the spelled names: an acronym follows the name it
    abbreviates from one act to the next, so it needs the same dating."""
    return _named_index(json.loads(Path(path).read_text(encoding='utf-8')), "abbr")


def _act_aliases(entry):
    """The lower-cased name/acronym variants an act is cited by -- its `label`(s)
    and `abbr`(s), each stored as a str or a list. Shared by load_namedacts and
    load_treaties."""
    for key in ("label", "abbr"):
        value = entry.get(key)
        for alias in ([value] if isinstance(value, str) else value or []):
            yield alias.lower()


# Swedish drops a noun's definite suffix after a genitive determiner, so a
# statute writes "EU:s dataskyddsförordning" where namedacts.json registers only
# the definite "dataskyddsförordningen" -- and the alias then never matched, so
# the whole reference went unlinked (dataskyddslagen 2018:218 cites the GDPR
# exactly this way). The determiner itself was never missing: EU_DET has carried
# "EU:s" all along. Only the noun heads an EU act's short name actually ends in
# are listed, so this cannot strip a real final syllable off an acronym.
INDEFINITE_HEADS = {"direktivet": "direktiv", "förordningen": "förordning",
                    "beslutet": "beslut", "rättsakten": "rättsakt",
                    "konventionen": "konvention", "fördraget": "fördrag"}


def with_indefinite_aliases(named_acts):
    """`named_acts` plus the indefinite form of every alias that ends in a
    definite EU-act noun ("dataskyddsförordningen" -> "dataskyddsförordning"),
    so the genitive "EU:s <akt>" resolves. An explicitly registered alias always
    wins -- a derived form never overwrites hand-edited data."""
    out = dict(named_acts)
    for alias, celex in named_acts.items():
        for definite, indefinite in INDEFINITE_HEADS.items():
            if alias.endswith(definite):
                out.setdefault(alias[:-len(definite)] + indefinite, celex)
                break
    return out


def load_namedacts(path):
    """Map each EU-act short name or acronym (lower-cased) to its CELEX, from the
    hand-edited EU named-act dataset (CELEX -> {label?, abbr?}, each a str or a
    list). The EULAGSTIFTNING analogue of load_namedlaws/load_abbreviations: it
    lets the engine resolve "artikel N i dataskyddsförordningen" / "GDPR art 6" to
    the act's CELEX, the way named SFS laws resolve to an SFS id. Sector-1 CELEX
    (the treaties + Charter) are skipped -- they are linked by the always-on treaty
    mechanism (load_treaties / TREATIES), so keeping them out of the caller-supplied
    named-act terminal avoids a second grammar path for the same names."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    out = {}
    for celex, entry in data.items():
        if not isinstance(entry, dict) or celex.startswith("1"):
            continue                       # the "_comment" string / sector-1 treaty
        for alias in _act_aliases(entry):
            out[alias] = celex
    return out


def load_treaties(namedacts_path, coe_path):
    """Map each EU/European primary-law instrument name (lower-cased) to the
    ext-relative path of its consolidated text: the EU treaties and the Charter
    from the *sector-1* entries of the named-act dataset (-> ``celex/<CELEX>``),
    the ECHR from the Council-of-Europe names dataset (-> ``coe/<number>``). These
    link everywhere -- not gated on caller-supplied acts -- so lagrum loads them
    itself into TREATIES rather than taking them as a parser argument."""
    out = {}
    for celex, entry in json.loads(Path(namedacts_path).read_text('utf-8')).items():
        if isinstance(entry, dict) and celex.startswith("1"):
            for alias in _act_aliases(entry):
                out[alias] = "celex/" + celex
    for number, entry in json.loads(Path(coe_path).read_text('utf-8')).items():
        if isinstance(entry, dict):
            for alias in _act_aliases(entry):
                out[alias] = "coe/" + number
    return out


# A draft statute has no number yet, and the lagstiftaren writes the gap as a
# zero löpnummer: "järnvägstrafiklagen (2018:000)" in the betänkande that
# proposes it, "0000:000" where the year is open too. No real SFS has a zero
# löpnummer, so these are never citations to a document -- but the grammar minted
# a uri for each, and ~100 000 of them piled onto a handful of targets
# (lagen.nu/0000:000 alone collected 10 292 inbound links). They rendered as
# plain text, the corpus holding no such document, but they sat in the catalog
# and topped `dangling_targets` -- which is the want-list the eurlex backfill
# downloads against (L1).
_PLACEHOLDER_SFS = re.compile(r'\d+:0+$')


def is_placeholder_sfsid(sfsid):
    """Whether an SFS id is the lagstiftaren's not-yet-assigned placeholder
    ("2018:000", "0000:00") rather than a real number."""
    return bool(_PLACEHOLDER_SFS.fullmatch(sfsid.strip()))


def lagrum_uri(attrs, base='https://lagen.nu/'):
    """Format collected attributes as a lagen.nu URI, replicating what
    COIN minting produced (same attribute munging as sfs_format_uri)."""
    attrs = dict(attrs)
    if 'lawref' in attrs:
        attrs = {'law': attrs['law'], 'lawref': attrs['lawref']}
    if ('item' in attrs or 'itemnumeric' in attrs) and 'piece' not in attrs:
        attrs['piece'] = '1'
    for k, v in attrs.items():
        attrs[k] = ORDINALS.get(v, v)
    law = _normalize_sfsid(attrs.pop('law')).replace('\xa0', ' ')
    # page-number laws slug like the corpus basefiles (sfs.register.sfs_slug):
    # "1904:48 s.1" -> 1904:48_s.1 -- NOT the legacy COIN template's
    # 1904:48_s._1, which the catalog never contained
    law = re.sub(r' ?s\.? ?(\d+)$', r'_s.\1', law)
    uri = base + law.replace('bih. ', 'bih.').replace(' ', '_')
    if 'lawref' in attrs:
        return uri + '#L' + attrs['lawref']
    fragment = ''.join(
        letter + attrs[key].replace(' ', '').replace('\xa0', '')
        for key, letter in FRAGMENT_LETTERS if attrs.get(key))
    return uri + ('#' + fragment if fragment else '')


def celex_year(value):
    """A parsed act number interpreted as a CELEX year (a two-digit year is
    1900s -- the oldest EU acts are from the 1950s), or None when it falls
    outside 1950-2050 and so cannot be a year."""
    year = int(value) + (1900 if len(value) <= 2 else 0)
    return year if 1950 <= year <= 2050 else None


def celex_uri(attrs, base='https://lagen.nu/'):
    """Compute a celex URI (``3<year><type><number>``). The year/number order
    in a cited act number differs by act type and flipped for all types in the
    2015 numbering reform, so fmt_eu_ref puts the structurally-likeliest year in
    ``ar``; here we settle it by the one invariant that always holds -- a CELEX
    year is in 1950-2050 -- taking the other value when ``ar`` isn't a year, and
    refusing to mint a (broken) link when neither is."""
    if 'akttyp' not in attrs:
        if 'forordning' in attrs:
            attrs['akttyp'] = 'förordning'
        elif 'direktiv' in attrs:
            attrs['akttyp'] = 'direktiv'
    if 'akttyp' not in attrs or 'ar' not in attrs or 'lopnummer' not in attrs:
        raise NoLink()
    year = celex_year(attrs['ar'])
    if year is not None:
        number = int(attrs['lopnummer'])
    else:
        year, number = celex_year(attrs['lopnummer']), int(attrs['ar'])
    if year is None:
        raise NoLink()
    letter = {'direktiv': 'L', 'förordning': 'R',
              'rekommendation': 'H', 'beslut': 'D',
              'directive': 'L', 'regulation': 'R',
              'recommendation': 'H', 'decision': 'D'}[attrs['akttyp'].lower()]
    uri = base + 'ext/celex/3%04d%s%04d' % (year, letter, number)
    # the sub-article, stycke and lettered point, for an act cited by number as
    # well as by name ("artikel 125.4 a i förordning (EU) nr 1303/2013")
    frag = eu_fragment(Pinpoint(attrs.get('artikel'), attrs.get('underartikel'),
                                attrs.get('stycke'), attrs.get('punkt')))
    return uri + ('#' + frag if frag else '')


# the named-EU-act extension, added only when EULAGSTIFTNING is active AND the
# caller supplies act aliases (like LAW_ABBREV for KORTLAGRUM): a known act name
# becomes a valid `rattsakt_part`, so "artikel N i <name>" and "artikel N <name>"
# resolve to the act's CELEX. A leading determiner/adjective (den, EU:s, allmänna)
# is grammar, so `label` data carries only the noun-phrase head.
#
# It also turns on article *anaphora*: once an EU act is named, a later "artikel
# N i förordningen" (the definite generic noun) or a bare "artikel N" pinpoints
# the same act. A bare article trailed by a *named primary-law instrument* (a
# treaty, the Charter, the ECHR) is instead captured by TREATY_RULES and linked
# onto that instrument's own consolidated text -- never mis-pinned onto the act.
EU_NAMNAKT_RULES = r"""
%extend rattsakt_part: eu_namnakt_full
%extend eu_ref: artikel_part _W eu_namnakt_full
eu_namnakt_full: (EU_DET _W)? (EU_ADJ _W)? eu_namnakt
eu_namnakt: EU_NAMNAKT
EU_ADJ: "allmänna" | "allmän"
"""


# EU/European primary law cited by name -- the EU treaties, the Charter of
# Fundamental Rights, and the ECHR -- mapped to the ext-relative path of their
# consolidated text (a CELEX for EU instruments, the Council-of-Europe treaty id
# for the ECHR). Secondary acts get their CELEX minted by celex_uri, but these are
# a small fixed set with irregular ids, so the name resolves straight to the path.
# An article/sub-article pinpoint rides as a #-fragment on the consolidated text
# (celex/12016E/TXT#16.2, coe/005#A8), the addressing every act uses. No corpus
# pages for the EU treaties/Charter yet, so those render as external EUR-Lex links.
# The name<->path data is hand-edited in the datasets (namedacts.json sector-1 +
# coe/data/names.json), loaded once here since it links in every vertical.
TREATIES = load_treaties(datasets.NAMEDACTS, datasets.COE_NAMES)

# EU-reference rules that link *everywhere* (not gated on caller-supplied acts,
# the way EU_NAMNAKT is), whenever EULAGSTIFTNING is active:
#  * a treaty/Charter/ECHR article ("artikel N i <treaty>", the "i" optional) ->
#    the instrument's consolidated-text path; a list/range links each member;
#  * the definite generic noun ("(det) direktivet", "förordningen") -> the last
#    named EU act, so an EU document's own back-reference to a directive it just
#    cited resolves (the anaphora used to be Swedish-parser-only).
EU_EXTRA_RULES = r"""
%extend eu_ref: artikel_part _W (IN _W)? eu_treaty
%extend eu_ref: eu_treaty COMMA? _W SARSKILT _W artikel_part
%extend eu_ref: rattsakt_part COMMA? _W SARSKILT _W artikel_part
%extend rattsakt_part: eu_generic
eu_treaty: EU_TREATY
eu_generic: (EU_DET _W)? EU_GENERIC
EU_DET: "EU:s" | "den" | "det"
EU_GENERIC: "förordningen" | "direktivet" | "rättsakten"
SARSKILT: "särskilt"
"""


@functools.cache
def parser(requested, expanded, abbrevs=(), eu_acts=(), lang="swe"):
    """Earley parser compiled for a set of parse types. Root alternatives
    come only from the explicitly `requested` types; rule fragments and
    terminals from the dependency-`expanded` set -- so a dependency
    (KORTLAGRUM/ENKLALAGRUM both depend on LAGRUM) lends its productions
    without also contributing its own ?ref roots. `abbrevs` (sorted
    longest-first) supplies the KORTLAGRUM LAW_ABBREV terminal; `eu_acts`
    (likewise) the EULAGSTIFTNING EU_NAMNAKT terminal of known EU-act names.
    `lang` swaps the EU-legislation surface (EU_RULES_ENG + the eng terminal
    block) for an English-language document; the Swedish-only treaty and
    named-act extensions are then left out."""
    rules = dict(RULES)
    if lang == "eng":
        rules[EULAGSTIFTNING] = EU_RULES_ENG
    roots = [r for t in TYPE_ORDER if t in requested for r in ROOTS[t]]
    grammar = "start: ref\n?ref: " + "\n    | ".join(roots) + "\n"
    grammar += "".join(rules.get(t, '') for t in TYPE_ORDER if t in expanded)
    grammar += TERMINALS
    if EULAGSTIFTNING in expanded:
        grammar += EU_TERMINALS[lang]
    if KORTLAGRUM in expanded:
        grammar += "\nLAW_ABBREV: %s\n" % " | ".join('"%s"' % a for a in abbrevs)
    if EULAGSTIFTNING in expanded and lang != "eng":
        grammar += EU_EXTRA_RULES
        grammar += "\nEU_TREATY: %s\n" % " | ".join(
            '"%s"i' % t for t in sorted(TREATIES, key=len, reverse=True))
        if eu_acts:
            grammar += EU_NAMNAKT_RULES
            grammar += "\nEU_NAMNAKT: %s\n" % " | ".join(
                '"%s"i' % a for a in eu_acts)
    if FORESKRIFT in expanded:
        # longest designation first, so "ELSÄK-FS" is not shadowed by a shorter
        # registered series that prefixes it
        grammar += "\nFS_DESIGNATION: %s\n" % " | ".join(
            '"%s"' % d for d in sorted(FS_DESIGNATIONS, key=len, reverse=True))
    return Lark(grammar, parser='earley')


def _tree_tokens(tree):
    return list(tree.scan_values(lambda v: isinstance(v, Token)))


# Token types that qualify a number and so belong to the link they trail
# (or, leading, the link they precede): unit markers and ordinals. Pure
# connectives (HYP, COMMA, AND, ...) and law-name/punctuation tokens are
# never absorbed -- they stay as plain text between links.
ABSORB_MARKERS = frozenset((
    'SM', 'DSM', 'SECTION_CHAR', 'CHAPTER_CHAR', 'ORDINAL_WORD', 'PIECE_WORD',
    'PIECE_DIGIT', 'SENTENCE_WORD', 'KAP', 'MOM', 'PUNKTEN', 'ITEM_CHAR'))


def _node_span(node):
    """(start, end) covering every token of `node`, in the coordinates of
    the window the tree was parsed from."""
    toks = _tree_tokens(node)
    return min(t.start_pos for t in toks), max(t.end_pos for t in toks)


def _law_id_span(law_node):
    """Span of just the law-identifying token (the SFS number, or the named
    law word) -- not the surrounding "lagen ( … )" scaffolding, which the
    old pipeline left outside the link."""
    toks = [t for t in _tree_tokens(law_node)
            if t.type in ('LAW_REF_ID', 'NAMED_LAW')]
    return (toks[0].start_pos, toks[0].end_pos) if toks else _node_span(law_node)


def find_refids(tree):
    """Collect *_ref_id subtree texts into an attribute dict, like the
    old find_attributes (key = production name minus the suffix)."""
    d = {}
    for sub in tree.iter_subtrees_topdown():
        if sub.data.endswith('_ref_id'):
            d[sub.data[:-7]] = ' '.join(
                t.value for t in _tree_tokens(sub)).strip()
    return d


def subtree(tree, name, *default):
    """First subtree (self included) with the given rule name. Raises
    StopIteration when absent, unless a `default` is supplied."""
    return next((s for s in tree.iter_subtrees_topdown() if s.data == name),
                *default)


def _token_text(tree):
    """Concatenate the tree's token values (no separator)."""
    return ''.join(t.value for t in _tree_tokens(tree))


def _riksmote_str(node):
    """Riksmöte id keeping the slash form ("1996/97", "1971")."""
    return '/'.join(t.value for t in _tree_tokens(node) if t.type == 'NUMBER')


def _avg_ids(node, name):
    """(diarienummer, window-span) pairs of the named *_ref_id rule, in
    document order. A citation carrying several dnr ("dnr X och Y") is
    several separate references sharing a prefix, not one -- each links its
    own diarienummer token so the spans stay disjoint (rule:fail-fast)."""
    return [(_token_text(s), _node_span(s)) for s in node.iter_subtrees_topdown()
            if s.data == name]


# "WP29" is what everyone calls artikel 29-gruppen itself, so the number that
# would name a document is the one number that never does
WP29_GROUP = '29'

# "Riktlinjer NN/ÅÅÅÅ" is not the EDPB's form alone -- every European authority
# numbers its guidance that way, and Swedish prose names the issuer in front of
# it: "Socialstyrelsens riktlinjer 2/2018", "EBA:s riktlinjer 4/2017",
# "Europarådets rekommendation 1/2019". Those must not mint EDPB addresses, and
# the "renders as plain text when unhosted" argument does not save them: 2/2018
# *is* in the corpus, so a sentence about Socialstyrelsen would link to the
# EDPB's certification guideline and write a false edge onto its rail.
#
# So a match is rejected when another body claims it: the text immediately
# before it ends in a capitalised genitive ("Socialstyrelsens ", "EBA:s ") that
# is not one of the EDPB's own names. A bare "riktlinjer 05/2020", a lower-case
# "styrelsens riktlinjer", and every EDPB spelling stay linked -- which is what
# IMY and the guidance itself actually write.
EDPB_NAMES = ('Europeiska dataskyddsstyrelsen', 'Dataskyddsstyrelsen', 'EDPB',
              'Styrelsen', 'Artikel 29-gruppen', 'Artikel 29-arbetsgruppen')
# The genitive may fall on the second word of a name ("Europeiska
# bankmyndighetens riktlinjer", "Europeiska kommissionens rekommendation"), so
# the pattern spans an optional lower-case continuation after the capitalised
# head -- which is also the shape of the EDPB's own long name, hence the
# exemption list is tried against the whole run.
RE_OTHER_ISSUER = re.compile(
    r'(?<![\wåäöÅÄÖ])'
    r'(?!(?:%s)(?:s|:s)?\s+$)'                    # ... unless it is the EDPB
    r'[A-ZÅÄÖ][\wåäöÅÄÖ-]*'                       # a capitalised head word
    r'(?:\s+[a-zåäö][\wåäöÅÄÖ-]*)?'               # + an optional lower-case tail
    r'(?:s|:s)\s+$'                               # ... in the genitive, last
    % '|'.join(re.escape(n) for n in EDPB_NAMES))

# the same body under its full name: "artikel 29-gruppen" / "artikel
# 29-arbetsgruppen", which is a *body*, not a reference to artikel 29
ARTICLE29 = '29'
RE_ARTICLE29_GROUP = re.compile(r'[-‑‐–—](?:arbets)?gruppen')

# A document that lists its own chapters ("Innehållet i föreskrifterna är
# uppdelat enligt följande: 1 kap. – Allmänna bestämmelser 2 kap. – …") has its
# table of contents read as citations into whatever act its ingress named last
# -- PMFS 2022:1 1 § sends all eight of its own chapters into
# säkerhetsskyddsförordningen. Resolving it here was tried and abandoned: a
# self-reference phrase ("dessa föreskrifter", "denna lag") that ends the
# anaphoric focus also strips the base from the legitimate anaphora that
# follows, and suppressed ~0.7% of links corpus-wide -- "8 kap. 2 § första
# stycket samma lag", the "Förordning (2007:926)." change notes -- to save the
# handful a table of contents costs. The signal that separates the two is
# structural, not grammatical (consecutive chapters, each trailed by a dash and
# a heading, none carrying a §), so it belongs to whatever recognises a TOC
# block at parse time, not to the citation engine.


def vagledning_slug(number):
    """An EDPB series number as it appears in a URI ("5/2020" -> "05-2020").

    The löpnummer is zero-padded to two digits because the EDPB pads it in some
    years and not others -- "Riktlinjer 05/2020" beside "Riktlinjer 1/2018" --
    and a citation copies whichever form it saw. Kept byte-identical to
    `edpb.series.number_slug`, which mints the same address from the document's
    side; `test_edpb.py` holds the two together."""
    serial, _, year = number.partition('/')
    return '%02d-%s' % (int(serial), year)


def _jk_is_date(dnr):
    """A JK diarienummer NNNN-MM-DD whose first part is a recent year and
    whose other parts read as month/day is probably a date, not a ref."""
    ordinal, second, third = (int(x) for x in dnr.split('-'))
    return (1980 <= ordinal <= date.today().year
            and 1 <= second <= 12 and 1 <= third <= 31)


class _MatchState:
    """Per-root-match formatter state (the old clear_state cleared these
    between matches)."""

    def __init__(self):
        self.currentlaw = None
        self.currentchapter = None
        self.currentsection = None
        self.currentpiece = None


class LagrumParser:
    """Finds references in one document's text nodes. Call parse_text
    once per text node in document order -- state evolves ("samma lag"
    refers back to the last named law, and "lagen (1994:953) om ..."
    teaches the parser law names used later in the document)."""

    def __init__(self, namedlaws, basefile, base='https://lagen.nu/',
                 abbreviations=None, parse_types=None, named_acts=None,
                 lang="swe", written=None):
        self.namedlaws = namedlaws
        # the date the document being parsed was written, so a bare law name
        # resolves to the act that bore it *then*. None = today's law, which is
        # what every caller without a date gets and what the parser always did.
        self.written = written
        self.basefile = basefile
        self.base = base
        self.lang = lang
        # expanded here rather than in `load_namedacts` so the derived forms stay
        # inside the citation engine: `eu_acts` below builds the EU_NAMNAKT
        # terminal from this same mapping, so the grammar and the CELEX lookup
        # cannot disagree, while the ⌘K resolver keeps the hand-edited names only
        self.named_acts = with_indefinite_aliases(named_acts or {})
        # the document's own law URI -- the prefix every self-reference (a
        # relative "5 §" or an ändringshänvisning "#L<act>") is minted under,
        # used to recognise self-links from id-suppressed provisions.
        self.self_law_uri = lagrum_uri(
            {'law': _fragment_context(basefile, None)['law']}, base)
        self.state = _DocState()
        self.abbreviations = abbreviations or {}
        # Default set is SFS + EU (the SFS-pipeline behaviour). Supplying
        # `abbreviations` adds KORTLAGRUM, so existing call sites keep
        # working; callers wanting full control pass `parse_types`.
        if parse_types is None:
            parse_types = [LAGRUM, EULAGSTIFTNING]
            if abbreviations:
                parse_types.append(KORTLAGRUM)
        requested = frozenset(parse_types)
        self.parse_types = _expand_types(parse_types)
        assert KORTLAGRUM not in self.parse_types or self.abbreviations, \
            "KORTLAGRUM parse type requires abbreviations"
        # ENKLALAGRUM relaxes the external-ref combine rule (a lone chapter
        # ref folds into the law link); only in effect when it is the
        # requested SFS grammar, not when full LAGRUM is also requested.
        self.enkla = ENKLALAGRUM in requested and LAGRUM not in requested
        abbrevs = tuple(sorted(self.abbreviations, key=len, reverse=True))
        eu_acts = tuple(sorted(self.named_acts, key=len, reverse=True))
        self.lark = parser(requested, self.parse_types,
                           abbrevs if KORTLAGRUM in self.parse_types else (),
                           eu_acts if EULAGSTIFTNING in self.parse_types else (),
                           lang)
        self.trigger = build_trigger(self.parse_types, lang)

    def reset(self, written=None):
        """Discard per-document state (learned law names, the "samma lag"
        focus, förarbete/EU-act anaphora) before parsing a new document.
        Call this instead of rebuilding the parser -- construction (grammar
        compilation, dataset loading) is the expensive part.

        `written` is the next document's own date, since a reused parser meets
        documents from different years: it is per-document state like the rest,
        so it is set here rather than at construction. Omitted means today's law,
        the same as never setting it."""
        self.state = _DocState()
        self.written = written

    # --- scanning ---

    def parse_text(self, text, fragment=None, context=None,
                   predicate='dcterms:references'):
        """Return a list of Ref for every reference found in `text`.
        `fragment` is the minted fragment id of the nearest identified
        ancestor node (context for relative references); alternatively
        pass an explicit attribute dict as `context`. An empty context
        dict means no base at all: relative references stay unlinked
        until a law is named (the old nobaseuri mode)."""
        if context is None:
            context = _fragment_context(self.basefile, fragment)
        self.nobaseuri = not context
        refs = []
        pos = 0
        while True:
            m = self.trigger.search(text, pos)
            if not m:
                break
            tree, length = self.try_parse(text, m.start())
            if tree is not None and self.acceptable(tree, text, m.start(),
                                                    m.start() + length):
                base = m.start()
                # let a formatter peek at the text trailing its match (the
                # bare-article anaphora guard needs to see a coordination /
                # other-instrument continuation that the node itself excludes)
                self._scan_text, self._scan_base = text, base
                try:
                    attrlist = list(self.format_root(tree, context))
                    for attrs, (s, e) in zip(
                            attrlist, self.link_spans(attrlist, tree, length),
                            strict=True):
                        if '_uri' in attrs:    # self-contained (rättsfall)
                            uri = attrs['_uri']
                        elif any(k in attrs for k in EU_KEYS):
                            uri = celex_uri(attrs, self.base)
                            # remember a formally-named act so a later bare
                            # "artikel N" anaphora can pinpoint it
                            self.state.last_eu_act = uri.split(
                                'ext/celex/')[-1].split('#')[0]
                        elif is_placeholder_sfsid(str(attrs.get('law', ''))):
                            raise NoLink()      # a draft's "(2018:000)" (L1)
                        else:
                            uri = lagrum_uri(attrs, self.base)
                            if 'law' in attrs:
                                self._learn_abbrev(text, base + e, attrs)
                        refs.append(Ref(base + s, base + e,
                                        text[base + s:base + e], predicate, uri))
                except NoLink:
                    pass
                pos = m.start() + length
            else:
                pos = m.start() + 1
        return refs

    def link_spans(self, attrlist, tree, length):
        """Per-link (start, end) spans within the window. Each link starts
        from its own emitted token span; trailing structural markers (a §§
        after a section range, a 'kap.' …) that no link claims are absorbed
        into the nearest preceding link they contiguously follow, the way
        the old pipeline drew the link boundary. Links without an emitted
        span (self-contained rättsfall etc.) cover the whole match."""
        spans = [list(a.get('_span', (0, length))) for a in attrlist]
        if not spans:
            return spans
        tokens = _tree_tokens(tree)
        markers = sorted((t.start_pos, t.end_pos) for t in tokens
                         if t.type in ABSORB_MARKERS)
        for mstart, mend in markers:
            if any(s <= mstart and mend <= e for s, e in spans):
                continue  # already inside a link's own span
            cand = None
            for i, (_s, e) in enumerate(spans):
                if e <= mstart and not self._token_between(tokens, e, mstart) \
                        and (cand is None or e > spans[cand][1]):
                    cand = i
            if cand is not None:
                spans[cand][1] = max(spans[cand][1], mend)
        return [tuple(s) for s in spans]

    @staticmethod
    def _token_between(tokens, a, b):
        """True if any token sits strictly between offsets a and b -- i.e.
        the gap is not pure whitespace, so a marker past it is not a
        contiguous trailer of the link ending at a."""
        return any(a < t.start_pos and t.end_pos <= b for t in tokens)

    def try_parse(self, text, start):
        """Longest reference expression anchored at `start`, or (None, 0)."""
        window = text[start:start + WINDOW]
        for _ in range(8):
            window = window.rstrip(' ,;')
            if not window:
                return None, 0
            try:
                return self.lark.parse(window), len(window)
            except UnexpectedInput as e:
                upto = getattr(e, 'pos_in_stream', None)
                if not upto:
                    return None, 0
                if upto >= len(window):
                    # UnexpectedEOF: incomplete trailing production --
                    # back off a whole word and retry
                    window = re.sub(r'\S+$', '', window)
                else:
                    window = window[:upto]
        return None, 0

    def acceptable(self, tree, text, start, end):
        """Whether a parsed match may link at all, judged on the text around it.

        The old ChangeRef required either a trailing period or a following
        non-space/comma character -- "lag (1998:204) om ..." is not a change
        note (the SFS number alone gets linked on a later trigger), and neither
        is "Lag (1991:242)" at the very end of a text node (the old lookahead
        failed at end-of-buffer). `start`/`end` bound the match, so a rule can
        read what precedes it as well as what follows."""
        node = tree.children[0]
        if isinstance(node, Tree) and node.data == 'change_ref':
            has_dot = any(t.type == 'DOT' for t in node.children
                          if isinstance(t, Token))
            if not has_dot and (end >= len(text) or text[end] in ' ,'):
                return False
        # another authority's numbered guidance is not the EDPB's
        if (isinstance(node, Tree)
                and node.data in ('riktlinje_ref', 'rekommendation_ref')
                and RE_OTHER_ISSUER.search(text[:start])):
            return False
        # "artikel 29-gruppen" is the body the article established, not the
        # article: the working party is named that way in every data-protection
        # document written since 1995, and reading it as a reference sent 13 of
        # one EDPB guideline's links to artikel 29 in the GDPR -- which repealed
        # the directive the group existed under, and has no such body in it.
        if (isinstance(node, Tree) and node.data == 'eu_ref'
                and text[:end].rstrip().endswith(ARTICLE29)
                and RE_ARTICLE29_GROUP.match(text[end:])):
            return False
        return True

    # --- formatting (ports of the old format_* semantics) ---

    def format_root(self, tree, context):
        """Return completed attribute dicts, one per link, in document
        order. Raises NoLink when the whole match must stay unlinked."""
        match = _MatchState()
        out = []
        self.dispatch(tree.children[0], match, out, context)
        if match.currentlaw:
            self.state.lastlaw = match.currentlaw
        return out

    def emit(self, attrs, match, out, context, span=None):
        """Complete attrs from match state and structural context (the
        old find_attributes + sfs_format_uri completion) and append.
        `span` is the (start, end) of this link's own tokens within the
        window, used to inline the link at its exact position."""
        d = dict(attrs)
        if span is not None:
            d['_span'] = span
        if any(k in d for k in EU_KEYS):
            out.append(d)
            return
        for key, value in (('law', match.currentlaw),
                           ('chapter', match.currentchapter),
                           ('section', match.currentsection),
                           ('piece', match.currentpiece)):
            if value and key not in d:
                d[key] = value
        specificity = False
        for key in ATTRIBUTE_ORDER:
            if key in d:
                specificity = True
            elif not specificity and key in context:
                d[key] = context[key]
        if not d.get('law'):
            return  # no mintable URI (relative ref without any base)
        out.append(d)

    def dispatch(self, node, match, out, context):
        if isinstance(node, Token):
            return
        handler = getattr(self, 'fmt_' + node.data, None)
        if handler:
            handler(node, match, out, context)
        else:
            for child in node.children:
                self.dispatch(child, match, out, context)

    def fmt_change_ref(self, node, match, out, context):
        # the change note links its whole span -- "Lag (2001:1016)."
        self.emit({'lawref': _normalize_sfsid(find_refids(node)['law'])},
                  match, out, context, span=_node_span(node))

    def fmt_sfs_nr(self, node, match, out, context):
        law = _normalize_sfsid(find_refids(node)['law'])
        if self.nobaseuri:  # the old format_SFSNr learned the base law
            context['law'] = law
        # link just the SFS number, not any enclosing "( … )"
        self.emit({'law': law}, match, out, context, span=_law_id_span(node))

    def fmt_generic_ref(self, node, match, out, context):
        # A generic_ref that names a chapter makes it sticky, like every other
        # chapter-bearing production here. The old GenericRef production
        # short-circuited to a single link before any state-setting formatter
        # could run, so a following bare section resolved against the *node's*
        # structural context instead -- which made the same construction mean
        # two things depending on its joiner: "3 kap. 2 § och 13 §" already read
        # 13 § as chapter 3's (fmt_individual_chapter_section_refs holds the
        # chapter), while "3 kap. 2 §, 13 §" did not. Swedish drafting
        # continues within the chapter until a new one or a new law is named, so
        # "4 kap. 7 § första stycket samt 8 och 9 §§ säkerhetsskyddslagen" means
        # 4 kap. 8-9 §§, not the chapterless 8 § the act does not have. An
        # explicit law on the later ref still resets (emit prefers its own
        # attrs), so a cross-act "… samt 5 § lagen (1990:52)" is untouched.
        ids = find_refids(node)
        self.emit(ids, match, out, context, span=_node_span(node))
        if ids.get('chapter'):
            match.currentchapter = ids['chapter']

    fmt_section_anatomy = fmt_generic_ref
    fmt_piece_item_ref = fmt_generic_ref

    def fmt_individual_chapter_section_refs(self, node, match, out, context):
        sections = [c for c in node.children
                    if isinstance(c, Tree) and c.data == 'section_ref']
        match.currentchapter = find_refids(node.children[0])['chapter']
        # the chapter prefix ("3 kap.") folds into the first section link
        self.emit({'section': find_refids(sections[0])['section']},
                  match, out, context,
                  span=(_node_span(node.children[0])[0],
                        _node_span(sections[0])[1]))
        for section in sections[1:]:
            self.emit({'section': find_refids(section)['section']},
                      match, out, context, span=_node_span(section))
        # chapter stays sticky (the old formatter never reset it)

    def fmt_chapter_section_refs(self, node, match, out, context):
        chapter_ref, sections = node.children
        match.currentchapter = find_refids(chapter_ref)['chapter']
        self.emit({'chapter': match.currentchapter}, match, out, context,
                  span=_node_span(chapter_ref))
        self.dispatch(sections, match, out, context)
        # the old format_ChapterSectionRefs/format_AlternateChapter-
        # SectionRefs reset the chapter, so a trailing chapterless
        # "11 § första stycket" unit resolves against the *node's*
        # structural context -- semantically dubious but golden truth
        match.currentchapter = None

    def fmt_chapter_section_piece_refs(self, node, match, out, context):
        chapter_ref, section_pieces = node.children
        match.currentchapter = find_refids(chapter_ref)['chapter']
        self.emit({'chapter': match.currentchapter}, match, out, context,
                  span=_node_span(chapter_ref))
        self.dispatch(section_pieces, match, out, context)

    def fmt_single_section_ref(self, node, match, out, context):
        self.emit({'section': find_refids(node)['section']},
                  match, out, context, span=_node_span(node))

    # only reachable as the final "eller 16 §" of alternate_section_refs;
    # every other rule containing section_ref formats it itself
    fmt_section_ref = fmt_single_section_ref

    def fmt_section_piece_refs(self, node, match, out, context):
        section = node.children[0]
        match.currentsection = find_refids(section)['section']
        pieces = [c for c in node.children[1:] if isinstance(c, Tree)]
        for i, piece in enumerate(pieces):
            # the section prefix ("42 §") folds into the first piece link
            span = ((_node_span(section)[0], _node_span(piece)[1]) if i == 0
                    else _node_span(piece))
            self.emit(find_refids(piece), match, out, context, span=span)
        match.currentsection = None

    def fmt_section_piece_item_range(self, node, match, out, context):
        section, piece = node.children[0], node.children[1]
        match.currentsection = find_refids(section)['section']
        match.currentpiece = find_refids(piece)['piece']
        self.emit({'piece': match.currentpiece}, match, out, context,
                  span=(_node_span(section)[0], _node_span(piece)[1]))
        for item in node.children[2:]:
            if isinstance(item, Tree):
                self.emit(find_refids(item), match, out, context,
                          span=_node_span(item))
        match.currentsection = None
        match.currentpiece = None

    def fmt_section_item_refs(self, node, match, out, context):
        section = node.children[0]
        match.currentsection = find_refids(section)['section']
        self.emit({'section': match.currentsection}, match, out, context,
                  span=_node_span(section))
        for item in node.children[1:]:
            if isinstance(item, Tree) and item.data == 'item_ref':
                # item_ref carries one ref id -- either item_ref_id ("3 a") or
                # itemnumeric_ref_id ("tredje punkten"); emit whichever it is
                # (lagrum_uri folds both to the N fragment letter)
                self.emit(find_refids(item),
                          match, out, context, span=_node_span(item))
        match.currentsection = None

    def fmt_piece_and_item_refs(self, node, match, out, context):
        self.emit(find_refids(node.children[0]), match, out, context,
                  span=_node_span(node.children[0]))
        self.emit(find_refids(node.children[-1]), match, out, context,
                  span=_node_span(node.children[-1]))

    def fmt_piece_item_refs(self, node, match, out, context):
        piece = node.children[0]
        match.currentpiece = find_refids(piece)['piece']
        items = [c for c in node.children[1:] if isinstance(c, Tree)]
        for i, item in enumerate(items):
            span = ((_node_span(piece)[0], _node_span(item)[1]) if i == 0
                    else _node_span(item))
            self.emit(find_refids(item), match, out, context, span=span)
        match.currentpiece = None

    def fmt_external_ref(self, node, match, out, context):
        law_node = node.children[-1]
        anonymous = (isinstance(law_node, Tree) and
                     law_node.data == 'anonymous_external_law')
        self.resolve_law(law_node, match)
        inner = []
        self.dispatch(node.children[0], match, inner, context)
        out.extend(inner)
        # One combined link covers the whole expression for a single
        # section-bearing reference (the old format_ExternalRefs
        # single-GenericRefs/single-SectionRefID check); otherwise the law
        # expression gets its own link, with the chapter cleared first
        # (format_ExternalLaw). "samma lag" never links itself -- it has no
        # law-name or SFS-number tokens. ENKLALAGRUM also folds a lone
        # chapter-only ref into the law link (the old simplified grammar's
        # combine rule); plain LAGRUM keeps the separate law link unless the
        # single inner ref bears a section.
        #
        # An *anonymous* law ("lagen (2016:1145) om offentlig upphandling")
        # combines too. The old engine split it (format_ExternalRef's
        # AnonymousExternalLaw branch) because it could not tell where the law's
        # name ended and the sentence resumed -- but that is only an argument
        # against extending the link *past* the SFS number, which this does not
        # do: the span still ends at the closing paren, and the trailing "om …"
        # stays plain text. What the split actually produced was a second,
        # pinpointless edge to the act as a whole from every pinpointed
        # citation, which read as "half the corpus cites this law as such" in
        # the whole-document panel (S2).
        combined = len(inner) == 1 and (self.enkla or 'section' in inner[0])
        same_law = isinstance(law_node, Tree) and law_node.data == 'same_law'
        if combined and '_span' in inner[0]:
            # the single link swallows the trailing law expression, so its
            # text reads "4 kap. 24 § … tullagen (2000:1281)" as one link
            inner[0]['_span'] = (inner[0]['_span'][0], _node_span(law_node)[1])
        if not combined and not same_law:
            match.currentchapter = None
            # an anonymous law links just its SFS number ("(1976:580)" ->
            # "1976:580"); a named one links the whole "name (number)"
            span = _law_id_span(law_node) if anonymous else _node_span(law_node)
            self.emit({'law': match.currentlaw}, match, out, context, span=span)

    fmt_external_refs = fmt_external_ref

    def fmt_named_external_law_ref(self, node, match, out, context):
        self.resolve_law(node, match)
        # a named law links its name and any trailing "(SFS-number)"
        self.emit({'law': match.currentlaw}, match, out, context,
                  span=_node_span(node))
        if self.nobaseuri:  # old format_NamedExternalLawRef side effect
            context['law'] = match.currentlaw

    # --- KORTLAGRUM (abbreviated lagrum) ---

    def fmt_kortlagrum_normal(self, node, match, out, context):
        match.currentlaw = self.abbrev_to_sfsid(node)
        genref = next(c for c in node.children if isinstance(c, Tree))
        self.dispatch(genref, match, out, context)

    def fmt_kortlagrum_short(self, node, match, out, context):
        match.currentlaw = self.abbrev_to_sfsid(node)
        nums = [t.value for t in node.children
                if isinstance(t, Token) and t.type == 'NUMBER']
        attrs = {'chapter': nums[0], 'section': nums[1]}
        piece = next((c for c in node.children
                      if isinstance(c, Tree) and c.data == 'piece_ref'), None)
        if piece is not None:
            attrs.update(find_refids(piece))
        self.emit(attrs, match, out, context, span=_node_span(node))

    def abbrev_to_sfsid(self, node):
        """Resolve the LAW_ABBREV token of a kortlagrum match -- the
        document's own definition first (see _learn_abbrev), then the global
        table -- or raise NoLink for an unknown abbreviation (consumes the
        span, no link)."""
        abbrev = next(t.value for t in _tree_tokens(node)
                      if t.type == 'LAW_ABBREV')
        state = self.state
        if (local := state.abbrevs.get(abbrev)) is not None:
            state.abbrev_uses[abbrev] = state.abbrev_uses.get(abbrev, 0) + 1
            return local
        law = _named_at(self.abbreviations, abbrev, self.written)
        if law is None:
            raise NoLink()
        return _normalize_sfsid(law)

    def _learn_abbrev(self, text, end, attrs):
        """Register a document-local abbreviation declared right after the
        law reference just emitted at text[..end]: "lagen (1994:1564) om
        alkoholskatt, förkortad LAS" binds LAS to 1994:1564 for the rest of
        this document, shadowing the global table (where LAS is
        anställningsskyddslagen -- prop. 2021/22:61 misbound 324 links that
        way). The evidence rule is namedlaw_to_sfsid's: the document saying
        which act it means beats any table. Only abbreviations the grammar
        already knows are learned -- an unknown one can never resolve, so
        there is nothing to shadow -- and the first definition wins, matching
        the define-at-first-use drafting convention."""
        m = RE_ABBREV_DEF.match(text, end)
        if m is None:
            return
        abbrev = m.group('kw') or m.group('paren')
        if abbrev not in self.abbreviations or abbrev in self.state.abbrevs:
            return
        law = _normalize_sfsid(str(attrs['law']))
        self.state.abbrevs[abbrev] = law
        glob = _named_at(self.abbreviations, abbrev, self.written)
        glob = _normalize_sfsid(glob) if glob else None
        if glob != law:
            self.state.abbrev_shadows[abbrev] = glob

    def local_abbreviations(self):
        """The abbreviations this document defined for itself, for artifact
        stamping: {abbrev: {"sfs": bound id, "uses": n resolutions through
        the binding[, "shadows": what the global table says instead]}}.
        A "shadows" key means an unshadowed parse would have linked those
        uses to a different law."""
        state = self.state
        return {abbrev: ({"sfs": law, "uses": state.abbrev_uses.get(abbrev, 0)}
                         | ({"shadows": state.abbrev_shadows[abbrev]}
                            if abbrev in state.abbrev_shadows else {}))
                for abbrev, law in state.abbrevs.items()}

    def resolve_law(self, law_node, match):
        """Set match.currentlaw from an external_law subtree. Raises
        NoLink for unknown/blacklisted law names: the whole match is
        then consumed without producing links, like the old engine."""
        if isinstance(law_node, Tree) and law_node.data != 'same_law':
            refids = find_refids(law_node)
            name = next((t.value for t in _tree_tokens(law_node)
                         if t.type == 'NAMED_LAW'), None)
            if 'law' in refids:
                match.currentlaw = _normalize_sfsid(refids['law'])
                if name:
                    self.state.namedlaws[_normalize_lawname(name)] = \
                        match.currentlaw
                return
            match.currentlaw = self.namedlaw_to_sfsid(name)
            if match.currentlaw is None:
                raise NoLink()
            return
        if self.state.lastlaw is None:
            raise NoLink()
        match.currentlaw = self.state.lastlaw

    def namedlaw_to_sfsid(self, name):
        """The act a spelled law name denotes, as of the document's own date.

        A name the *document itself* introduced ("lagen (1994:953) om ...") wins
        over the dataset whatever the date: it is this document saying which act
        it means, which is better evidence than any table."""
        name = _normalize_lawname(name)
        if name in NOLAW or name in LAW_SYNONYMS:
            return None
        return (self.state.namedlaws.get(name)
                or _named_at(self.namedlaws, name, self.written))

    # --- EU ---

    def _eu_celex_uri(self, celex, pin=NO_PINPOINT, remember=True):
        """ext/celex/<CELEX> deep-linked to what `pin` names inside it. Names the
        act as the document's current EU act (for later anaphora) unless
        `remember` is false (an anaphoric ref must not refresh what it points at).
        The fragment is the dotted grammar the eurlex renderer mints for the
        target anchor (#6.1.c, #9.2.S2)."""
        if remember:
            self.state.last_eu_act = celex
        frag = eu_fragment(pin)
        return self.base + 'ext/celex/' + celex + ('#' + frag if frag else '')

    def _treaty_uri(self, path, pin=NO_PINPOINT):
        """ext/<path> (celex/12016E/TXT, coe/005) deep-linked to what `pin` names,
        like _eu_celex_uri but for a primary-law instrument keyed by name. A treaty
        is never remembered as the anaphora act in focus. An EU instrument
        fragments its article the CELEX way (#16.2); a Council-of-Europe treaty
        uses the CoE article grammar its own artifact mints (#A8, #A6P1)."""
        uri = self.base + 'ext/' + path
        if not pin.artikel:
            return uri
        if path.startswith('coe/'):
            # a Council-of-Europe treaty uses the CoE article grammar its own
            # artifact mints (#A8, #A6P1), shared via the dependency-free
            # lib.coe_ids leaf (importing lib.coe here would cycle). It has no
            # stycke form, so a stycke pinpoint lands on the article.
            return uri + '#' + coe_article_fragment(pin.artikel, pin.underartikel,
                                                    pin.punkt)
        return uri + '#' + eu_fragment(pin)

    def _article_specs(self, node):
        """Per-article ``(Pinpoint, span)``. A single article
        keeps the whole eu_ref span (so "artikel 47 i stadgan" links as one
        phrase); a coordinated list or a range ("artiklarna 101 och 102", "12–15")
        links each number on its own span.

        A lettered point coordinates *within* one article ("artikel 6.1 c och e"
        -- the form dataskyddslagen uses), so an item carrying several letters
        expands to one link per letter, each on its own span. `find_refids`
        collapses same-named subtrees into one dict entry and so cannot see the
        second letter; the letters are read off the item's own subtrees."""
        items = [s for s in node.iter_subtrees_topdown() if s.data == 'artikel_item']

        def letters(item):
            return [s for s in item.iter_subtrees_topdown()
                    if s.data == 'punkt_ref_id']

        def spec(d, span, punkt):
            # the stycke is written as an ordinal word ("andra stycket"); the
            # anchor counts from 1, so it is settled to a digit here. The grammar's
            # ORDINAL_WORD and this table are the same nine words, so a miss is a
            # broken program, not a pinpoint to quietly drop
            stycke = d.get('stycke')
            return (Pinpoint(d.get('artikel'), d.get('underartikel'),
                             ORDINALS[stycke] if stycke else None, punkt), span)

        out = []
        for it in items:
            d = find_refids(it)
            if len(items) == 1:
                # from "artikel" to the node end -- the whole "artikel N i
                # <instrument>" for the article-first order, just "artikel N" when
                # the instrument was named first ("<treaty>, särskilt artikel N")
                span = (_node_span(subtree(node, 'artikel_part'))[0],
                        _node_span(node)[1])
            else:
                span = _node_span(it)
            # A letter pinpoints whether or not a sub-article precedes it, so
            # "artikel 3 a" is point (a) of article 3. Swedish also renders an
            # *inserted* article with a space ("artikel 168 a" = 168a), and the
            # two are indistinguishable from the text alone -- but the corpus
            # settles which is worth optimising for: of the sub-article-less
            # hits in a 3,000-document scan of the case-law corpus, nearly all
            # are points (Reg. 469/2009 art. 3 a-d and the skyddsgrund
            # directive's art. 2 a-n have no numbered paragraphs at all, and a
            # förordningsmotiv citing the birds directive's "artikel 5 a" says
            # *punkten* in the same sentence). One inserted article, 168 a of
            # the VAT directive, is the exception that pays for the rest.
            ls = letters(it)
            if len(ls) <= 1:
                out.append(spec(d, span, _token_text(ls[0]) if ls else None))
            else:
                # each letter is its own link, on the letter's own span -- the
                # phrase span would make every one of them cover the whole
                # coordination and overlap
                out.extend(spec(d, _node_span(l), _token_text(l)) for l in ls)
        return out

    @staticmethod
    def _emit_uris(out, specs, node, build):
        """Emit one ``{_uri, _span}`` per article spec via ``build(pinpoint)``; an
        instrument named with no article links itself once."""
        if not specs:
            out.append({'_uri': build(NO_PINPOINT), '_span': _node_span(node)})
            return
        for pin, span in specs:
            out.append({'_uri': build(pin), '_span': span})

    def _recital_specs(self, node):
        """Per-recital ``(recital, span)``. A single recital takes the whole
        "skäl 108" as its span, a coordinated list one number each -- the
        convention `_article_specs` already follows. The span never reaches the
        act that follows, unlike a lone article's: the act phrase may be shared
        with a coordinated article ("skäl 108 och artikel 46.1 i X"), and two
        links cannot both own it."""
        items = [it for it in node.iter_subtrees_topdown()
                 if it.data == 'skal_item']
        part = _node_span(subtree(node, 'skal_part'))
        return [(find_refids(it)['skal'],
                 part if len(items) == 1 else _node_span(it))
                for it in items]

    @staticmethod
    def _act_span(node):
        """The span of the phrase naming the act, inside an eu_ref that also
        cites a recital. A recital link owns only "skäl N", so the act keeps its
        own link over its own words -- which is what it had before recitals were
        grammar at all ("skäl 17 i direktiv 2000/31/EG" used to match just the
        directive), and losing it would cost both that link and the anaphora
        memory every later "artikel N i direktivet" depends on. Every eu_ref
        alternative carrying a recital ends in `rattsakt_part`, so its absence
        is a broken grammar, not a case to fall back for."""
        return _node_span(subtree(node, 'rattsakt_part'))

    def _emit_act_ref(self, out, node, parts, specs, build):
        """Emit one eu_ref onto a single act: each cited recital on its own
        "skäl N", then each cited article via `build`. Where *only* recitals were
        cited, the act still takes the link over the words that name it -- and
        takes it through `build`, which is what records it as the act in focus
        for the anaphora that follows."""
        if 'skal_item' in parts:
            act_uri = build(NO_PINPOINT)
            self._emit_recitals(out, node, act_uri)
            if not specs:
                out.append({'_uri': act_uri, '_span': self._act_span(node)})
                return
        self._emit_uris(out, specs, node, build)

    def _emit_recitals(self, out, node, act_uri):
        """Emit one ``#recital-N`` link per cited recital, onto `act_uri` (the
        act's own uri, no fragment). The fragment is the anchor the eurlex
        renderer mints for a recital (``#recital-108``), the way the article
        fragment is the dotted one it mints for an article."""
        for recital, span in self._recital_specs(node):
            out.append({'_uri': '%s#recital-%s' % (act_uri, recital),
                        '_span': span})

    def fmt_eu_ref(self, node, match, out, context):
        parts = {sub.data for sub in node.iter_subtrees()}
        specs = self._article_specs(node) if 'artikel_item' in parts else []
        # a treaty / the Charter / the ECHR, cited by name -- linked onto its own
        # consolidated text (never anaphora-pinned onto the act in focus)
        if 'eu_treaty' in parts:
            path = TREATIES[_token_text(subtree(node, 'eu_treaty')).lower()]
            self._emit_uris(out, specs, node,
                            lambda pin: self._treaty_uri(path, pin))
            return
        # a known EU act named by short name ("artikel N i dataskyddsförordningen")
        if 'eu_namnakt' in parts:
            celex = self.named_acts.get(
                _token_text(subtree(node, 'eu_namnakt')).lower())
            if celex is None:
                raise NoLink()
            self._emit_act_ref(out, node, parts, specs,
                               lambda pin: self._eu_celex_uri(celex, pin))
            return
        # the definite generic noun ("artikel N i (det) förordningen/direktivet")
        # pinpoints the act in focus; a bare "artikel N" self-refers inside an EU
        # act, else anaphora-links the last named act
        bare = parts <= BARE_PARTS
        if 'eu_generic' in parts or bare:
            # a bare article only anaphora-links when it stands alone: a coordination
            # ("artikel 7 och 8.1 ...") or a trailing "i <instrument>" past the part
            # we matched may belong to a *different*, unrecognised act, so we refuse
            # rather than risk mis-pinning. The generic noun explicitly refers back,
            # so it may point at an external act a recital just named ("... i
            # förordning (EG) nr 45/2001. ... artikel N i förordningen").
            if bare:
                tail = self._scan_text[self._scan_base + _node_span(node)[1]:][:14]
                guard = (r"\s*(?:,|and|or)\s*\d|\s+of\s" if self.lang == "eng"
                         else r"\s*(?:,|och|eller|samt)\s*\d|\s+i\s")
                if re.match(guard, tail):
                    raise NoLink()
            target = (self.state.self_eu_act if bare else None) \
                or self.state.last_eu_act
            if not target:
                raise NoLink()
            # recitals reach here too, via the generic noun ("skäl 108 i
            # förordningen") -- the commonest of the recital forms in this
            # corpus. Emitting them through the shared path is what keeps that
            # from degrading to an act-level link under text promising a punkt.
            self._emit_act_ref(out, node, parts, specs,
                               lambda pin: self._eu_celex_uri(target, pin,
                                                             remember=False))
            return
        # an act cited by number ("(artikel N i) direktiv 2000/31/EG"): celex_uri
        # mints the act, and each cited article pinpoints that same act
        attrs = find_refids(node)
        tokens = _tree_tokens(node)
        for t in tokens:
            if t.type in ('DIREKTIV', 'FORORDNING', 'REKOMMENDATION', 'BESLUT'):
                attrs['akttyp'] = t.value
        if 'akttyp' not in attrs:  # bare "95/46/EG" / "(EEG) nr 2092/91"
            if 'direktiv_part' in parts:
                attrs['akttyp'] = 'direktiv'
            elif 'forordning_part' in parts:
                attrs['akttyp'] = 'förordning'
        # The pre-2015 "(EU) No <number>/<year>" regulation form is number-first;
        # directives and the post-2015 "(EU) <year>/<number>" form (all act
        # types) are year-first. forordning_part labels its first value
        # lopnummer, so for a year-first one (no "nr"/"No" token) move the year
        # into `ar` -- celex_uri range-checks and corrects either way.
        if ('forordning_part' in parts and 'ar' in attrs and 'lopnummer' in attrs
                and not any(t.type in ('NR', 'NO_EN') for t in tokens)):
            attrs['ar'], attrs['lopnummer'] = attrs['lopnummer'], attrs['ar']
        # the act alone, with any article pinpoint stripped -- what a recital
        # hangs its `#recital-N` off, and the base each article spec re-pinpoints
        act = {k: v for k, v in attrs.items()
               if k not in ('artikel', 'underartikel', 'stycke', 'punkt')}
        if 'skal_item' in parts:
            self._emit_recitals(out, node, celex_uri(act, self.base))
            if not specs:
                # ... and the act keeps the link over its own phrase that it had
                # before a preceding recital was part of the same reference
                self.emit(attrs, match, out, context, span=self._act_span(node))
                return
        if not specs:
            self.emit(attrs, match, out, context, span=_node_span(node))
            return
        for pin, span in specs:
            d = dict(act)
            d['artikel'] = pin.artikel
            if pin.underartikel:
                d['underartikel'] = pin.underartikel
            if pin.stycke:
                d['stycke'] = pin.stycke
            if pin.punkt:
                d['punkt'] = pin.punkt
            self.emit(d, match, out, context, span=span)

    # --- RATTSFALL (Swedish case law) ---

    def fmt_nja_referat(self, node, match, out, context):
        a = find_refids(node)
        out.append({'_uri': '%sdom/nja/%ss%s'
                    % (self.base, a['year'], a['sidnr'])})

    def fmt_nja_notis(self, node, match, out, context):
        a = find_refids(node)
        out.append({'_uri': '%sdom/nja/%s/not/%s'
                    % (self.base, a['year'], a['notnr'])})

    def fmt_court_referat(self, node, match, out, context):
        a = find_refids(node)
        out.append({'_uri': self.rattsfall_uri(a['court'], a['year'],
                                               ':' + a['rf_lopnr'])})

    def fmt_court_notis(self, node, match, out, context):
        a = find_refids(node)
        out.append({'_uri': self.rattsfall_uri(a['court'], a['year'],
                                               '/not/' + a['notnr'])})

    def rattsfall_uri(self, court, year, tail):
        # court code -> URI slug (lowercased, å/ä/ö folded): RÅ->ra, MÖD->mod
        slug = fold_swedish(court.lower())
        return '%sdom/%s/%s%s' % (self.base, slug, year, tail)

    # --- FORARBETEN (preparatory works) ---

    def fmt_forarb_doc(self, node, match, out, context):
        out.append({'_uri': self.forarb_doc_uri(node.children[0])})

    def fmt_forarb_refs(self, node, match, out, context):
        doc = node.children[0]
        base = self.forarb_doc_uri(doc.children[0])
        self.emit_pages(node, base, out, _node_span(doc)[0])

    def fmt_anon_prop_refs(self, node, match, out, context):
        if self.state.last_forarbete is None:
            raise NoLink()
        # the A_PROP token ("a. prop.") anchors the first page link's span
        self.emit_pages(node, self.state.last_forarbete, out,
                        node.children[0].start_pos)

    def emit_pages(self, node, base, out, doc_start):
        """One `#sid{n}` link per page, each spanning its own page-number
        token so a multi-page list ("s. 445 och 454", "s. 162-165") does
        not collapse to one overlapping span. The first link folds in the
        leading document text ("prop. … s. 445"); later pages link the
        bare number, the way the golden corpus draws the boundaries."""
        pages = [s for s in node.iter_subtrees_topdown() if s.data == 'sida_num']
        for i, page in enumerate(pages):
            pstart, pend = _node_span(page)
            span = (doc_start if i == 0 else pstart, pend)
            out.append({'_uri': '%s#sid%s' % (base, _token_text(page)),
                        '_span': span})

    def fmt_avsnitt_external(self, node, match, out, context):
        komm = context.get('kommittensbetankande')
        if not komm:
            raise NoLink()  # don't know which committee report -> unlinked
        base = self.base + 'sou/' + komm
        for frag in self.avsnitt_frags(node):
            out.append({'_uri': '%s#%s' % (base, frag)})

    def fmt_avsnitt_list(self, node, match, out, context):
        base = self.context_doc_uri(context)
        if base is None:
            raise NoLink()
        for frag in self.avsnitt_frags(node):
            out.append({'_uri': '%s#%s' % (base, frag)})

    DOC_PREFIX = {'prop_ref': 'prop', 'bet_ref': 'bet',
                  'skrivelse_ref': 'rskr', 'sou_ref': 'sou', 'ds_ref': 'ds'}

    def forarb_doc_uri(self, inner):
        """Base URI (no fragment) for a forarb_doc's inner ref subtree."""
        if inner.data == 'celex_ref':
            return self.forarbete_celex_uri(inner)
        if inner.data == 'prop_ref':
            riksmote, no = self.prop_riksmote_no(
                next(c for c in inner.children if isinstance(c, Tree)))
            uri = '%sprop/%s:%s' % (self.base, riksmote, no)
            self.state.last_forarbete = uri  # for a later "a. prop."
            return uri
        riksmote = _riksmote_str(subtree(inner, 'riksmote_ref_id'))
        no = _token_text(subtree(inner, 'bet_no_ref_id') if inner.data == 'bet_ref'
                        else subtree(inner, 'lopnr_ref_id'))
        return '%s%s/%s:%s' % (self.base, self.DOC_PREFIX[inner.data],
                               riksmote, no)

    def prop_riksmote_no(self, body):
        riksmote = _riksmote_str(subtree(body, 'riksmote_ref_id'))
        lopnr = _token_text(subtree(body, 'lopnr_ref_id'))
        if body.data == 'prop_x':
            sub = _token_text(subtree(body, 'subriksmote_ref_id'))
            return riksmote, (lopnr if sub == 'A' else sub + lopnr)
        return riksmote, lopnr

    def forarbete_celex_uri(self, inner):
        year, lopnr = _token_text(inner)[1:].split('L')
        if len(year) == 2:
            year = '19' + year
        return '%sext/celex/3%sL%s' % (self.base, year, lopnr)

    def context_doc_uri(self, context):
        if not all(k in context for k in ('type', 'year', 'no')):
            return None
        prefix = 'prop' if 'Proposition' in context['type'] else 'sou'
        return '%s%s/%s:%s' % (self.base, prefix, context['year'],
                               context['no'])

    def avsnitt_frags(self, node):
        return ['S' + _token_text(s).replace('.', '-')
                for s in node.iter_subtrees_topdown()
                if s.data == 'avsnitt_ref_id']

    # --- EURATTSFALL (CJEU case law) ---

    ECJ_DESCRIPTOR = {'C': 'J', 'T': 'A', 'F': 'W'}

    def fmt_ecj_ref(self, node, match, out, context):
        # the pre-1989 numbering ("Case 31/87") has no court letter: only the
        # ECJ existed, so its absence *means* the Court of Justice
        decision_node = subtree(node, 'ecj_decision', None)
        decision = _token_text(decision_node) if decision_node is not None else 'C'
        serial = _token_text(subtree(node, 'ecj_serial'))
        year = _token_text(subtree(node, 'ecj_year'))
        if len(year) == 2:  # two-digit year: <54 -> 20xx else 19xx
            year = ('20' if int(year) < 54 else '19') + year
        celex = '6%sC%s%04d' % (year, self.ECJ_DESCRIPTOR[decision],
                                int(serial))
        out.append({'_uri': self.base + 'ext/celex/' + celex})

    # --- MYNDIGHETSBESLUT (authority decisions) ---

    # --- FORESKRIFT (myndighetsföreskrifter) ---

    def fmt_foreskrift_ref(self, node, match, out, context):
        """"PMFS 2022:1" -> ``pmfs/2022:1``. The löpnummer is normalised the way
        the föreskrift basefiles mint it (no leading zeros), so a citation and
        the document it names agree on one uri."""
        toks = {t.type: t.value for t in _tree_tokens(node)}
        arsutgava, lopnummer = toks['FS_NUMBER'].split(':')
        out.append({'_uri': '%s%s/%s:%d' % (self.base, FS_SLUG[toks['FS_DESIGNATION']],
                                            arsutgava, int(lopnummer)),
                    '_span': _node_span(node)})

    def fmt_arn_refs(self, node, match, out, context):
        for dnr, span in _avg_ids(node, 'arn_ref_id'):
            out.append({'_uri': self.base + 'avg/arn/' + dnr, '_span': span})

    def fmt_jo_refs(self, node, match, out, context):
        for dnr, span in _avg_ids(node, 'jo_ref_id'):
            out.append({'_uri': self.base + 'avg/jo/' + dnr, '_span': span})

    def fmt_jk_refs(self, node, match, out, context):
        for dnr, span in _avg_ids(node, 'jk_ref_id'):
            if not _jk_is_date(dnr):  # a plausible date is not a diarienummer
                out.append({'_uri': self.base + 'avg/jk/' + dnr, '_span': span})

    # --- VAGLEDNING (EDPB / artikel 29-gruppens guidance) ---

    def _vagledning(self, node, serie, out):
        out.append({'_uri': '%sedpb/%s/%s'
                    % (self.base, serie,
                       vagledning_slug(_token_text(subtree(node, 'vl_id')))),
                    '_span': _node_span(node)})

    def fmt_riktlinje_ref(self, node, match, out, context):
        self._vagledning(node, 'riktlinjer', out)

    def fmt_rekommendation_ref(self, node, match, out, context):
        self._vagledning(node, 'rekommendationer', out)

    def fmt_wp_ref(self, node, match, out, context):
        number = _token_text(subtree(node, 'wp_id'))
        if number == WP29_GROUP:   # "WP29" names the group, not a document
            return
        out.append({'_uri': self.base + 'edpb/wp/' + number,
                    '_span': _node_span(node)})


# --------------------------------------------------------------------------
# the shared SFS-vocabulary parser
# --------------------------------------------------------------------------

@functools.cache
def _sfs_vocabulary():
    """The named-law and abbreviation tables, read once. Both derive from the
    same hand-edited dataset, so they load together: every source's parser wants
    the identical pair."""
    return (load_namedlaws(datasets.NAMEDLAWS),
            load_abbreviations(datasets.NAMEDLAWS))


def sfs_parser(basefile, parse_types, named_acts=None, written=None):
    """A citation parser for a source that cites Swedish law: the named-law table
    plus the abbreviations, over `parse_types`.

    Returns a **new** parser each call, with fresh document state (no learned law
    names, no "samma lag" focus), so a caller never has to remember to reset one
    and two parsers can never alias. That is cheap because the two expensive
    parts are cached elsewhere -- the vocabulary in `_sfs_vocabulary` here and
    the compiled grammar in `parser` above -- leaving construction well under a
    tenth of a millisecond warm, against ~25 ms for the cold grammar compile.

    A caller that keeps one parser across many documents must still call
    :meth:`LagrumParser.reset` between them; every caller here takes a new one.
    """
    namedlaws, abbreviations = _sfs_vocabulary()
    return LagrumParser(namedlaws, basefile=basefile,
                        abbreviations=abbreviations,
                        parse_types=list(parse_types),
                        named_acts=named_acts, written=written)
