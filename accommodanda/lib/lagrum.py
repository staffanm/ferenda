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

from lark import Lark, Token, Tree
from lark.exceptions import UnexpectedInput

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

LAGRUM = 'LAGRUM'                  # SFS references ("3 kap. 2 § lagen …")
KORTLAGRUM = 'KORTLAGRUM'          # abbreviated SFS refs ("3 § MBL", "JB 22:2")
EULAGSTIFTNING = 'EULAGSTIFTNING'  # EU treaties/regulations/directives
RATTSFALL = 'RATTSFALL'            # Swedish case law ("NJA 1994 s. 12")
FORARBETEN = 'FORARBETEN'          # prop./bet./rskr./SOU/Ds/celex + page refs
EURATTSFALL = 'EURATTSFALL'        # CJEU case law ("mål C-176/09")
MYNDIGHETSBESLUT = 'MYNDIGHETSBESLUT'  # JO/JK/ARN decisions (by diarienummer)
ENKLALAGRUM = 'ENKLALAGRUM'        # absolute-only SFS refs (förarbete-safe)

# deterministic assembly order; kortlagrum first so its roots take
# precedence in the ?ref alternation (an abbreviated form must win over a
# bare generic ref that would leave the abbreviation unconsumed)
TYPE_ORDER = [KORTLAGRUM, ENKLALAGRUM, LAGRUM, EULAGSTIFTNING, RATTSFALL,
              FORARBETEN, EURATTSFALL, MYNDIGHETSBESLUT]

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
      | rattsakt_part
      | artikel_part

artikel_part: ARTIKEL _W artikel_ref_id (DOT underartikel_ref_id)?
artikel_ref_id: NUMBER
underartikel_ref_id: NUMBER

rattsakt_part: institution _W akttyp _W (direktiv_part | forordning_part) (_W av_datum)?
             | akttyp _W (direktiv_part | forordning_part) (_W av_datum)?
             | direktiv_part
             | forordning_part
institution: RADETS | EP_RADETS | KOMMISSIONENS
akttyp: DIREKTIV | FORORDNING
direktiv_part: ar_ref_id SLASH lopnummer_ref_id SLASH samarbete_ref_id
forordning_part: LPAR samarbete_ref_id RPAR (_W NR)? _W lopnummer_ref_id SLASH ar_ref_id
ar_ref_id: NUMBER
lopnummer_ref_id: NUMBER
samarbete_ref_id: SAMARBETE
av_datum: AV _W DEN _W datum_ref_id
datum_ref_id: DATUM
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
ARTIKEL.3: /artikel/
RADETS: /rådets/
EP_RADETS: /Europaparlamentets och rådets/
KOMMISSIONENS: /kommissionens/
DIREKTIV: /direktiv/
FORORDNING: /förordning/
SAMARBETE: /EEG|EG|EU/
NR: /nr/
AV: /av/
DEN: /den/
DATUM: /\d{1,2} (?:januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december) \d{4}/
COLON: ":"
"""

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

# grammar rule fragments per parse type (LAW_ABBREV is appended at build
# time from the supplied abbreviations, see parser())
RULES = {LAGRUM: LAGRUM_RULES, EULAGSTIFTNING: EU_RULES,
         KORTLAGRUM: KORTLAGRUM_RULES, RATTSFALL: RATTSFALL_RULES,
         FORARBETEN: FORARBETEN_RULES, EURATTSFALL: EURATTSFALL_RULES,
         MYNDIGHETSBESLUT: MYNDIGHETSBESLUT_RULES}

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

# court code -> URI slug (lowercased, å/ä/ö folded): RÅ->ra, MÖD->mod
SLUG_TRANS = str.maketrans('åäö', 'aao')

# Trigger patterns propose candidate start positions for the parser
# (mirroring what the old char-by-char PEG root could match), one source
# fragment per parse type. build_trigger() ORs together the fragments of
# the enabled types into a single re.X regex. Fragments are written with
# no leading "|" so they compose in any order.
LAGRUM_TRIGGER_SRC = r"""
    \b\d+(?:\ ?[a-n]\b)?
        (?:(?:\ ?,\ ?|\ och\ |\ eller\ |\ samt\ |\ ?[-–—]{1,2}\ ?)
           \d+(?:\ ?[a-n]\b)?)*\ §            # section (lists/intervals)
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
    \bartikel\ \d                             # EU article
  | \b(?:rådets|kommissionens|Europaparlamentets\ och\ rådets)\b
  | \b\d+/\d+/E(?:EG|G|U)\b                   # 95/46/EG
  | \bdirektiv\ (?=\(E(?:EG|G|U)\))           # bare "direktiv (EU) 2022/2555"
  | \(E(?:EG|G|U)\)\ (?:nr\ )?\d+/\d+         # (EEG) nr 2092/91
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
"""

# fires at each authority-decision marker (ARN/JO/JK)
MYNDIGHETSBESLUT_TRIGGER_SRC = r"""
    \bavgörande\ \d{4}-\d{2}-\d{2}              # ARN "avgörande <date>;"
  | \bARN:s\ änr\ \d                            # ARN "ARN:s änr"
  | \bJO\ \d                                    # JO "JO YYYY/YY"
  | \bJO:s\ beslut\ den\ \d                     # JO "JO:s beslut den …"
  | \b(?:[Dd]nr|ärende\ nr)\ \d+-\d+-\d+        # JK diarienummer (3 parts)
"""

TRIGGER_SRC = {LAGRUM: LAGRUM_TRIGGER_SRC, EULAGSTIFTNING: EU_TRIGGER_SRC,
               KORTLAGRUM: KORTLAGRUM_TRIGGER_SRC,
               RATTSFALL: RATTSFALL_TRIGGER_SRC,
               FORARBETEN: FORARBETEN_TRIGGER_SRC,
               EURATTSFALL: EURATTSFALL_TRIGGER_SRC,
               MYNDIGHETSBESLUT: MYNDIGHETSBESLUT_TRIGGER_SRC}


def expand_types(types):
    """Add each requested type's dependencies (one level is enough)."""
    out = set(types)
    for t in types:
        out.update(DEPENDS.get(t, ()))
    return frozenset(out)


def build_trigger(types):
    parts = [TRIGGER_SRC[t].strip() for t in TYPE_ORDER
             if t in types and t in TRIGGER_SRC]
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


@dataclass
class Ref:
    start: int
    end: int
    text: str
    predicate: str
    uri: str
    kind: str | None = None    # link flavour for the renderer (e.g. "term")


def interleave(text, refs):
    """Splice `refs` (Ref objects with disjoint [start, end) spans) into
    `text`, returning the inline-run list the artifact stores: plain `str`
    runs interleaved with {"predicate", "uri", "text"} link dicts, in
    document order. Text with no refs is a single-element list `[text]`;
    empty text is `[]`."""
    out, pos = [], 0
    for ref in sorted(refs, key=lambda r: r.start):
        if ref.start < pos:
            continue  # disjoint spans expected; ignore any stray overlap
        if ref.start > pos:
            out.append(text[pos:ref.start])
        run = {"predicate": ref.predicate, "uri": ref.uri, "text": ref.text}
        if ref.kind:
            run["kind"] = ref.kind
        out.append(run)
        pos = ref.end
    if pos < len(text):
        out.append(text[pos:])
    return out


@dataclass
class DocState:
    """Reference-parser state with document lifetime."""
    lastlaw: str | None = None
    namedlaws: dict = field(default_factory=dict)  # learned in-document
    last_forarbete: str | None = None  # base URI of last prop ("a. prop.")
    last_eu_act: str | None = None     # CELEX of the last named EU act (anaphora)


class NoLink(Exception):
    """The match is consumed but yields no links (unknown named law,
    or an EU reference too incomplete for a celex number)."""


RE_BASEFILE_LAW = re.compile(r'\d+:(?:bih\.[_ ]?|N)?\d+(?:[_ ]s\.\d+|[_ ]\d+)?')


def fragment_context(basefile, fragment):
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


def normalize_sfsid(sfsid):
    return re.sub(r'(\d+:\d+)\.(\d)', r'\1 \2', sfsid).replace('\n', ' ')


def normalize_lawname(lawname):
    lawname = lawname.lower()
    return lawname[:-1] if lawname.endswith('s') else lawname


def load_namedlaws(path):
    """Map each named law ("brottsbalken", "miljöbalken", …) to its SFS id,
    from the hand-editable named-law dataset (law id -> {label?, abbr?})."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return {entry["label"]: lawid.replace('_', ' ')
            for lawid, entry in data.items() if "label" in entry}


def load_abbreviations(path):
    """Map each law abbreviation (JB, RB, BrB, …) to its SFS id -- the data
    the old KORTLAGRUM LawAbbreviation terminal was built from. A law may have
    several (its `abbr` is then a list); all of them resolve to the same law."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    out = {}
    for lawid, entry in data.items():
        abbr = entry.get("abbr")
        for a in ([abbr] if isinstance(abbr, str) else abbr or []):
            out[a] = lawid.replace('_', ' ')
    return out


def load_namedacts(path):
    """Map each EU-act short name or acronym (lower-cased) to its CELEX, from the
    hand-edited EU named-act dataset (CELEX -> {label?, abbr?}, each a str or a
    list). The EULAGSTIFTNING analogue of load_namedlaws/load_abbreviations: it
    lets the engine resolve "artikel N i dataskyddsförordningen" / "GDPR art 6" to
    the act's CELEX, the way named SFS laws resolve to an SFS id."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    out = {}
    for celex, entry in data.items():
        if not isinstance(entry, dict):
            continue                       # the leading "_comment" string
        for key in ("label", "abbr"):
            value = entry.get(key)
            for alias in ([value] if isinstance(value, str) else value or []):
                out[alias.lower()] = celex
    return out


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
    law = normalize_sfsid(attrs.pop('law')).replace('\xa0', ' ')
    # page-number laws slug like the old COIN templates: 1910:103_s._1
    law = re.sub(r' ?s\.? ?(\d+)$', r'_s._\1', law)
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
    letter = {'direktiv': 'L', 'förordning': 'R'}[attrs['akttyp']]
    uri = base + 'ext/celex/3%04d%s%04d' % (year, letter, number)
    if attrs.get('artikel'):
        uri += '#' + attrs['artikel']
        if attrs.get('underartikel'):
            uri += '.' + attrs['underartikel']
    return uri


# the named-EU-act extension, added only when EULAGSTIFTNING is active AND the
# caller supplies act aliases (like LAW_ABBREV for KORTLAGRUM): a known act name
# becomes a valid `rattsakt_part`, so "artikel N i <name>" and "artikel N <name>"
# resolve to the act's CELEX. A leading determiner/adjective (den, EU:s, allmänna)
# is grammar, so `label` data carries only the noun-phrase head.
#
# It also turns on article *anaphora*: once an EU act is named, a later "artikel
# N i förordningen" (the definite generic noun) or a bare "artikel N" pinpoints
# the same act -- but a bare article trailed by a *different* instrument
# (europakonventionen, stadgan, EUF-fördraget) is captured by `eu_other` and left
# unlinked, so an ECHR/Charter/treaty article is never mis-pinned onto the act.
EU_NAMNAKT_RULES = r"""
%extend rattsakt_part: eu_namnakt_full
%extend rattsakt_part: eu_generic
%extend eu_ref: artikel_part _W eu_namnakt_full
%extend eu_ref: artikel_part _W (IN _W)? eu_other
eu_namnakt_full: (EU_DET _W)? (EU_ADJ _W)? eu_namnakt
eu_namnakt: EU_NAMNAKT
eu_generic: EU_GENERIC
eu_other: EU_OTHER
EU_DET: "EU:s" | "den" | "det"
EU_ADJ: "allmänna" | "allmän"
EU_GENERIC: "förordningen" | "direktivet" | "rättsakten"
EU_OTHER: "europakonventionen" | "Europakonventionen" | "EKMR" | "rättighetsstadgan" | "stadgan" | "EU-stadgan" | "EUF-fördraget" | "FEUF" | "EU-fördraget"
"""


@functools.cache
def parser(requested, expanded, abbrevs=(), eu_acts=()):
    """Earley parser compiled for a set of parse types. Root alternatives
    come only from the explicitly `requested` types; rule fragments and
    terminals from the dependency-`expanded` set -- so a dependency
    (KORTLAGRUM/ENKLALAGRUM both depend on LAGRUM) lends its productions
    without also contributing its own ?ref roots. `abbrevs` (sorted
    longest-first) supplies the KORTLAGRUM LAW_ABBREV terminal; `eu_acts`
    (likewise) the EULAGSTIFTNING EU_NAMNAKT terminal of known EU-act names."""
    roots = [r for t in TYPE_ORDER if t in requested for r in ROOTS[t]]
    grammar = "start: ref\n?ref: " + "\n    | ".join(roots) + "\n"
    grammar += "".join(RULES.get(t, '') for t in TYPE_ORDER if t in expanded)
    grammar += TERMINALS
    if KORTLAGRUM in expanded:
        grammar += "\nLAW_ABBREV: %s\n" % " | ".join('"%s"' % a for a in abbrevs)
    if EULAGSTIFTNING in expanded and eu_acts:
        grammar += EU_NAMNAKT_RULES
        grammar += "\nEU_NAMNAKT: %s\n" % " | ".join('"%s"i' % a for a in eu_acts)
    return Lark(grammar, parser='earley')


def tree_tokens(tree):
    return list(tree.scan_values(lambda v: isinstance(v, Token)))


# Token types that qualify a number and so belong to the link they trail
# (or, leading, the link they precede): unit markers and ordinals. Pure
# connectives (HYP, COMMA, AND, ...) and law-name/punctuation tokens are
# never absorbed -- they stay as plain text between links.
ABSORB_MARKERS = frozenset((
    'SM', 'DSM', 'SECTION_CHAR', 'CHAPTER_CHAR', 'ORDINAL_WORD', 'PIECE_WORD',
    'PIECE_DIGIT', 'SENTENCE_WORD', 'KAP', 'MOM', 'PUNKTEN', 'ITEM_CHAR'))


def node_span(node):
    """(start, end) covering every token of `node`, in the coordinates of
    the window the tree was parsed from."""
    toks = tree_tokens(node)
    return min(t.start_pos for t in toks), max(t.end_pos for t in toks)


def law_id_span(law_node):
    """Span of just the law-identifying token (the SFS number, or the named
    law word) -- not the surrounding "lagen ( … )" scaffolding, which the
    old pipeline left outside the link."""
    toks = [t for t in tree_tokens(law_node)
            if t.type in ('LAW_REF_ID', 'NAMED_LAW')]
    return (toks[0].start_pos, toks[0].end_pos) if toks else node_span(law_node)


def find_refids(tree):
    """Collect *_ref_id subtree texts into an attribute dict, like the
    old find_attributes (key = production name minus the suffix)."""
    d = {}
    for sub in tree.iter_subtrees_topdown():
        if sub.data.endswith('_ref_id'):
            d[sub.data[:-7]] = ' '.join(
                t.value for t in tree_tokens(sub)).strip()
    return d


def subtree(tree, name):
    """First subtree (self included) with the given rule name."""
    return next(s for s in tree.iter_subtrees_topdown() if s.data == name)


def token_text(tree):
    """Concatenate the tree's token values (no separator)."""
    return ''.join(t.value for t in tree_tokens(tree))


def riksmote_str(node):
    """Riksmöte id keeping the slash form ("1996/97", "1971")."""
    return '/'.join(t.value for t in tree_tokens(node) if t.type == 'NUMBER')


def avg_ids(node, name):
    """Diarienummer strings of the named *_ref_id rule, in document order."""
    return [token_text(s) for s in node.iter_subtrees_topdown()
            if s.data == name]


def jk_is_date(dnr):
    """A JK diarienummer NNNN-MM-DD whose first part is a recent year and
    whose other parts read as month/day is probably a date, not a ref."""
    ordinal, second, third = (int(x) for x in dnr.split('-'))
    return (1980 <= ordinal <= date.today().year
            and 1 <= second <= 12 and 1 <= third <= 31)


class MatchState:
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
                 abbreviations=None, parse_types=None, named_acts=None):
        self.namedlaws = namedlaws
        self.basefile = basefile
        self.base = base
        self.named_acts = named_acts or {}
        # the document's own law URI -- the prefix every self-reference (a
        # relative "5 §" or an ändringshänvisning "#L<act>") is minted under,
        # used to recognise self-links from id-suppressed provisions.
        self.self_law_uri = lagrum_uri(
            {'law': fragment_context(basefile, None)['law']}, base)
        self.state = DocState()
        self.abbreviations = abbreviations or {}
        # Default set is SFS + EU (the SFS-pipeline behaviour). Supplying
        # `abbreviations` adds KORTLAGRUM, so existing call sites keep
        # working; callers wanting full control pass `parse_types`.
        if parse_types is None:
            parse_types = [LAGRUM, EULAGSTIFTNING]
            if abbreviations:
                parse_types.append(KORTLAGRUM)
        requested = frozenset(parse_types)
        self.parse_types = expand_types(parse_types)
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
                           eu_acts if EULAGSTIFTNING in self.parse_types else ())
        self.trigger = build_trigger(self.parse_types)

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
            context = fragment_context(self.basefile, fragment)
        self.nobaseuri = not context
        refs = []
        pos = 0
        while True:
            m = self.trigger.search(text, pos)
            if not m:
                break
            tree, length = self.try_parse(text, m.start())
            if tree is not None and self.acceptable(tree, text,
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
                        else:
                            uri = lagrum_uri(attrs, self.base)
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
        tokens = tree_tokens(tree)
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

    def acceptable(self, tree, text, end):
        """The old ChangeRef required either a trailing period or a
        following non-space/comma character -- "lag (1998:204) om ..."
        is not a change note (the SFS number alone gets linked on a
        later trigger), and neither is "Lag (1991:242)" at the very end
        of a text node (the old lookahead failed at end-of-buffer)."""
        node = tree.children[0]
        if isinstance(node, Tree) and node.data == 'change_ref':
            has_dot = any(t.type == 'DOT' for t in node.children
                          if isinstance(t, Token))
            if not has_dot and (end >= len(text) or text[end] in ' ,'):
                return False
        return True

    # --- formatting (ports of the old format_* semantics) ---

    def format_root(self, tree, context):
        """Return completed attribute dicts, one per link, in document
        order. Raises NoLink when the whole match must stay unlinked."""
        match = MatchState()
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
        self.emit({'lawref': normalize_sfsid(find_refids(node)['law'])},
                  match, out, context, span=node_span(node))

    def fmt_sfs_nr(self, node, match, out, context):
        law = normalize_sfsid(find_refids(node)['law'])
        if self.nobaseuri:  # the old format_SFSNr learned the base law
            context['law'] = law
        # link just the SFS number, not any enclosing "( … )"
        self.emit({'law': law}, match, out, context, span=law_id_span(node))

    def fmt_generic_ref(self, node, match, out, context):
        # no chapter stickiness here: the old GenericRef production
        # short-circuited to a single link before any state-setting
        # custom formatter could run, so "3 kap. 2 §, 13 §" resolves
        # "13 §" against the node's structural context, not chapter 3
        self.emit(find_refids(node), match, out, context, span=node_span(node))

    fmt_section_anatomy = fmt_generic_ref
    fmt_piece_item_ref = fmt_generic_ref

    def fmt_individual_chapter_section_refs(self, node, match, out, context):
        sections = [c for c in node.children
                    if isinstance(c, Tree) and c.data == 'section_ref']
        match.currentchapter = find_refids(node.children[0])['chapter']
        # the chapter prefix ("3 kap.") folds into the first section link
        self.emit({'section': find_refids(sections[0])['section']},
                  match, out, context,
                  span=(node_span(node.children[0])[0],
                        node_span(sections[0])[1]))
        for section in sections[1:]:
            self.emit({'section': find_refids(section)['section']},
                      match, out, context, span=node_span(section))
        # chapter stays sticky (the old formatter never reset it)

    def fmt_chapter_section_refs(self, node, match, out, context):
        chapter_ref, sections = node.children
        match.currentchapter = find_refids(chapter_ref)['chapter']
        self.emit({'chapter': match.currentchapter}, match, out, context,
                  span=node_span(chapter_ref))
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
                  span=node_span(chapter_ref))
        self.dispatch(section_pieces, match, out, context)

    def fmt_single_section_ref(self, node, match, out, context):
        self.emit({'section': find_refids(node)['section']},
                  match, out, context, span=node_span(node))

    # only reachable as the final "eller 16 §" of alternate_section_refs;
    # every other rule containing section_ref formats it itself
    fmt_section_ref = fmt_single_section_ref

    def fmt_section_piece_refs(self, node, match, out, context):
        section = node.children[0]
        match.currentsection = find_refids(section)['section']
        pieces = [c for c in node.children[1:] if isinstance(c, Tree)]
        for i, piece in enumerate(pieces):
            # the section prefix ("42 §") folds into the first piece link
            span = ((node_span(section)[0], node_span(piece)[1]) if i == 0
                    else node_span(piece))
            self.emit(find_refids(piece), match, out, context, span=span)
        match.currentsection = None

    def fmt_section_piece_item_range(self, node, match, out, context):
        section, piece = node.children[0], node.children[1]
        match.currentsection = find_refids(section)['section']
        match.currentpiece = find_refids(piece)['piece']
        self.emit({'piece': match.currentpiece}, match, out, context,
                  span=(node_span(section)[0], node_span(piece)[1]))
        for item in node.children[2:]:
            if isinstance(item, Tree):
                self.emit(find_refids(item), match, out, context,
                          span=node_span(item))
        match.currentsection = None
        match.currentpiece = None

    def fmt_section_item_refs(self, node, match, out, context):
        section = node.children[0]
        match.currentsection = find_refids(section)['section']
        self.emit({'section': match.currentsection}, match, out, context,
                  span=node_span(section))
        for item in node.children[1:]:
            if isinstance(item, Tree) and item.data == 'item_ref':
                # item_ref carries one ref id -- either item_ref_id ("3 a") or
                # itemnumeric_ref_id ("tredje punkten"); emit whichever it is
                # (lagrum_uri folds both to the N fragment letter)
                self.emit(find_refids(item),
                          match, out, context, span=node_span(item))
        match.currentsection = None

    def fmt_piece_and_item_refs(self, node, match, out, context):
        self.emit(find_refids(node.children[0]), match, out, context,
                  span=node_span(node.children[0]))
        self.emit(find_refids(node.children[-1]), match, out, context,
                  span=node_span(node.children[-1]))

    def fmt_piece_item_refs(self, node, match, out, context):
        piece = node.children[0]
        match.currentpiece = find_refids(piece)['piece']
        items = [c for c in node.children[1:] if isinstance(c, Tree)]
        for i, item in enumerate(items):
            span = ((node_span(piece)[0], node_span(item)[1]) if i == 0
                    else node_span(item))
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
        # one combined link covers the whole expression only for a
        # single section-bearing reference to a named/same law (the old
        # format_ExternalRefs single-GenericRefs/single-SectionRefID
        # check); otherwise the law expression gets its own link, with
        # the chapter cleared first (format_ExternalLaw). "samma lag"
        # never links itself -- it has no law-name or SFS-number tokens
        # ENKLALAGRUM also folds a lone chapter-only ref into the law link
        # (the old simplified grammar's combine rule); plain LAGRUM keeps
        # the separate law link unless the single inner ref bears a section
        combined = (not anonymous and len(inner) == 1
                    and (self.enkla or 'section' in inner[0]))
        same_law = isinstance(law_node, Tree) and law_node.data == 'same_law'
        if combined and '_span' in inner[0]:
            # the single link swallows the trailing law expression, so its
            # text reads "4 kap. 24 § … tullagen (2000:1281)" as one link
            inner[0]['_span'] = (inner[0]['_span'][0], node_span(law_node)[1])
        if not combined and not same_law:
            match.currentchapter = None
            # an anonymous law links just its SFS number ("(1976:580)" ->
            # "1976:580"); a named one links the whole "name (number)"
            span = law_id_span(law_node) if anonymous else node_span(law_node)
            self.emit({'law': match.currentlaw}, match, out, context, span=span)

    fmt_external_refs = fmt_external_ref

    def fmt_named_external_law_ref(self, node, match, out, context):
        self.resolve_law(node, match)
        # a named law links its name and any trailing "(SFS-number)"
        self.emit({'law': match.currentlaw}, match, out, context,
                  span=node_span(node))
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
        self.emit(attrs, match, out, context, span=node_span(node))

    def abbrev_to_sfsid(self, node):
        """Resolve the LAW_ABBREV token of a kortlagrum match, or raise
        NoLink for an unknown abbreviation (consumes the span, no link)."""
        abbrev = next(t.value for t in tree_tokens(node)
                      if t.type == 'LAW_ABBREV')
        law = self.abbreviations.get(abbrev)
        if law is None:
            raise NoLink()
        return normalize_sfsid(law)

    def resolve_law(self, law_node, match):
        """Set match.currentlaw from an external_law subtree. Raises
        NoLink for unknown/blacklisted law names: the whole match is
        then consumed without producing links, like the old engine."""
        if isinstance(law_node, Tree) and law_node.data != 'same_law':
            refids = find_refids(law_node)
            name = next((t.value for t in tree_tokens(law_node)
                         if t.type == 'NAMED_LAW'), None)
            if 'law' in refids:
                match.currentlaw = normalize_sfsid(refids['law'])
                if name:
                    self.state.namedlaws[normalize_lawname(name)] = \
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
        name = normalize_lawname(name)
        if name in NOLAW or name in LAW_SYNONYMS:
            return None
        return self.state.namedlaws.get(name) or self.namedlaws.get(name)

    # --- EU ---

    def _eu_celex_uri(self, celex, attrs, remember=True):
        """ext/celex/<CELEX> deep-linked to the cited article (and sub-article).
        Names the act as the document's current EU act (for later anaphora) unless
        `remember` is false (an anaphoric ref must not refresh what it points at)."""
        if remember:
            self.state.last_eu_act = celex
        uri = self.base + 'ext/celex/' + celex
        if attrs.get('artikel'):
            uri += '#' + attrs['artikel']
            if attrs.get('underartikel'):
                uri += '.' + attrs['underartikel']
        return uri

    def fmt_eu_ref(self, node, match, out, context):
        attrs = find_refids(node)
        parts = {sub.data for sub in node.iter_subtrees()}
        # an article of a *different* instrument (ECHR/Charter/treaty) -- captured
        # so it is consumed and skipped, never anaphora-pinned onto our act
        if 'eu_other' in parts:
            raise NoLink()
        # a known EU act named by short name ("artikel N i dataskyddsförordningen")
        if 'eu_namnakt' in parts:
            celex = self.named_acts.get(
                token_text(subtree(node, 'eu_namnakt')).lower())
            if celex is None:
                raise NoLink()
            out.append({'_uri': self._eu_celex_uri(celex, attrs),
                        '_span': node_span(node)})
            return
        # the definite generic noun ("artikel N i förordningen") pinpoints the act
        # in focus -- unambiguous, since it explicitly refers back to it
        bare = parts <= {'eu_ref', 'artikel_part', 'artikel_ref_id',
                         'underartikel_ref_id'}
        if 'eu_generic' in parts or bare:
            # a bare "artikel N" only anaphora-links when it stands alone: a
            # coordination ("artikel 7 och 8.1 ...") or a trailing "i <instrument>"
            # may belong to a *different* act named past the part we matched, so we
            # refuse rather than risk pinning a Charter/treaty article onto our act
            if bare:
                tail = self._scan_text[self._scan_base + node_span(node)[1]:][:14]
                if re.match(r"\s*(?:,|och|eller|samt)\s*\d|\s+i\s", tail):
                    raise NoLink()
            if not self.state.last_eu_act:
                raise NoLink()
            out.append({'_uri': self._eu_celex_uri(self.state.last_eu_act, attrs,
                                                   remember=False),
                        '_span': node_span(node)})
            return
        tokens = tree_tokens(node)
        for t in tokens:
            if t.type in ('DIREKTIV', 'FORORDNING'):
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
                and not any(t.type == 'NR' for t in tokens)):
            attrs['ar'], attrs['lopnummer'] = attrs['lopnummer'], attrs['ar']
        self.emit(attrs, match, out, context, span=node_span(node))

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
        slug = court.lower().translate(SLUG_TRANS)
        return '%sdom/%s/%s%s' % (self.base, slug, year, tail)

    # --- FORARBETEN (preparatory works) ---

    def fmt_forarb_doc(self, node, match, out, context):
        out.append({'_uri': self.forarb_doc_uri(node.children[0])})

    def fmt_forarb_refs(self, node, match, out, context):
        base = self.forarb_doc_uri(node.children[0].children[0])
        for page in self.sidor_pages(node):
            out.append({'_uri': '%s#sid%s' % (base, page)})

    def fmt_anon_prop_refs(self, node, match, out, context):
        if self.state.last_forarbete is None:
            raise NoLink()
        for page in self.sidor_pages(node):
            out.append({'_uri': '%s#sid%s'
                        % (self.state.last_forarbete, page)})

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
        riksmote = riksmote_str(subtree(inner, 'riksmote_ref_id'))
        no = token_text(subtree(inner, 'bet_no_ref_id') if inner.data == 'bet_ref'
                        else subtree(inner, 'lopnr_ref_id'))
        return '%s%s/%s:%s' % (self.base, self.DOC_PREFIX[inner.data],
                               riksmote, no)

    def prop_riksmote_no(self, body):
        riksmote = riksmote_str(subtree(body, 'riksmote_ref_id'))
        lopnr = token_text(subtree(body, 'lopnr_ref_id'))
        if body.data == 'prop_x':
            sub = token_text(subtree(body, 'subriksmote_ref_id'))
            return riksmote, (lopnr if sub == 'A' else sub + lopnr)
        return riksmote, lopnr

    def forarbete_celex_uri(self, inner):
        year, lopnr = token_text(inner)[1:].split('L')
        if len(year) == 2:
            year = '19' + year
        return '%sext/celex/3%sL%s' % (self.base, year, lopnr)

    def context_doc_uri(self, context):
        if not all(k in context for k in ('type', 'year', 'no')):
            return None
        prefix = 'prop' if 'Proposition' in context['type'] else 'sou'
        return '%s%s/%s:%s' % (self.base, prefix, context['year'],
                               context['no'])

    def sidor_pages(self, node):
        return [token_text(s) for s in node.iter_subtrees_topdown()
                if s.data == 'sida_num']

    def avsnitt_frags(self, node):
        return ['S' + token_text(s).replace('.', '-')
                for s in node.iter_subtrees_topdown()
                if s.data == 'avsnitt_ref_id']

    # --- EURATTSFALL (CJEU case law) ---

    ECJ_DESCRIPTOR = {'C': 'J', 'T': 'A', 'F': 'W'}

    def fmt_ecj_ref(self, node, match, out, context):
        decision = token_text(subtree(node, 'ecj_decision'))
        serial = token_text(subtree(node, 'ecj_serial'))
        year = token_text(subtree(node, 'ecj_year'))
        if len(year) == 2:  # two-digit year: <54 -> 20xx else 19xx
            year = ('20' if int(year) < 54 else '19') + year
        celex = '6%sC%s%04d' % (year, self.ECJ_DESCRIPTOR[decision],
                                int(serial))
        out.append({'_uri': self.base + 'ext/celex/' + celex})

    # --- MYNDIGHETSBESLUT (authority decisions) ---

    def fmt_arn_refs(self, node, match, out, context):
        for dnr in avg_ids(node, 'arn_ref_id'):
            out.append({'_uri': self.base + 'avg/arn/' + dnr})

    def fmt_jo_refs(self, node, match, out, context):
        for dnr in avg_ids(node, 'jo_ref_id'):
            out.append({'_uri': self.base + 'avg/jo/' + dnr})

    def fmt_jk_refs(self, node, match, out, context):
        for dnr in avg_ids(node, 'jk_ref_id'):
            if not jk_is_date(dnr):  # a plausible date is not a diarienummer
                out.append({'_uri': self.base + 'avg/jk/' + dnr})
