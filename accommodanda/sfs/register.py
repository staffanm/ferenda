"""Parse the SFSR register page into amendment metadata + the L*-sourced
change tuples (change-act -> affected paragraph).

Source: ``site/data/sfs/register/{year}/{nr}.html`` -- the rkrattsbaser
"Visa register" page. The consolidated SFST page carries only the base-act
header; the per-amendment change list (Omfattning, ...) lives only here.

Ported from the old ``extract_metadata_register`` (sfs.py:604-789) minus the
framework: no rdflib document graph, no COIN minting, no consolidation
envelope. Per-amendment Förarbeten extraction is deferred until the
FORARBETEN citation grammar is ported (the ``forarbeten`` field stays []).

Each register row becomes one amendment entry, keyed by its own URI:
the base act first, then every change act. Property values that the old
pipeline only resolved during ``polish_metadata`` (department/publisher
labels -> org URIs, the SFS dataset URI, the rinfo ``owl:sameAs``) are
resolved here so the output matches the frozen golden form directly.
"""

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from rdflib import Graph
from rdflib.namespace import DCTERMS, FOAF, SKOS

from ..lib import util
from ..lib.errors import SkipDocument

BASE = "https://lagen.nu/"
RESOURCE_TTL = (Path(__file__).parent.parent.parent
                / "lagen/nu/res/extra/swedishlegalsource.ttl")
NAMEDLAWS_TTL = Path(__file__).parent.parent.parent / "lagen/nu/res/extra/sfs.ttl"
RINFO_PUBL = "http://rinfo.lagrummet.se/publ/sfs/"
CELEX_BASE = "https://lagen.nu/ext/celex/"
KONSOLIDERAD_TYPE = ("http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#"
                     "KonsolideradGrundforfattning")

# properties the golden always serializes as a list, even with one value
# (the rest collapse a single value to a scalar)
ALWAYS_LIST = {"rpubl:ersatter", "rpubl:upphaver", "rpubl:inforsI",
               "owl:sameAs", "rdf:type", "rpubl:forarbete",
               "rpubl:konsolideringsunderlag"}

# change-act constants the old extract_metadata_register hardcoded; resolved
# from labels to URIs below
PUBLISHER = "Regeringskansliet"
FORFATTNINGSSAMLING = "SFS"

# Omfattning change categories -> predicate (sfs.py:697-729). A None
# predicate (renames) yields no tuple but the raw text is still preserved.
OMFATTNING_PREDICATES = (
    (("ändr.", "ändr ", "ändring "), "rpubl:ersatter"),
    (("upph.", "upp.", "utgår"), "rpubl:upphaver"),
    (("ny", "ikrafttr.", "ikrafftr.", "ikraftr.", "ikraftträd.", "tillägg"),
     "rpubl:inforsI"),
)

# the old _find_utfardandedatum stub: a handful of change acts whose
# utfärdandedatum the register page omits (sfs.py:1087-1102)
UTFARDANDE = {
    "1915:218": "1915-12-31", "1987:329": "1987-12-31",
    "1994:1513": "1994-12-31", "1994:1809": "1994-12-31",
    "2013:363": "2013-05-23", "2008:344": "2008-05-22",
    "2009:1550": "2009-12-17", "2013:411": "2013-05-30",
    "2013:647": "2013-07-02", "2010:448": "2010-06-08",
    "2010:110": "2010-03-16", "2010:343": "2010-05-19",
}

# change acts to some special laws never repeat the base SFS no in their
# title, so the "base SFS not in title" check is suppressed (sfs.py:673-676)
TITLE_WITHOUT_BASEFILE = {
    "1949:381", "1958:637", "1987:230", "1970:994", "1998:808", "1962:700",
    "1942:740", "1981:774", "2010:110", "1949:105", "1810:0926", "1974:152",
    "2014:801", "1991:1469"}


@functools.cache
def resource_map():
    """label -> URI for orgs (foaf:name) and series (skos:altLabel), loaded
    from the live swedishlegalsource dataset."""
    graph = Graph().parse(RESOURCE_TTL, format="turtle")
    out = {}
    for _, prop in ((FOAF.name, FOAF.name), (SKOS.altLabel, SKOS.altLabel)):
        for subject, _, label in graph.triples((None, prop, None)):
            out.setdefault(str(label), str(subject))
    return out


def lookup_resource(label):
    """Resolve a label to its URI, or return the label unchanged when the
    dataset doesn't know it (surfaces as a diff rather than crashing the
    corpus run)."""
    return resource_map().get(label, label)


def sanitize_departement(val):
    """Drop sub-org ids: "Finansdepartementet S3" -> "Finansdepartementet";
    keep only the first of several space/comma-joined departments
    (sfs.py:883-898)."""
    while True:
        cleaned = re.sub(r",? (och|[A-ZÅÄÖ\d]{1,5})$", "", val)
        if val == cleaned:
            break
        val = cleaned
    if re.search("departementet,? [A-Z]", val):
        val = re.split(",? ", val)[0]
    return val


BASEFILE_RE = re.compile(r"\(?\d{4}:(bih\.?_?)?\d+( ?s\.? ?\d+)?\)?")


def forfattningstyp(rubrik):
    """rdf:type from the title (sfs.py:1074-1085)."""
    rubrik = util.normalize_space(
        BASEFILE_RE.sub("", rubrik).replace("()", ""))
    if (rubrik.startswith("Lag ")
            or (rubrik.endswith("lag") and not rubrik.startswith("Förordning"))
            or rubrik.endswith(("balk", "Tryckfrihetsförordning"))):
        return "rpubl:Lag"
    return "rpubl:Forordning"


def sfs_slug(sfsnr):
    """The lagen.nu slug for an SFS number: "1907:69 s.2" -> "1907:69_s.2"
    (sfs.py:638-639)."""
    arsutgava, lopnummer = sfsnr.split(":", 1)
    lopnummer = (lopnummer.replace("s. ", "s.")
                 .replace("bih. ", "bih.").replace(" ", "_"))
    return arsutgava, lopnummer


@dataclass
class ChangeAct:
    sfsnr: str
    rows: dict = field(default_factory=dict)


@dataclass
class Register:
    sfsnr: str
    header: dict
    changes: list = field(default_factory=list)

    @property
    def acts(self):
        """Base act first (its header doubles as a change-act rowdict),
        then every change act -- the iteration order the golden uses."""
        return [ChangeAct(sfsnr=self.sfsnr, rows=self.header)] + self.changes


def parse_register(path):
    soup = BeautifulSoup(Path(path).read_bytes(), "lxml")
    if soup.find(string="Sökningen gav ingen träff!"):
        raise SkipDocument("no register page at %s" % path)
    content = soup.find("div", "search-results-content")
    boxes = content.find_all("div", "result-inner-box")
    header = {"SFS-nummer": util.normalize_space(boxes[0].text.split("\xb7")[1]),
              "Rubrik": util.normalize_space(boxes[1].text)}
    for box in boxes[2:]:
        key, val = box.text.split(":", 1)
        header[key.strip()] = val.strip()
    basefile = header["SFS-nummer"]
    changes = []
    for container in content.find_all("div", "result-inner-sub-box-container"):
        sfsnr = container.find(
            "div", "result-inner-sub-box-header").text.split("SFS ")[1].strip()
        if basefile == "1993:1637" and sfsnr == "1993:1446":
            sfsnr = "1993:1646"  # uncorrectable error in the register page
        rows = {}
        for row in container.find_all("div", "result-inner-sub-box"):
            key, val = row.text.split(":", 1)
            rows[key.strip()] = util.normalize_space(val)
        changes.append(ChangeAct(sfsnr=sfsnr, rows=rows))
    return Register(sfsnr=basefile, header=header, changes=changes)


def register_from_source(source):
    """Build a :class:`Register` from a downloaded JSON ``_source`` (the new
    beta API) instead of the SFSR HTML page. The JSON carries the same
    register data pre-split into structured fields, so we map it back onto the
    rkrattsbaser key names and reuse all of amendment_properties unchanged.

    ``andringsforfattningar`` is newest-first in the API; the register page
    (and so the golden) lists change acts oldest-first."""
    org = source.get("organisation") or {}
    reg = source.get("register") or {}
    header = {"SFS-nummer": source["beteckning"], "Rubrik": source["rubrik"]}
    if org.get("namnOchEnhet"):
        header["Departement"] = org["namnOchEnhet"]
    if reg.get("forarbeten"):
        header["Förarbeten"] = reg["forarbeten"]
    if reg.get("celexnummer"):
        header["CELEX-nr"] = reg["celexnummer"]
    if source.get("ikraftDateTime"):
        header["Ikraft"] = source["ikraftDateTime"]
    changes = []
    for act in reversed(source.get("andringsforfattningar") or []):
        rows = {"SFS-nummer": act["beteckning"], "Rubrik": act.get("rubrik", "")}
        for json_key, row_key in (("anteckningar", "Omfattning"),
                                  ("forarbeten", "Förarbeten"),
                                  ("ikraftDateTime", "Ikraft"),
                                  ("celexnummer", "CELEX-nr")):
            if act.get(json_key):
                rows[row_key] = act[json_key]
        changes.append(ChangeAct(sfsnr=act["beteckning"], rows=rows))
    return Register(sfsnr=source["beteckning"], header=header, changes=changes)


def sfst_header_from_source(source):
    """Build the SFST (consolidated) header dict from a JSON ``_source``,
    matching parse_sfst_header's output keys (consumed by build_metadata)."""
    org = source.get("organisation") or {}
    fulltext = source.get("fulltext") or {}
    header = {"Rubrik": source["rubrik"]}
    if org.get("namnOchEnhet"):
        header["Departement"] = org["namnOchEnhet"]
    if fulltext.get("andringInford"):
        header["Ändring införd"] = fulltext["andringInford"]
    for json_key, hdr_key in (("utfardadDateTime", "Utfärdad"),
                              ("omtryck", "Omtryck"),
                              ("ovrigt", "Övrigt"),
                              ("upphavdGenom", "Författningen har upphävts genom")):
        if fulltext.get(json_key):
            header[hdr_key] = fulltext[json_key]
    for json_key, hdr_key in (("ikraftDateTime", "Ikraft"),
                              ("upphavdDateTime", "Upphävd"),
                              ("tidsbegransadDateTime", "Tidsbegränsad")):
        if source.get(json_key):
            header[hdr_key] = source[json_key]
    return header


def parse_sfst_header(path):
    """Header key→value pairs from the downloaded SFST (consolidated) page —
    the fields the register page lacks: Utfärdad, Ikraft, the
    'Ändring införd: t.o.m. SFS …' consolidation cutoff, Övrigt."""
    soup = BeautifulSoup(Path(path).read_bytes(), "lxml")
    boxes = [b for b in soup.find_all("div", "result-inner-box")
             if not b.find("div", "body-text")]
    header = {}
    if boxes:
        header["Rubrik"] = util.normalize_space(boxes[1].get_text())
        for box in boxes[2:]:
            text = box.get_text(" ", strip=True)
            if ":" in text:
                key, val = text.split(":", 1)
                header[key.strip()] = val.strip()
    return header


@functools.cache
def abbreviations():
    """basefile → abbreviation ("1998:808" → "MB"), from the named-law
    dataset's dcterms:alternative. (rdfs:label is in the same dataset but
    the old pipeline emitted it only for a subset we can't reconstruct, so
    it's not reproduced.)"""
    graph = Graph().parse(NAMEDLAWS_TTL, format="turtle")
    return {str(s).replace(BASE, ""): str(o)
            for s, _, o in graph.triples((None, DCTERMS.alternative, None))}


def build_metadata(sfst_header, register, basefile):
    """The document-level metadata of the *consolidated* act: register/header
    descriptive fields plus the consolidation envelope (the konsolidering URI
    + underlag derive from the 'Ändring införd t.o.m.' cutoff). The run-date
    fields (dcterms:issued, the date-stamped owl:sameAs) are not emitted —
    they're canonicalized away in the comparator."""
    base_uri = amendment_uri(basefile, BASE)
    m = re.search(r"t\.o\.m\.\s*SFS\s+(.+)$", sfst_header.get("Ändring införd", ""))
    cutoff = m.group(1).strip() if m else None
    version = cutoff or basefile
    # the identifier keeps the nicely-spaced SFS number ("1829:49 s. 279");
    # the cutoff only governs the version, never underlag membership (a
    # repealing act beyond the consolidation point is still an underlag)
    identifier = "SFS " + register.header.get("SFS-nummer", basefile)
    props = {
        "dcterms:identifier": identifier + (
            " i lydelse enligt SFS " + cutoff if cutoff else ""),
        "dcterms:title": sfst_header.get("Rubrik") or register.header.get("Rubrik"),
        "dcterms:publisher": lookup_resource(PUBLISHER),
        "rdf:type": [KONSOLIDERAD_TYPE],
        "rpubl:konsoliderar": base_uri,
        "rev:owl:sameAs": base_uri + "/konsolidering",
        "rpubl:konsolideringsunderlag": [base_uri] + [
            amendment_uri(c.sfsnr, BASE) for c in register.changes],
    }
    secondary = {lookup_resource(PUBLISHER): {"rdfs:label": PUBLISHER}}
    # the consolidated SFST header is authoritative (the old pipeline let it
    # override the register), so prefer it for the responsible department
    departement = sfst_header.get("Departement") or register.header.get("Departement")
    if departement:
        creator = lookup_resource(sanitize_departement(departement))
        props["dcterms:creator"] = creator
        secondary[creator] = {"rdfs:label": departement}
    for src, dst in (("Utfärdad", "rpubl:utfardandedatum"),
                     ("Ikraft", "rpubl:ikrafttradandedatum"),
                     ("Upphävd", "rpubl:upphavandedatum")):
        if sfst_header.get(src):
            props[dst] = sfst_header[src][:10]
    if sfst_header.get("Övrigt"):
        props["rdfs:comment"] = util.normalize_space(sfst_header["Övrigt"])
    omtryck = re.search(r"(\d{4}:\d+)\s*$", sfst_header.get("Omtryck", ""))
    if omtryck:
        props["rinfoex:omtryck"] = amendment_uri(omtryck.group(1), BASE)
    if sfst_header.get("Tidsbegränsad"):
        props["rinfoex:tidsbegransad"] = sfst_header["Tidsbegränsad"][:10]
    # the value may be "SFS 1994:14", "SFS1990:649" or a bare "2000:310"
    upphavt = re.search(r"(\d{4}:\d+(?: ?s\.? ?\d+)?)", sfst_header.get(
        "Författningen har upphävts genom",
        register.header.get("Författningen har upphävts genom", "")))
    if upphavt:
        props["rinfoex:upphavdAv"] = amendment_uri(upphavt.group(1), BASE)
    if basefile in abbreviations():
        props["dcterms:alternate"] = abbreviations()[basefile]
    for key, value in props.items():
        if isinstance(value, list):
            props[key] = sorted(set(value))
    return {"uri": base_uri + "/konsolidering/" + ":".join(sfs_slug(version)),
            "properties": {k: v for k, v in props.items() if v is not None},
            "secondary": secondary}


def omfattning_predicate(changecat):
    for prefixes, predicate in OMFATTNING_PREDICATES:
        if changecat.startswith(prefixes):
            return predicate
    return None  # renames ("nuvarande …", "rubr. närmast …") and unknowns


def amendment_uri(sfsnr, base):
    return base + ":".join(sfs_slug(sfsnr))


def lfragment(sfsnr):
    """The L-prefixed register fragment id for a change act, slug-form:
    "1902:71 s.1" -> "L1902:71_s.1"."""
    return "L" + ":".join(sfs_slug(sfsnr))


def forarbete_identifier(text):
    """The dcterms:identifier form of a matched förarbete citation, as the
    golden records it: "bet. 1980/81:JuU4" -> "Bet. 1980/81:JuU4", with the
    proposition spelling normalized (sfs.py:755 + prop_sanitize_identifier)."""
    ident = text[:1].upper() + text[1:]
    if ident.startswith(("Prop", "PROP")):
        ident = ident.replace("PROP", "Prop").replace("\xa0", " ")
        if ident.startswith("Prop "):
            ident = "Prop. " + ident[len("Prop "):]
        if re.match(r"Prop\.\d", ident):  # missing space: "Prop.1992"
            ident = ident.replace("Prop.", "Prop. ", 1)
        # the not-uncommon "2009/2010:87" -> "2009/10:87"
        m = re.search(r"(\d{4})/(\d{4}):(\d+)$", ident)
        if m and m.group(2) != "2000" and int(m.group(1)) == int(m.group(2)) - 1:
            ident = ident.replace(m.group(2), m.group(2)[-2:])
    return ident


def parse_forarbeten(text, parser):
    """The förarbete identifiers in a register "Förarbeten:" field, sorted
    like the golden (a FORARBETEN-typed parser is supplied by the caller)."""
    return sorted(forarbete_identifier(ref.text)
                  for ref in parser.parse_text(text, predicate="rpubl:forarbete"))


def amendment_properties(act, basefile, omfattning_parser, base):
    """The flattened properties dict for one amendment entry, in the
    post-polish form the golden records (labels resolved to URIs)."""
    sfsnr = act.sfsnr
    arsutgava, lopnummer = sfs_slug(sfsnr)
    props = {
        "dcterms:identifier": "SFS " + sfsnr,
        "rpubl:arsutgava": arsutgava,
        "rpubl:lopnummer": lopnummer,
        "dcterms:publisher": lookup_resource(PUBLISHER),
        "rpubl:beslutadAv": lookup_resource(PUBLISHER),
        "rpubl:forfattningssamling": lookup_resource(FORFATTNINGSSAMLING),
        "owl:sameAs": [RINFO_PUBL + ":".join(sfs_slug(sfsnr))],
    }
    for key, val in act.rows.items():
        if key == "SFS-nummer":
            continue
        elif key == "Departement":
            props["rpubl:departement"] = lookup_resource(
                sanitize_departement(val))
        elif key == "Rubrik":
            # title/rdf:type belong to the document metadata, not the
            # amendment register entry -- the golden NF carries neither here
            continue
        elif key == "Observera":
            props["rdfs:comment"] = val
        elif key == "Upphävd":
            props["rpubl:upphavandedatum"] = val[:10]
        elif key == "Ikraft":
            props["rpubl:ikrafttradandedatum"] = val[:10]
        elif key == "Tidsbegränsad":
            continue  # a document-level field, not an amendment property
        elif key == "CELEX-nr":
            celex = re.findall(r"3\d{2,4}[LR]\d{4}", val)
            if celex:
                props["rpubl:genomforDirektiv"] = [CELEX_BASE + c for c in celex]
                props["rpubl:celexNummer"] = list(celex)
        elif key == "Omfattning":
            for changecat in val.split("; "):
                predicate = omfattning_predicate(changecat)
                if predicate is None:
                    continue
                for ref in omfattning_parser.parse_text(
                        changecat, predicate=predicate):
                    props.setdefault(ref.predicate, []).append(ref.uri)
            props["rpubl:andrar"] = val
    if sfsnr in UTFARDANDE:
        props["rpubl:utfardandedatum"] = UTFARDANDE[sfsnr]
    # RDF object lists are sets (dedupe), and single values of a not-always-
    # multivalued property collapse to a scalar, matching the golden's
    # add_meta normalization
    for key, value in list(props.items()):
        if isinstance(value, list):
            value = sorted(set(value))
            props[key] = (value if key in ALWAYS_LIST or len(value) != 1
                          else value[0])
    return props
