"""Hermetic (network-free) tests for the föreskrift harvest engine: the
classification and number-extraction logic that decides what each landing-page
file is and which regulation it belongs to. The live enumerate/resolve paths are
exercised against the real sites during a harvest, not here."""

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import requests
from bs4 import BeautifulSoup

from ferenda.foreskrift import agencies, download, harvest
from ferenda.foreskrift.agencies import REGISTRY, SJVFS
from ferenda.foreskrift.harvest import (
    DocRef,
    Skip,
    classify_file,
    classify_href,
    classify_section,
    classify_single,
)

# aliased: the tests below bind a local `ref` to each result, which would
# otherwise shadow the imported function
from ferenda.foreskrift.harvest import ref as _ref
from ferenda.foreskrift.parse import extract_publisher
from ferenda.lib.harvest import guarded_enumerate, write_record
from ferenda.lib.util import record_path


def anchor(html):
    """The first <a> in an HTML fragment, with its surrounding context (so a
    section classifier can find a preceding heading)."""
    return BeautifulSoup(html, "html.parser").find("a")


@dataclass
class _Agency:
    fs: str = "fffs"
    base_url: str = "https://example.se"
    index_url: str = "https://example.se/list"
    params: dict = field(default_factory=dict)
    designation: str | None = None


# --- classify_file: role + number from link text ---------------------------

def test_classify_file_regulation_consolidation_amendment():
    base = ("fffs", "2013", "10")
    assert classify_file(anchor('<a>FFFS 2013:10</a>'), *base) == ("regulation", "2013", "10")
    assert classify_file(anchor('<a>FFFS 2013:10 (konsoliderad version)</a>'), *base) \
        == ("consolidation", "2013", "10")
    assert classify_file(anchor('<a>FFFS 2026:27</a>'), *base) == ("amendment", "2026", "27")
    assert classify_file(anchor('<a>Beslutspromemoria FFFS 2026:27</a>'), *base)[0] == "memo"


# --- classify_section: role from the preceding <h2> ------------------------

def test_classify_section_uses_heading():
    base = ("kifs", "2022", "3")
    grund = anchor('<div><h2>Grundföreskrift</h2><p><a>KIFS 2022:3 om bekämpningsmedel</a></p></div>')
    assert classify_section(grund, *base) == ("regulation", "2022", "3")
    kons = anchor('<div><h2>Konsoliderad KIFS 2022:3</h2><p><a>KIFS 2022:3, konsoliderad</a></p></div>')
    assert classify_section(kons, *base)[0] == "consolidation"
    amend = anchor('<div><h2>Ändringsföreskrifter</h2><p><a>KIFS 2026:1</a></p></div>')
    assert classify_section(amend, *base) == ("amendment", "2026", "1")
    # the short 'konsol.' heading form (not full 'konsoliderad') is a consolidation
    konsol = anchor('<div><h2>Konsol. KIFS 2022:3</h2><p><a>KIFS 2022:3</a></p></div>')
    assert classify_section(konsol, *base)[0] == "consolidation"
    # a konsekvensutredning under the amendment heading is a memo, not law
    memo = anchor('<div><h2>Ändringsföreskrifter</h2><p><a>Konsekvensutredning av KIFS 2026:1</a></p></div>')
    assert classify_section(memo, *base)[0] == "memo"


# --- classify_href: role + number from the PDF filename --------------------

def test_classify_href_by_filename():
    base = ("nfs", "2014", "29")
    assert classify_href(anchor('<a href="/x/nfs-2014-29.pdf">f</a>'), *base) == ("regulation", "2014", "29")
    assert classify_href(anchor('<a href="/x/nfs-2014-29-konsoliderad-2025.pdf">k</a>'), *base)[0] == "consolidation"
    assert classify_href(anchor('<a href="/x/nfs-2026-5.pdf">a</a>'), *base) == ("amendment", "2026", "5")
    # PTSFS conventions: underscore separator, andring-prefixed amendment
    pts = ("ptsfs", "2023", "2")
    assert classify_href(anchor('<a href="/x/ptsfs-2023_2.pdf">g</a>'), *pts) == ("regulation", "2023", "2")
    assert classify_href(anchor('<a href="/x/andring-...-ptsfs-2023-3.pdf">a</a>'), *pts)[0] == "amendment"
    # a konsekvensutredning PDF is dropped entirely
    assert classify_href(anchor('<a href="/x/konsekvensutredning-ptsfs-2023-2.pdf">m</a>'), *pts) is None
    # Swedac abbreviates the consolidated version '-konsol' (not the full
    # 'konsoliderad'); RE_KONSOLIDERAD must catch the short form as consolidation
    stafs = ("stafs", "2022", "9")
    assert classify_href(anchor('<a href="/x/stafs-2022-9-konsol.pdf">k</a>'), *stafs) \
        == ("consolidation", "2022", "9")
    # the base (non-konsol) file of the same regulation stays a regulation
    assert classify_href(anchor('<a href="/x/stafs-2022-9.pdf">g</a>'), *stafs) \
        == ("regulation", "2022", "9")


def test_classify_href_reads_andring_by_where_it_stands():
    # "ändring" before the number names the document the file amends; after it
    # the word is part of this document's own title, and the file is its text.
    # Every SIFS ändringsföreskrift lost its PDF to the unconditional rule.
    assert classify_href(anchor('<a href="/x/andring-nfs-2014-29.pdf">a</a>'),
                         "nfs", "2014", "29") == ("amendment", "2014", "29")
    assert classify_href(
        anchor('<a href="/x/sifs-2023_1-foreskrift-om-andring-i-sifs-2020_2.pdf">a</a>'),
        "sifs", "2023", "1") == ("regulation", "2023", "1")


def test_classify_href_decodes_the_percent_escaped_vowel():
    # Energimarknadsinspektionen escapes the "ä", so no rule saw the "ändring"
    # and an amending act was stored as EIFS 2015:4's own text
    assert classify_href(
        anchor('<a href="/x/EIFS-om-%C3%A4ndring-av-EIFS-2015-4.pdf">a</a>'),
        "eifs", "2015", "4") == ("amendment", "2015", "4")


def test_classify_href_reads_the_k_that_marks_a_konsoliderad_text():
    # Naturvårdsverket writes the "k" straight after the number, or after a
    # hyphen. 34 stored NFS documents hang such a file (ten distinct texts --
    # one konsolidering serves several base regulations); without the rule each
    # is read as a plain number, which is how the konsoliderad text of NFS
    # 1987:13 came to sit in that regulation's own regulation slot.
    assert classify_href(anchor('<a href="/x/snfs-1987-12k.pdf">k</a>'),
                         "nfs", "1987", "12") == ("consolidation", "1987", "12")
    assert classify_href(anchor('<a href="/x/nfs-2018-5-k.pdf">k</a>'),
                         "nfs", "2018", "5") == ("consolidation", "2018", "5")


def test_classify_href_drops_the_guidance_beside_the_regulation():
    # Spelinspektionen hangs the regulation, a Swedish vägledning and English
    # guidelines, all three named after the regulation. The guidelines PDF was
    # what we stored and parsed as SIFS 2022:3.
    base = ("sifs", "2022", "3")
    assert classify_href(
        anchor('<a href="/x/vagledning-for-sifs-2022_3.pdf">v</a>'), *base) is None
    assert classify_href(
        anchor('<a href="/x/guidelines-for-sifs-2022_3.pdf">g</a>'), *base) is None
    assert classify_href(
        anchor('<a href="/x/sifs-2022_3-spelinspektionens-foreskrifter.pdf">f</a>'),
        *base) == ("regulation", "2022", "3")
    # the word has to be in the file's own name, not in the path: every
    # Spelinspektionen regulation is served out of /foreskrifter-och-vagledning/
    assert classify_href(
        anchor('<a href="/dokument/foreskrifter-och-vagledning/gallande/'
               'sifs-2022_1-foreskrift.pdf">f</a>'),
        "sifs", "2022", "1") == ("regulation", "2022", "1")
    # a konsekvensutredning is refused wherever the href carries it
    assert classify_href(
        anchor('<a href="/konsekvensutredningar/ptsfs-2023-2.pdf">m</a>'),
        "ptsfs", "2023", "2") is None


def test_classify_file_reads_a_companion_out_of_the_filename():
    # Havs- och vattenmyndigheten gives the rättelseblad and the original the
    # same link text; only the file's own name tells them apart
    base = ("hvmfs", "2018", "1")
    rattelse = anchor('<a href="/d/HVMFS%202018-1-ev%20R%C3%A4ttelseblad%20till'
                      '%20tryck.pdf">HVMFS 2018:1 pdf, 120.8 kB.</a>')
    original = anchor('<a href="/d/HVMFS%202018-1-ev%20Ursprunglig.pdf">'
                      'HVMFS 2018:1 pdf, 224.1 kB.</a>')
    assert classify_file(rattelse, *base) == ("attachment", "2018", "1")
    assert classify_file(original, *base) == ("regulation", "2018", "1")


def test_filename_decodes_before_it_splits():
    # a publisher escapes the separator as well as the vowel, so a raw split
    # reads the whole path as the name. 668 of 13,450 stored regulation URLs
    # carry a name that reads differently decoded; 510 of them read a different
    # number through RE_SLUG_NUMBER.
    assert harvest.filename("/d/rafs%2FRA-FS%201997-04.pdf") == "RA-FS 1997-04.pdf"
    assert harvest.filename("/d/TVFS%202025-3.pdf") == "TVFS 2025-3.pdf"
    assert harvest.filename("/d/UPPH%C3%84VD_TRMFS%202017_2.pdf") \
        == "UPPHÄVD_TRMFS 2017_2.pdf"
    # the query goes: it carries digits of its own that a slug pattern reads as
    # a number (SSMFS's ?searchQuery=)
    assert harvest.filename("/d/ssmfs-2018-1.pdf?searchQuery=2021") == "ssmfs-2018-1.pdf"


def test_slug_number_refuses_a_year_inside_an_opaque_id():
    # Integritetsskyddsmyndigheten's links are "/link/<uuid>.aspx". Read
    # mid-string the hex digits mint a document: "f3da2014895c" was IMYFS
    # 2014:895. The name boundary is what refuses it.
    assert harvest.RE_SLUG_NUMBER.search("f3da2014895c.aspx") is None
    assert harvest.RE_SLUG_NUMBER.search("8f3da2014895c4b19c0f.aspx") is None
    # the boundary still admits every shape a real filename uses
    for name, number in (("rgkfs_2015_2.pdf", ("2015", "2")),
                         ("nfs-2014-29.pdf", ("2014", "29")),
                         ("RA-MS 2018-5.pdf", ("2018", "5")),
                         ("x/y/stafs-2022-9.pdf", ("2022", "9"))):
        m = harvest.RE_SLUG_NUMBER.search(name)
        assert m and m.groups() == number, name


def test_classify_keeps_a_regulation_whose_name_says_underrattelse():
    # "rättelse" unanchored also reads "underrättelse", and a regulation
    # classified as a companion is neither fetched nor recorded as a reference.
    # Five stored regulations name one in their file.
    assert classify_href(
        anchor('<a href="/f/hslf-fs-2015-9-foreskrifter-om-underrattelseskyldighet.pdf">'
               'HSLF-FS 2015:9</a>'), "hslffs", "2015", "9") == ("regulation", "2015", "9")
    assert classify_file(
        anchor('<a href="/f/kamfs-2013-4.pdf">Föreskrifter om underrättelse om '
               'tillfällig verksamhet (KAMFS 2013:4)</a>'),
        "kamfs", "2013", "4") == ("regulation", "2013", "4")
    # the correction sheet itself is still a companion, spelled either way
    assert classify_file(anchor('<a href="/f/x.pdf">Rättelseblad FFFS 2017:11</a>'),
                         "fffs", "2017", "11")[0] == "attachment"
    assert classify_file(anchor('<a href="/f/hvmfs-2018-1-ev-rattelseblad.pdf">'
                                'HVMFS 2018:1</a>'), "hvmfs", "2018", "1")[0] == "attachment"


def test_classify_reads_the_other_companion_words():
    # each of these is about the regulation and none is its text
    base = ("fffs", "2026", "1")
    assert classify_file(anchor('<a href="/f/x.pdf">Remissammanställning FFFS 2026:1</a>'),
                         *base)[0] == "memo"
    assert classify_file(anchor('<a href="/f/fffs-2026-1-hjalpdokument.pdf">FFFS 2026:1</a>'),
                         *base)[0] == "attachment"
    assert classify_file(anchor('<a href="/f/x.pdf">Hjälpdokument till FFFS 2026:1</a>'),
                         *base)[0] == "attachment"
    assert classify_file(anchor('<a href="/f/fffs-2026-1-faq.pdf">FFFS 2026:1</a>'),
                         *base)[0] == "attachment"
    # "faq" is anchored on the word: a name that merely contains the letters is
    # the regulation
    assert classify_file(anchor('<a href="/f/fffs-2026-1-faqir.pdf">FFFS 2026:1</a>'),
                         *base) == ("regulation", "2026", "1")


def test_classify_single_is_always_regulation():
    assert classify_single(anchor('<a href="/whatever">x</a>'), "stemfs", "2025", "8") \
        == ("regulation", "2025", "8")


# --- _ref: which number is the regulation's own ----------------------------

def test_ref_prefers_fs_designation_over_sfs_reference():
    seen = set()
    # an SFS reference (2006:1097) in the title must NOT win over the RGKFS number
    ref = _ref(_Agency(fs="rgkfs"),
               "Riksgäldskontorets föreskrifter (RGKFS 2015:2) med stöd av förordning (2006:1097)",
               "/x/rgkfs_2015_2.pdf", seen, direct=True)
    assert ref.basefile == "rgkfs/2015:2"


def test_ref_falls_back_to_filename_when_title_has_no_designation():
    seen = set()
    ref = _ref(_Agency(fs="rgkfs"), "Riksgäldskontorets föreskrifter och allmänna råd",
               "/dok/rgkfs_2006_1.pdf", seen, direct=True)
    assert ref.basefile == "rgkfs/2006:1"


def test_ref_number_from_slug_outranks_a_repeal_target_in_the_text():
    # KKVFS's register row for a repeal document names the *repealed* regulation
    # in its text; the filename is the document's own number
    ref = _ref(_Agency(fs="kkvfs", params={"number_from_slug": True}),
               "Upphävande av Konkurrensverkets allmänna råd (KKVFS 2015:2) om näringsförbud",
               "/globalassets/dokument/om-oss/forfattningssamling/kkvfs_2021-2.pdf",
               set(), direct=True)
    assert ref.basefile == "kkvfs/2021:2"
    assert ref.identifier == "KKVFS 2021:2"


def test_ref_dedupes_by_basefile():
    seen = set()
    a = _ref(_Agency(fs="kifs"), "Gå till KIFS 2017:7", "/kifs-20177", seen)
    b = _ref(_Agency(fs="kifs"), "KIFS 2017:7", "/kifs-20177-dup", seen)
    assert a.basefile == "kifs/2017:7" and b is None


def test_ref_direct_puts_pdf_in_extra():
    ref = _ref(_Agency(fs="lmfs"), "LMFS 2026:3 (pdf)", "/gl/lmfs-2026-3.pdf", set(), direct=True)
    assert ref.extra["regulation_url"] == "https://example.se/gl/lmfs-2026-3.pdf"


def test_ref_fs_from_designation_keeps_inherited_samling_identity():
    # An agency that took over a renamed/disbanded agency's samling (MCF, whose
    # listing mixes new MCFFS with still-in-force MSBFS/SÄIFS) files each document
    # under its own fs, read from the row's printed designation -- not agency.fs.
    seen = set()
    agency = _Agency(fs="mcffs", params={"fs_from_designation": True})
    own = _ref(agency, "MCFFS 2026:13", "/gallande-regler/mcffs-202613/", seen)
    assert own.basefile == "mcffs/2026:13" and own.fs == "mcffs" \
        and own.identifier == "MCFFS 2026:13"
    inherited = _ref(agency, "MSBFS 2020:1", "/gallande-regler/msbfs-20201/", seen)
    assert inherited.basefile == "msbfs/2020:1" and inherited.fs == "msbfs" \
        and inherited.identifier == "MSBFS 2020:1"
    # the hyphenated HSLF-FS designation collapses to a separator-free fs code
    hslf = _ref(agency, "HSLF-FS 2019:4", "/gallande-regler/hslf-fs-20194/", seen)
    assert hslf.basefile == "hslffs/2019:4" and hslf.identifier == "HSLF-FS 2019:4"


def test_ref_fs_from_designation_preserves_mixed_case_designation():
    # the printed designation is kept verbatim (never upper()'d), so a mixed-case
    # series keeps its identity in the identifier while its fs code lowercases:
    # SiS's SiSFS and the SiSUVFS (ungdomsvård) series it mixes on one page.
    seen = set()
    agency = _Agency(fs="sisfs", designation="SiSFS", params={"fs_from_designation": True})
    own = _ref(agency, "SiSFS 2025:1", "/x/sisfs-2025-1.pdf", seen, direct=True)
    assert own.basefile == "sisfs/2025:1" and own.identifier == "SiSFS 2025:1"
    uv = _ref(agency, "SiSUVFS 2025:1", "/x/sisuvfs-2025-1.pdf", seen, direct=True)
    assert uv.basefile == "sisuvfs/2025:1" and uv.fs == "sisuvfs" \
        and uv.identifier == "SiSUVFS 2025:1"


def test_ref_files_every_row_under_its_own_printed_designation():
    # the printed designation decides the samling for every agency, with no
    # opt-in: a row printing the agency's own series is unaffected, and one
    # printing a predecessor's keeps that predecessor's identity rather than
    # being published under a designation the agency never used ("NFS 1987:12"
    # for a document whose own file is snfs1987-12.pdf)
    seen = set()
    ref = _ref(_Agency(fs="kifs"), "KIFS 2017:7", "/kifs-20177", seen)
    assert ref.basefile == "kifs/2017:7" and ref.fs == "kifs"
    old = _ref(_Agency(fs="nfs"), "SNFS 1987:12 Föreskrifter om Pieljekaise",
               "/nfs/1970-89/snfs1987-12.pdf", seen)
    assert old.basefile == "snfs/1987:12" and old.fs == "snfs" \
        and old.identifier == "SNFS 1987:12"


def test_ref_reads_the_row_designation_the_row_claims_as_its_own():
    # a row names more than one document: an amendment's names the base it
    # amends, a repeal's what it repeals, an omtryck's the base it reprints.
    # Taking the leftmost filed four scopes' documents under another
    # document's number.
    seen = set()
    agency = _Agency(fs="memyfs")
    r = _ref(agency, "Föreskrifter om upphävande av MPRTFS 2019:3 om "
             "mediestöd (MPRTFS 2021:1)", "/x/a.pdf", seen, direct=True)
    assert r.basefile == "mprtfs/2021:1"
    # nothing but the base's number: the filename carries the document's own
    r = _ref(agency, "Föreskrifter om ändring i Mediemyndighetens föreskrifter "
             "(MEMYFS 2024:1) om mediestöd", "/x/memyfs-2024-2.pdf", seen, direct=True)
    assert r.basefile == "memyfs/2024:2"
    # an omtryck prints the base first and itself last
    r = _ref(_Agency(fs="tvfs"), "TVFS 2015:1 omtryckt genom Tillväxtverkets "
             "föreskrifter om stöd TVFS 2019:1", "/x/b.pdf", seen, direct=True)
    assert r.basefile == "tvfs/2019:1"


def test_ref_drops_the_samlings_own_forteckning():
    # the catalogue 18 c § författningssamlingsförordningen has an agency
    # publish is not a document in the samling, and its filename's date reads
    # as a number (Konsumentverket's became the document "KOVFS 2021:1")
    assert _ref(_Agency(fs="kovfs"), "Förteckning över gällande föreskrifter",
                "/x/forteckning-2021-01.pdf", set(), direct=True) is None


def test_ref_drops_the_publication_list_under_the_agencys_own_name():
    # each agency names that catalogue its own way; Konsumentverket calls it
    # "Samtliga publikationer i Konsumentverkets författningssamling (KOVFS)",
    # and its filename's date minted the document "KOVFS 2021:1", hiding the
    # real one
    assert _ref(_Agency(fs="kovfs"),
                "Samtliga publikationer i Konsumentverkets författningssamling (KOVFS)",
                "/downloads/kovfs-alla-publikationer-2021-01-konsumentverket.pdf",
                set(), direct=True) is None


def test_ref_needs_a_year_before_it_reads_a_number_off_a_slug():
    # Integritetsskyddsmyndigheten's links are "/link/<uuid>.aspx", whose hex
    # digits minted the document "IMYFS 0008:2"
    assert _ref(_Agency(fs="imyfs"), "Allmänna råd",
                "/link/f3da97d00082.aspx", set(), direct=True) is None


# --- direct_docref / newest_first: the shared tail of a bespoke enumerator ----

def test_direct_docref_builds_deduped_direct_ref():
    # the shared tail the filename-slug enumerators (skogs/prvfs/csnfs/…) use:
    # deduped basefile + the resolve_direct extra payload, number parsed by caller
    agency = _Agency(fs="sksfs")
    seen = set()
    r = harvest.direct_docref(agency, "sksfs", "2015", "4",
                              "https://example.se/x/sksfs-2015-4.pdf", seen, title="t")
    assert r.basefile == "sksfs/2015:4" and r.fs is None \
        and r.identifier == "SKSFS 2015:4" \
        and r.extra["regulation_url"] == "https://example.se/x/sksfs-2015-4.pdf"
    # a second sighting of the same base dedupes to None
    assert harvest.direct_docref(agency, "sksfs", "2015", "4", "u2", seen) is None
    # a routed predecessor series keeps its own DocRef.fs + explicit identifier
    routed = harvest.direct_docref(agency, "rsfs", "1999", "1", "u3", seen,
                                   identifier="RSFS 1999:1")
    assert routed.fs == "rsfs" and routed.identifier == "RSFS 1999:1"


def test_newest_first_orders_numerically_not_lexically():
    # 2026:12 must sort ahead of 2026:3 (the incremental watermark needs a true
    # newest-first stream, so the lopnummer compares as an int, not a string)
    refs = [DocRef("x/2019:5", "X 2019:5", "u"), DocRef("x/2026:3", "X 2026:3", "u"),
            DocRef("x/2026:12", "X 2026:12", "u")]
    assert [r.basefile for r in harvest.newest_first(refs)] \
        == ["x/2026:12", "x/2026:3", "x/2019:5"]


# --- enumeration resilience: a flaky index must not abort the run -----------

def test_guarded_enumerate_turns_a_blowup_into_a_skip():
    """A single-call enumerator (an API/index that dies outright) must end the
    walk with one Skip, not propagate and abort the whole 15-agency run."""
    def boom():
        raise ValueError("index endpoint down")
        yield  # pragma: no cover -- makes boom a generator
    out = list(guarded_enumerate(boom(), lambda *a: None))
    assert len(out) == 1 and isinstance(out[0], Skip)


def test_guarded_enumerate_passes_skips_and_docs_through():
    """A multi-page enumerator that yields a Skip for one bad page keeps
    yielding the documents it can still reach (the tail is preserved)."""
    def mixed():
        yield DocRef("x/2024:1", "X 2024:1", "u1")
        yield Skip("page 2 down")
        yield DocRef("x/2022:3", "X 2022:3", "u2")
    out = list(guarded_enumerate(mixed(), lambda *a: None))
    assert [type(o).__name__ for o in out] == ["DocRef", "Skip", "DocRef"]


def test_sjvfs_enumerate_files_rows_by_printed_series_and_keeps_the_document_proper(monkeypatch):
    """Six rows of the live register (two proxy pages, captured 2026-09-12):
    a bare-numbered base (SJVFS 2023:21); SJVFS 2025:17 twice -- the "Aktuell"
    row is a rättelseblad, the "Historik" row the document, whose summary opens
    with the masthead; LSFS 1980:8 and its bare-numbered amendment 1986:18,
    which inherits LSFS; DFS 2004:5. Nothing is read from the status tags."""
    pages = json.loads(
        (Path(__file__).parent / "files/foreskrift/sjvfs-register.json").read_text())

    class Response:
        def __init__(self, data=None):
            self.data = data

        def json(self):
            return self.data

    def request(_session, method, _url, **_kwargs):
        return Response() if method == "GET" else Response(pages.pop(0))

    monkeypatch.setattr("ferenda.foreskrift.agencies.request", request)
    monkeypatch.setattr("ferenda.foreskrift.agencies.time.sleep", lambda _seconds: None)
    refs = list(SJVFS.enumerate(None, SJVFS))
    assert [(r.basefile, r.identifier, r.fs) for r in refs] == [
        ("sjvfs/2025:17", "SJVFS 2025:17", None),
        ("sjvfs/2023:21", "SJVFS 2023:21", None),
        ("dfs/2004:5", "DFS 2004:5", "dfs"),
        ("lsfs/1986:18", "LSFS 1986:18", "lsfs"),
        ("lsfs/1980:8", "LSFS 1980:8", "lsfs")]
    assert refs[0].url.endswith("dCa5YZ6Wg2Hnv9Hlqx0?download=1")     # the Historik original
    assert refs[0].extra["regulation_url"] == refs[0].url
    assert "status" not in refs[0].extra
    assert refs[4].title.startswith("Lantbruksstyrelsens kungörelse")


def test_sjvfs_designation_by_field_base_row_or_year():
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "2023:21"}) == ("SJVFS", "2023", "21")
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "LSFS 1980:8", "Ändringsföreskriftnr": "1986:18"}) \
        == ("LSFS", "1986", "18")
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "1988:3"}) == ("LSFS", "1988", "3")
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "DFS 2004:5"}) == ("DFS", "2004", "5")
    # a 2009 amendment of a DFS base is SJVFS: DFS ended in 2007
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "DFS 2004:22", "Ändringsföreskriftnr": "2009:19"}) \
        == ("SJVFS", "2009", "19")
    # the register drops the designation on some DFS rows; the title's agency says
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "2004:19"},
                                      "Djurskyddsmyndighetens föreskrifter om djurhållning i djurparker") \
        == ("DFS", "2004", "19")
    assert agencies.sjvfs_designation({"Grundföreskriftnr": "2004:19"},
                                      "Statens jordbruksverks föreskrifter om något") == ("SJVFS", "2004", "19")
    assert agencies.sjvfs_designation({}) is None


def test_reap_refiles_a_record_that_predates_its_series(tmp_path):
    """A legacy import filed LSFS 1986:18 as sjvfs/1986:18; SJVFS began in 1991.
    Once the register harvest files it under lsfs, the sjvfs record is the
    leftover."""
    for fs in ("sjvfs", "lsfs"):
        harvest.write_record(record_path(tmp_path, fs, "%s/1986:18" % fs), {
            "fs": fs, "basefile": "%s/1986:18" % fs, "url": "https://e/%s" % fs,
            "files": {"regulation": {"name": "r.pdf", "url": "https://e/%s.pdf" % fs}}})
    assert download.superseded(tmp_path) == {"sjvfs/1986:18": ("lsfs/1986:18", "")}


def test_livsfs_index_reads_the_pdf_from_each_rows_first_cell(monkeypatch):
    """Livsmedelsverket's year tables link the PDF from the first cell and put
    the register's status text, with its own cross-reference link, in the
    second (#32). Three rows of the live 2011 page; LIVSFS 2011:13 is the one
    the old ``p.related-info`` selector lost."""
    html = (Path(__file__).parent / "files/foreskrift/livsfs-2011.html").read_text()
    monkeypatch.setattr(harvest, "request", lambda *_args, **_kwargs:
                        SimpleNamespace(text=html))
    monkeypatch.setattr(harvest.time, "sleep", lambda _seconds: None)
    agency = replace(REGISTRY["livsfs"],
                     params={**REGISTRY["livsfs"].params,
                             "index_urls": ["https://example.se/2011"]})
    refs = list(harvest.indexed_enumerate(None, agency))
    assert [r.basefile for r in refs] == [
        "livsfs/2011:13", "livsfs/2011:16", "livsfs/2011:19"]
    assert refs[0].extra["regulation_url"].endswith("/livsfs-2011-13.pdf")


def test_livsfs_files_the_predecessor_series_under_slvfs():
    """Livsmedelsverket's 1996-2001 year pages list the predecessor series:
    rows read "SLVFS 1998:12" and link slvfs-1998-12.pdf. They file under slvfs,
    the series later documents repeal them by; a bare "2002:12" row stays livsfs."""
    agency = REGISTRY["livsfs"]
    old = _ref(agency, "SLVFS 1998:12", "/globalassets/slvfs-1998-12.pdf", set(), direct=True)
    assert (old.basefile, old.fs, old.identifier) == ("slvfs/1998:12", "slvfs", "SLVFS 1998:12")
    new = _ref(agency, "2002:12", "/globalassets/livsfs-2002-12.pdf", set(), direct=True)
    assert (new.basefile, new.fs, new.identifier) == ("livsfs/2002:12", None, "LIVSFS 2002:12")
    assert REGISTRY["slvfs"].designation == "SLVFS"


def test_livsfs_landing_classifies_by_section_and_keeps_each_files_series():
    """The <main> block of LIVSFS 2008:13's landing page, as captured: the
    konsoliderad version under "Författningen med ändringar införda", the
    printed base under "Grundförfattningen", two amendments under "Senare
    ändringar". The consolidation's own filename names the amendment first
    (livsfs-2022-3-kons-2008-13.pdf), so the role comes from the heading."""
    html = (Path(__file__).parent / "files/foreskrift/livsfs-landing-2008-13.html").read_text()
    soup = BeautifulSoup(html, "html.parser")
    roles = [agencies.classify_livsfs(a, "livsfs", "2008", "13")
             for a in soup.select('a[href$=".pdf"]')]
    assert roles == [("consolidation", "2008", "13"), ("regulation", "2008", "13"),
                     ("amendment", "2013", "5"), ("amendment", "2022", "3")]


def test_livsfs_resolve_falls_back_to_the_index_pdf_without_a_landing_page(monkeypatch):
    """An amendment, or a repealed base, has no gallande-lagstiftning page (404):
    the index PDF is the document. A base in force resolves its landing page."""
    calls = []
    def head(session, method, url, **_kw):
        calls.append((method, url))
        if "20186" in url:
            resp = requests.Response(); resp.status_code = 404
            raise requests.exceptions.HTTPError(response=resp)
        return SimpleNamespace(text="")
    monkeypatch.setattr(agencies, "request", head)
    monkeypatch.setattr(agencies, "resolve_direct", lambda *a, **kw: "direct")
    monkeypatch.setattr(agencies, "resolve_landing",
                        lambda session, agency, ref, *a, **kw: ("landing", ref.url))
    agency = REGISTRY["livsfs"]
    amendment = DocRef(basefile="livsfs/2018:6", identifier="LIVSFS 2018:6", url="https://e/a.pdf")
    base = DocRef(basefile="livsfs/2014:4", identifier="LIVSFS 2014:4", url="https://e/b.pdf")
    assert agencies.livsfs_resolve(None, agency, amendment, "/r") == "direct"
    assert agencies.livsfs_resolve(None, agency, base, "/r") == \
        ("landing", "https://www.livsmedelsverket.se/om-oss/lagstiftning1/gallande-lagstiftning/livsfs-20144/")
    assert calls[0][0] == "HEAD"


def test_reap_finds_a_direct_series_leftover_by_its_regulation_pdf(tmp_path):
    """LIVSFS files every record under the one year index as its url, so the
    page url corroborates nothing; the regulation PDF's url is what the
    pre-split livsfs/1998:8 and the re-filed slvfs/1998:8 share, and its
    filename (slvfs-1998-8.pdf) names the samling that issued it."""
    index = "https://www.livsmedelsverket.se/om-oss/lagstiftning1/foreskrifter-i-nummerordning/"
    pdf = "https://www.livsmedelsverket.se/globalassets/lakemedelsrester/slvfs-1998-8.pdf"
    for fs in ("livsfs", "slvfs"):
        harvest.write_record(record_path(tmp_path, fs, "%s/1998:8" % fs), {
            "fs": fs, "basefile": "%s/1998:8" % fs, "url": index,
            "files": {"regulation": {"name": "%s-1998-8-regulation.pdf" % fs, "url": pdf}}})
    assert download.superseded(tmp_path) == {"livsfs/1998:8": ("slvfs/1998:8", pdf)}


def test_reap_prefers_the_newer_record_over_the_pdf_filename(tmp_path):
    """Livsmedelsverket names SLVFS 2000:3's file livsfs-2003-3-andr-1996-32.pdf;
    the filename is no evidence of the samling. The record the later run wrote
    is the re-filing."""
    index = "https://www.livsmedelsverket.se/om-oss/lagstiftning1/foreskrifter-i-nummerordning/"
    pdf = "https://www.livsmedelsverket.se/globalassets/livsfs-2003-3-andr-1996-32.pdf"
    for fs, age in (("livsfs", 200), ("slvfs", 100)):
        path = record_path(tmp_path, fs, "%s/2000:3" % fs)
        harvest.write_record(path, {"fs": fs, "basefile": "%s/2000:3" % fs, "url": index,
                                    "files": {"regulation": {"name": "x.pdf", "url": pdf}}})
        os.utime(path, ns=(10**9 * (10**6 - age), 10**9 * (10**6 - age)))
    assert download.superseded(tmp_path) == {"livsfs/2000:3": ("slvfs/2000:3", pdf)}


def test_reap_drops_an_empty_record_beside_its_lineage_twin(tmp_path):
    """A row whose first cell links another document's landing page fetched
    nothing under livsfs/1997:27; the re-filed slvfs/1997:27 holds the
    document. LIVSFS succeeded SLVFS, so the empty one is the leftover."""
    harvest.write_record(record_path(tmp_path, "livsfs", "livsfs/1997:27"), {
        "fs": "livsfs", "basefile": "livsfs/1997:27", "url": "https://e/index/",
        "files": {"regulation": None, "consolidation": [], "amendment": []}})
    harvest.write_record(record_path(tmp_path, "slvfs", "slvfs/1997:27"), {
        "fs": "slvfs", "basefile": "slvfs/1997:27", "url": "https://e/gallande/slvfs-199727/",
        "files": {"regulation": {"name": "r.pdf", "url": "https://e/slvfs-1997-27.pdf"}}})
    assert download.superseded(tmp_path) == {"livsfs/1997:27": ("slvfs/1997:27", "")}


def test_browser_agency_selects_the_camoufox_transport_only(tmp_path, monkeypatch):
    selected = {}

    class Browser:
        def __init__(self, profile, pace):
            selected.update(profile=profile, pace=pace)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    agency = harvest.Agency(
        fs="skvfs", name="SKV", publisher="Skatteverket",
        base_url="https://example.se", index_url="https://example.se/list",
        enumerate=lambda *_args: (), resolve=lambda *_args: None,
        browser=True, browser_pace=23,
    )
    monkeypatch.setattr(harvest, "CamoufoxBrowser", Browser)
    monkeypatch.setattr(
        harvest, "_harvest_session",
        lambda selected_agency, _root, session, *_args: (selected_agency.fs, session),
    )

    fs, session = harvest.harvest(agency, tmp_path)
    assert fs == "skvfs" and isinstance(session, Browser)
    assert selected == {
        "profile": tmp_path / "skvfs" / ".browser-profile",
        "pace": 23,
    }


# --- paginated_enumerate: the archive pages under the listing's own parameter -

def _paginated_agency(page_url):
    return harvest.Agency(
        fs="mcffs", name="MCF", publisher="MCF", base_url="https://e",
        index_url="https://e/list", enumerate=harvest.paginated_enumerate,
        params={"page_url": page_url, "row_select": "a.row"})


def test_paginated_enumerate_pages_the_archive_with_the_listings_own_parameter(
        monkeypatch):
    # MCF pages with "selectedpage"; "?page=2" gives it the rows of page 1, so
    # the archive walk never ended and wrote nothing over two --force runs
    asked = []

    def fake_request(_session, _method, url, **_kw):
        asked.append(url)
        if "selectedpage=1" not in url:
            body = ""                                  # past the last page
        elif "upphavda" in url:
            body = '<a class="row" href="/r/msbfs-20181/">MSBFS 2018:1</a>'
        else:
            body = ('<a href="/upphavda/">Upphävda regler</a>'
                    '<a class="row" href="/r/mcffs-20261/">MCFFS 2026:1</a>')
        return SimpleNamespace(text=body)

    monkeypatch.setattr(harvest, "request", fake_request)
    monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
    agency = _paginated_agency("https://e/gallande/?sortOrder=Desc&selectedpage={page}")
    refs = list(harvest.paginated_enumerate(None, agency))
    assert [r.basefile for r in refs] == ["mcffs/2026:1", "msbfs/2018:1"]
    assert "https://e/upphavda/?selectedpage=1" in asked


def test_paginated_enumerate_ends_where_a_page_names_no_new_row(monkeypatch):
    # a listing that ignores its page parameter serves page 1 forever, and a
    # view whose rows have run out serves its last page again -- the same
    # answer, which is why `lib.harvest.paginated` reads both as the end. The
    # walk keeps page 1's rows and asks for one page more, never for a third.
    asked = []

    def fake_request(_session, _method, url, **_kw):
        asked.append(url)
        return SimpleNamespace(
            text='<a class="row" href="/r/mcffs-20261/">MCFFS 2026:1</a>')

    monkeypatch.setattr(harvest, "request", fake_request)
    monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
    agency = _paginated_agency("https://e/gallande/?page={page}")
    agency.params["no_archive"] = True
    out = list(harvest.paginated_enumerate(None, agency))
    assert [r.basefile for r in out] == ["mcffs/2026:1"]
    assert asked == ["https://e/gallande/?page=1", "https://e/gallande/?page=2"]


def test_paginated_enumerate_raises_on_a_pager_that_never_terminates(monkeypatch):
    # every page names rows nobody has seen: that is a changed site, not a
    # large samling, and an uncapped walk over it is a hang. The cap raises
    # rather than asserts -- under -O an assert would let the walk run on
    # (rule:errors-drive-retry-use-raise).
    def fake_request(_session, _method, url, **_kw):
        page = url.rsplit("=", 1)[1]
        return SimpleNamespace(
            text='<a class="row" href="/r/mcffs-2026%s/">MCFFS 2026:%s</a>'
                 % (page, page))

    monkeypatch.setattr(harvest, "request", fake_request)
    monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
    agency = _paginated_agency("https://e/gallande/?page={page}")
    agency.params["no_archive"] = True
    try:
        list(harvest.paginated_enumerate(None, agency))
    except ValueError as exc:
        assert "no longer terminates" in str(exc)
    else:
        raise AssertionError("an endless pager must stop the walk")


def test_paginated_enumerate_needs_a_named_page_parameter():
    # a page_url with no {page} would page the archive at the bare listing url
    agency = _paginated_agency("https://e/gallande/")
    try:
        list(harvest.paginated_enumerate(None, agency))
    except AssertionError as exc:
        assert "names no {page} parameter" in str(exc)
    else:
        raise AssertionError("a page_url without {page} must not pass")


# --- magic-sniff: a non-PDF body is logged + counted, never silently dropped -

def _agency_fffs():
    return harvest.Agency(fs="fffs", name="FI", publisher="Finansinspektionen",
                          base_url="https://e", index_url="https://e/list")


def test_resolve_landing_rejects_and_counts_non_pdf(tmp_path, monkeypatch):
    # a WAF/error page served 200 for a link the classifier kept must be rejected
    # by a magic-byte sniff, logged and counted -- not stored while the record is
    # still written (which used to mask the doc with zero trace).
    class Resp:
        text = '<a href="/x/fffs-2013-10.pdf">FFFS 2013:10</a>'
        content = b"<html>WAF challenge -- not a PDF</html>"
    monkeypatch.setattr(harvest, "request", lambda *a, **kw: Resp())
    ref = DocRef(basefile="fffs/2013:10", identifier="FFFS 2013:10",
                 url="https://e/landing")
    logs, rejects = [], []
    record = harvest.resolve_landing(None, _agency_fffs(), ref, str(tmp_path),
                                     delay=0, log=logs.append, rejects=rejects)
    assert record["files"]["regulation"] is None      # nothing stored as the PDF
    assert len(rejects) == 1
    assert any("non-PDF" in m for m in logs)
    assert not (tmp_path / "fffs" / "fffs-2013-10-regulation.pdf").exists()


class _PdfResp:
    """A landing page whose every PDF link serves a real PDF body."""
    content = b"%PDF-1.4 body"

    def __init__(self, text):
        self.text = text


def test_resolve_landing_lets_a_second_anchor_classify_the_same_href(
        tmp_path, monkeypatch):
    # Havs- och vattenmyndigheten hangs one PDF under two anchors: a generic
    # "Ursprunglig utgåva" first and the one naming the document second.
    # Marking the href seen on the anchor the classifier rejected left seven
    # regulations unfetched.
    page = ('<a href="/d/HVMFS-2017-20-ev.pdf">Ursprunglig utgåva pdf, 1.2 MB.</a>'
            '<a href="/d/HVMFS-2017-20-ev.pdf">HVMFS 2017:20 pdf, 1.2 MB.</a>')
    monkeypatch.setattr(harvest, "request", lambda *a, **kw: _PdfResp(page))
    monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
    agency = harvest.Agency(fs="hvmfs", name="HaV", publisher="HaV",
                            base_url="https://e", index_url="https://e/list",
                            params={"pdf_select": 'a[href*="/d/"]'})
    ref = DocRef(basefile="hvmfs/2017:20", identifier="HVMFS 2017:20",
                 url="https://e/landing")
    record = harvest.resolve_landing(None, agency, ref, str(tmp_path), delay=0)
    assert record["files"]["regulation"]["url"] == "https://e/d/HVMFS-2017-20-ev.pdf"


def test_resolve_landing_keeps_the_first_regulation_and_files_the_rest(
        tmp_path, monkeypatch):
    # SCB hangs the föreskrift first and its bilagor after it, each of which
    # classify_single reads as another regulation. Overwriting the slot stored
    # the last bilaga as the law.
    page = ('<a href="/d/scb-fs-2016-7.pdf">Statistiska centralbyråns föreskrifter</a>'
            '<a href="/d/scb-fs-2016-7-variabelforteckning.pdf">Variabelförteckning</a>')
    monkeypatch.setattr(harvest, "request", lambda *a, **kw: _PdfResp(page))
    monkeypatch.setattr(harvest.time, "sleep", lambda _s: None)
    agency = harvest.Agency(fs="scbfs", name="SCB", publisher="SCB",
                            base_url="https://e", index_url="https://e/list",
                            designation="SCB-FS",
                            params={"pdf_select": 'a[href*="/d/"]',
                                    "classify": classify_single})
    ref = DocRef(basefile="scbfs/2016:7", identifier="SCB-FS 2016:7",
                 url="https://e/landing")
    record = harvest.resolve_landing(None, agency, ref, str(tmp_path), delay=0)
    assert record["files"]["regulation"]["url"] == "https://e/d/scb-fs-2016-7.pdf"
    assert [e["url"] for e in record["files"]["attachment"]] == \
        ["https://e/d/scb-fs-2016-7-variabelforteckning.pdf"]


def test_resolve_direct_rejects_and_counts_non_pdf(tmp_path, monkeypatch):
    class Resp:
        content = b"<html>error page</html>"
    monkeypatch.setattr(harvest, "request", lambda *a, **kw: Resp())
    ref = DocRef(basefile="bfs/2026:1", identifier="BFS 2026:1", url="https://e/x",
                 extra={"regulation_url": "https://e/x.pdf", "title": "t"})
    logs, rejects = [], []
    record = harvest.resolve_direct(None, _agency_fffs(), ref, str(tmp_path),
                                    delay=0, log=logs.append, rejects=rejects)
    assert record["files"]["regulation"] is None
    assert len(rejects) == 1 and any("non-PDF" in m for m in logs)


# --- extract_publisher: the issuing agency from the PDF masthead --------------
# Inputs are real (whitespace-collapsed) masthead openings; the extractor is what
# lets an inherited SÄIFS/SRVFS number keep its own defunct issuer rather than the
# current custodian. Applies to every myndighetsföreskrift source (one parser).

def test_publisher_from_utgivare_drops_the_named_individual():
    # 'Utgivare: <person>, <agency>' -> the agency, never the person
    mast = ("Statens räddningsverks författningssamling Utgivare: Key Hedström, "
            "Statens räddningsverk ISSN 0283-6165 SRVFS 2004:3")
    assert extract_publisher(mast) == "Statens räddningsverk"


def test_publisher_utgivare_without_agency_falls_back_to_series_title():
    # extraction often drops the agency after the person ('Anna Asp ISSN … MCFFS
    # 2026:2'); the '<agency>s författningssamling' title then supplies it
    mast = ("Myndigheten för civilt försvars författningssamling Utgivare: Anna Asp "
            "ISSN 2000-1886 MCFFS 2026:2 Utkom från trycket den 19 januari 2026")
    assert extract_publisher(mast) == "Myndigheten för civilt försvar"


def test_publisher_series_title_optional_genitive_and_capital_f():
    # an older masthead prints 'Krisberedskapsmyndigheten Författningssamling'
    # (no genitive -s, capital F) -- still the agency
    mast = ("Krisberedskapsmyndigheten Författningssamling Utgivare: Maria Broms "
            "Hagelin SN 165 587 ISSN 1651-5587 KBMFS Krisberedskapsmyndighetens "
            "föreskrifter 2008:1")
    assert extract_publisher(mast) == "Krisberedskapsmyndigheten"


def test_publisher_does_not_bleed_into_preceding_heading_words():
    # a cover-page heading of Capitalised words before the title must not be
    # swept into the agency (the continuation is lowercase-only)
    mast = ("Skyltning Överlåtelse Transport Sprängämnesinspektionens "
            "författningssamling Sprängämnesinspektionens föreskrifter om")
    assert extract_publisher(mast) == "Sprängämnesinspektionen"


def test_publisher_does_not_run_past_the_agency_into_the_next_words():
    # the Utgivare agency stops at the next Capitalised token ('Allmänna'), not
    # swallowing it
    mast = ("Utgivare: Key Hedström, Myndigheten för samhällsskydd och beredskap "
            "Allmänna råd ISSN 2000-1886")
    assert extract_publisher(mast) == "Myndigheten för samhällsskydd och beredskap"


def test_publisher_falls_back_to_foreskrift_name_when_no_series_line():
    # no 'Utgivare:' and no 'författningssamling' -> the possessive prefix of the
    # föreskrift's own name
    mast = "Naturvårdsverkets föreskrifter (NFS 2020:5) om buller"
    assert extract_publisher(mast) == "Naturvårdsverket"


def test_publisher_prose_allmanna_rad_is_not_a_possessive_agency():
    # a lowercase prose 'följande allmänna råd' is not a title; nothing is claimed
    mast = ("Räddningsverket meddelar härmed följande allmänna råd för "
            "tillämpningen av ovannämnda föreskrifter.")
    assert extract_publisher(mast) is None


def test_closed_series_agencies_registered_without_a_live_harvester():
    # RSFS/SOSFS are closed series: registered (their documents live in the
    # corpus) but with no live enumerate/resolve, so a harvest skips them.
    # HSLF-FS is not one of them any more -- see test_foreskrift_hslffs.py.
    for fs, designation in (("rsfs", "RSFS"), ("sosfs", "SOSFS")):
        assert fs in REGISTRY
        assert REGISTRY[fs].enumerate is None and REGISTRY[fs].resolve is None
        assert REGISTRY[fs].designation == designation


# --- the archive of repealed regulations the listing links -------------------

ARCHIVE_INDEX = """
<ul>
  <li><a href="/regler/mcffs-2026-1/">MCFFS 2026:1 om ledningssystem</a></li>
  <li><a href="/regler/upphavda-regler/">Upphävda regler</a></li>
  <li><a href="https://someone.else/upphavda/">Upphävda hos annan</a></li>
</ul>"""

ARCHIVE_PAGE = """
<ul><li><a href="/regler/srvfs-2004-3/">SRVFS 2004:3 om skriftlig redogörelse</a></li></ul>"""


def test_archive_links_are_the_agencys_own_listing_of_repealed_regulations():
    # thirteen scopes publish their repealed regulations behind a link like
    # this one and never enumerated them, so the documents that repealed them
    # were missing too. Another host's link is not this agency's archive.
    agency = _Agency(fs="mcffs", base_url="https://www.mcf.se")
    soup = BeautifulSoup(ARCHIVE_INDEX, "html.parser")
    assert harvest.archive_links(soup, agency) == [
        "https://www.mcf.se/regler/upphavda-regler/"]
    opted_out = replace(agency, params={"no_archive": True})
    assert harvest.archive_links(soup, opted_out) == []


def test_indexed_enumerate_follows_the_archive_once(monkeypatch):
    pages = {"https://www.mcf.se/regler/": ARCHIVE_INDEX,
             "https://www.mcf.se/regler/upphavda-regler/": ARCHIVE_PAGE}
    asked = []

    def fake_request(_session, _method, url, **_kwargs):
        asked.append(url)
        return SimpleNamespace(text=pages[url])

    monkeypatch.setattr(harvest, "request", fake_request)
    monkeypatch.setattr(harvest.time, "sleep", lambda _seconds: None)
    agency = _Agency(fs="mcffs", base_url="https://www.mcf.se",
                     index_url="https://www.mcf.se/regler/",
                     params={"link_select": "li a"})
    refs = list(harvest.indexed_enumerate(None, agency))
    # the repealed SRVFS regulation is reached, and keeps its own samling
    assert [r.basefile for r in refs] == ["mcffs/2026:1", "srvfs/2004:3"]
    # the archive is followed from the listing only, never from itself
    assert asked == ["https://www.mcf.se/regler/",
                     "https://www.mcf.se/regler/upphavda-regler/"]


def test_paginated_enumerate_walks_the_archive_it_finds(monkeypatch):
    # MCF's "gällande regler" is ten documents and its "upphävda regler" 177,
    # each paged the same way
    index = ('<div><a class="r" href="/x/mcffs-2026-1/">MCFFS 2026:1 om x</a>'
             '<a href="/upphavda/">Upphävda regler</a></div>')
    pages = {"https://www.mcf.se/regler/?page=1": index,
             "https://www.mcf.se/regler/?page=2": "<div></div>",
             "https://www.mcf.se/upphavda/?page=1":
                 '<div><a class="r" href="/x/srvfs-2004-3/">SRVFS 2004:3 om y</a></div>',
             "https://www.mcf.se/upphavda/?page=2": "<div></div>"}
    monkeypatch.setattr(harvest, "request",
                        lambda _s, _m, url, **_kw: SimpleNamespace(text=pages[url]))
    monkeypatch.setattr(harvest.time, "sleep", lambda _seconds: None)
    agency = _Agency(fs="mcffs", base_url="https://www.mcf.se",
                     index_url="https://www.mcf.se/regler/",
                     params={"page_url": "https://www.mcf.se/regler/?page={page}",
                             "row_select": "a.r"})
    refs = list(harvest.paginated_enumerate(None, agency))
    assert [r.basefile for r in refs] == ["mcffs/2026:1", "srvfs/2004:3"]


def test_ref_files_a_designation_under_its_registered_slug_not_its_spelling():
    # four samlingar carry a Swedish vowel their slug transliterates. Reading
    # the slug straight off the printed designation filed 51 Elsäkerhetsverket
    # documents under an "elsäkfs" no registry knows, beside the 37 already
    # held under elsakfs.
    seen = set()
    r = _ref(_Agency(fs="elsakfs"), "ELSÄK-FS 2008:1 om elektriska anläggningar",
             "/x/elsak-fs-2008-1.pdf", seen, direct=True)
    assert r.basefile == "elsakfs/2008:1" and r.fs == "elsakfs"
    # ÅFS is Åklagarmyndighetens; afs is Arbetsmiljöverkets
    r = _ref(_Agency(fs="aafs"), "ÅFS 2021:3 om förundersökning", "/x/afs-2021-3.pdf",
             seen, direct=True)
    assert r.basefile == "aafs/2021:3"


def test_every_registered_samling_cites_itself_the_way_it_is_printed():
    # `printed_designation` falls back to the slug in capitals, which is right
    # for the 40-odd samlingar whose slug is their designation and wrong for
    # the ones carrying a Swedish vowel: ELSÄK-FS was cited as "ELSAKFS".
    from ferenda.foreskrift.model import printed_designation
    from ferenda.lib import datasets
    for fs, row in datasets.load_fs_series().items():
        designation = row.get("designation")
        if not designation:
            continue
        printed = printed_designation("https://lagen.nu/%s/2020:1" % fs)
        assert printed == "%s 2020:1" % designation, \
            "%s is cited as %r, not as the %r it prints" % (fs, printed, designation)


# --- bespoke enumerators: the archive, and each row's own designation -------

def _pages(monkeypatch, pages, asked=None):
    """Serve `pages` (url -> HTML) to both modules, with no delays. The listing
    walk fetches from `harvest`; an agency that reads a JSON API still fetches
    from `agencies`."""
    def fake_request(_session, _method, url, **_kwargs):
        if asked is not None:
            asked.append(url)
        return SimpleNamespace(text=pages[url])

    monkeypatch.setattr(agencies, "request", fake_request)
    monkeypatch.setattr(harvest, "request", fake_request)
    monkeypatch.setattr(harvest.time, "sleep", lambda _seconds: None)


def test_index_soups_reads_the_listing_and_the_archive_it_links(monkeypatch):
    # the listing an agency calls "gällande föreskrifter" is not the samling.
    # Seven bespoke enumerators read only it, so ~800 repealed regulations --
    # and the documents that repealed them -- were never fetched.
    asked = []
    _pages(monkeypatch, {"https://x.se/list": '<a href="/old/">Upphävda föreskrifter</a>',
                         "https://x.se/old/": "<p>old</p>"}, asked)
    agency = _Agency(base_url="https://x.se", index_url="https://x.se/list")
    pages = list(harvest.index_soups(None, agency))
    assert len(pages) == 2
    assert asked == ["https://x.se/list", "https://x.se/old/"]
    # the archive is followed from the listing only, never from itself
    assert pages[1][1].get_text(strip=True) == "old"


def test_index_soups_makes_a_skip_of_an_archive_page_that_will_not_fetch(monkeypatch):
    # a 404 on the archive used to abort the agency mid-enumeration: the rows
    # of the in-force listing were already enumerated and the walk ended with a
    # traceback. A Skip keeps them and leaves the store dirty for the next run.
    def fake_request(_session, _method, url, **_kwargs):
        if url.endswith("/old/"):
            raise requests.exceptions.HTTPError(
                "404", response=SimpleNamespace(status_code=404))
        return SimpleNamespace(text='<a href="/old/">Upphävda föreskrifter</a>')

    monkeypatch.setattr(harvest, "request", fake_request)
    monkeypatch.setattr(harvest.time, "sleep", lambda _seconds: None)
    agency = _Agency(base_url="https://x.se", index_url="https://x.se/list")
    pages = list(harvest.index_soups(None, agency))
    assert len(pages) == 2
    assert isinstance(pages[1], Skip) and "https://x.se/old/" in pages[1].reason


def test_index_soups_raises_when_the_scopes_only_listing_is_gone(monkeypatch):
    # nothing has been enumerated and the entry point itself answers 404: that
    # is a changed site, not a hole to retry next run
    def fake_request(_session, _method, _url, **_kwargs):
        raise requests.exceptions.HTTPError(
            "404", response=SimpleNamespace(status_code=404))

    monkeypatch.setattr(harvest, "request", fake_request)
    agency = _Agency(base_url="https://x.se", index_url="https://x.se/list")
    try:
        list(harvest.index_soups(None, agency))
    except requests.exceptions.HTTPError:
        pass
    else:
        raise AssertionError("a dead sole listing must not pass as a Skip")


def test_uhrfs_enumerate_reads_the_upphavda_archive(monkeypatch):
    # UHRFS 2013:2 is what our own UHRFS 2023:5 names as the föreskrift it
    # repeals; it sits only on the archive page.
    _pages(monkeypatch, {
        agencies.UHRFS.index_url:
            '<a href="/f/uhrfs-2026-4-om-x.pdf">Föreskrifter om ändring</a>'
            '<a href="/upphavda/">Upphävda föreskrifter</a>',
        "https://www.uhr.se/upphavda/":
            '<a href="/f/uhrfs-2013-2-om-omradesbehorigheter.pdf">Områdesbehörigheter</a>'})
    refs = list(agencies.uhrfs_enumerate(None, agencies.UHRFS))
    assert [r.basefile for r in refs] == ["uhrfs/2026:4", "uhrfs/2013:2"]


def test_prvfs_enumerate_reads_a_number_printed_with_a_space_after_the_colon(monkeypatch):
    # A1 prints one row as "1977: 1, M:1". The space alone dropped PRVFS
    # 1977:1, the act PRVFS 2023:1 names as the one it repeals. Avdelning C
    # (upphävda författningar) holds 107 more the harvest never visited.
    _pages(monkeypatch, {
        agencies.PRVFS.index_url:
            '<a href="/globalassets/dokument/om-prv/prvfs/77prvfs_m1.pdf">1977: 1, M:1</a>'
            '<a href="/avdelning-c/">Avdelning C - Upphävda författningar</a>',
        "https://www.prv.se/avdelning-c/":
            '<a href="/globalassets/dokument/om-prv/prvfs/prvfs2015-1.pdf">2015:1, P:64</a>'})
    refs = list(agencies.prvfs_enumerate(None, agencies.PRVFS))
    assert [(r.basefile, r.identifier) for r in refs] == [
        ("prvfs/1977:1", "PRVFS 1977:1"), ("prvfs/2015:1", "PRVFS 2015:1")]


def test_last_designation_enumerate_reads_a_designation_only_the_filename_prints(monkeypatch):
    # Pliktverket's archive rows carry the title as link text and the
    # designation only in the PDF filename, so TRMFS 2017:2 and 2017:3 -- both
    # named by TPPVFS 2024:1's own repeal clause -- were unreachable.
    _pages(monkeypatch, {
        agencies.TPPVFS.index_url:
            '<a href="/download/18.a/TPPVFS 2024_1.pdf">Föreskrifter (TPPVFS 2024:1)</a>'
            '<a href="/foreskrifter/upphavda-foreskrifter">Upphävda föreskrifter</a>',
        "https://www.pliktverket.se/foreskrifter/upphavda-foreskrifter":
            '<a href="/download/18.b/UPPH%C3%84VD_TRMFS%202017_2.pdf">'
            'Föreskrifter och allmänna råd om förmåner</a>'})
    refs = list(agencies.last_designation_enumerate(None, agencies.TPPVFS))
    assert [(r.basefile, r.identifier, r.fs) for r in refs] == [
        ("tppvfs/2024:1", "TPPVFS 2024:1", "tppvfs"),
        ("trmfs/2017:2", "TRMFS 2017:2", "trmfs")]


def test_bfs_enumerate_keeps_a_bostadsstyrelsen_act_under_its_own_series(monkeypatch):
    # Boverkets API lists two Bostadsstyrelsen acts under their own BOFS
    # designation. Stamping agency.fs on every row published them as BFS, a
    # designation Boverket never used.
    items = [{"forfattning": "BOFS 1986:72", "typ": "grundforfattning",
              "titel": "Bostadsstyrelsens föreskrifter om tidskoefficienter",
              "dokumentlank": "https://rinfo.boverket.se/bofs1986-72.pdf"},
             {"forfattning": "BFS 2023:8", "typ": "grundforfattning",
              "titel": "Boverkets föreskrifter om upphävande av vissa författningar",
              "dokumentlank": "https://rinfo.boverket.se/bfs2023-8.pdf"}]
    monkeypatch.setattr(agencies, "request",
                        lambda _s, _m, _url, **_kw: items)
    refs = list(agencies.bfs_enumerate(None, agencies.BFS))
    assert [(r.basefile, r.identifier, r.fs) for r in refs] == [
        ("bofs/1986:72", "BOFS 1986:72", "bofs"),
        ("bfs/2023:8", "BFS 2023:8", None)]


def test_row_designation_ignores_a_number_that_is_not_the_rows_own():
    # an amendment row cites the base it amends; that designation says nothing
    # about the samling of the row's own document
    assert agencies.row_designation("BOFS 1986:72", "1986", "72") == "BOFS"
    assert agencies.row_designation("BFFS 1991:15", "1991", "15") == "BFFS"
    assert agencies.row_designation(
        "Föreskrifter om ändring i Boverkets föreskrifter (BFS 2011:6)",
        "2023", "8") is None
    assert agencies.row_designation("Föreskrifter om mediestöd", "2024", "1") is None


def test_memy_enumerate_files_a_row_under_the_number_it_claims_as_its_own(monkeypatch):
    # "Föreskrifter om upphävande av MPRTFS 2019:3 … (MPRTFS 2021:1)" is
    # MPRTFS 2021:1. Reading the leftmost designation filed it as MPRTFS
    # 2019:3 and then dropped the real MPRTFS 2019:3 as a duplicate basefile.
    _pages(monkeypatch, {agencies.MEMYFS.index_url: (
        '<a href="/a-mprtfs-2021_1.pdf">Föreskrifter om upphävande av MPRTFS 2019:3'
        ' om mediestöd (MPRTFS 2021:1)</a>'
        '<a href="/b-mprtfs-2019_3.pdf">Föreskrifter om mediestöd (MPRTFS 2019:3)</a>'
        '<a href="/c-memyfs-2025_3.pdf">Mediemyndighetens föreskrifter</a>'
        '<a href="/riksdagen/sfs-2018-2.pdf">Lag (2018:2) om mediestöd</a>')})
    refs = list(agencies.memy_enumerate(None, agencies.MEMYFS))
    assert [(r.basefile, r.identifier) for r in refs] == [
        ("mprtfs/2021:1", "MPRTFS 2021:1"),
        ("mprtfs/2019:3", "MPRTFS 2019:3"),
        # the row prints no designation, so the filename names the series
        ("memyfs/2025:3", "MEMYFS 2025:3")]


def test_pmfs_enumerate_reads_the_flat_template_and_the_upphavda_archive(monkeypatch):
    # the listing renders a single-version family in either of two templates.
    # The flat one has no .c-regulation__label, so 23 families were skipped by
    # `continue`; the archive holds 88 repeal acts the in-force listing never
    # shows again.
    full = ('<li class="c-list__item">'
            '<span class="c-regulation__label">PMFS 2026:18</span>'
            '<span class="c-regulation__title">Föreskrifter om ordningsvakter</span>'
            '<h4 class="c-regulation__subtitle">Grundförfattning</h4>'
            '<a class="icon-document" href="/forfattningssamling/pmfs-2026-18.pdf">PMFS 2026:18</a>'
            '</li>')
    flat = ('<li class="c-list__item">'
            '<a class="c-link icon-document" href="/forfattningssamling/pmfs-2024-10.pdf">'
            'PMFS 2024:10 (pdf, 99 kB)</a></li>')
    archive = ('<li class="c-list__item">'
               '<a class="icon-document" href="/forfattningssamling/pmfs-2026-21.pdf">'
               'PMFS 2026:21 om upphävande av RPSFS 2014:6</a></li>')
    _pages(monkeypatch, {
        "https://polisen.se/lagar-och-regler/polismyndighetens-forfattningssamling/1/":
            full + flat + '<a href="/lagar-och-regler/pmfs---upphavda/">Upphävda</a>',
        "https://polisen.se/lagar-och-regler/polismyndighetens-forfattningssamling/2/": "",
        "https://polisen.se/lagar-och-regler/pmfs---upphavda/1/": archive,
        "https://polisen.se/lagar-och-regler/pmfs---upphavda/2/": ""})
    refs = list(agencies.pmfs_enumerate(None, agencies.PMFS))
    assert [r.identifier for r in refs] == [
        "PMFS 2026:18", "PMFS 2024:10", "PMFS 2026:21"]
    # the flat template's sole link is the regulation, not a reference
    assert refs[1].extra["regulation_url"].endswith("pmfs-2024-10.pdf")


def test_stafs_and_stemfs_select_the_rows_of_their_archive_page():
    # the archive keeps its rows under its own path (swedac) or drops the
    # data-headline attributes (Energimyndigheten); the in-force selector read
    # neither, so following the archive returned nothing.
    archive = BeautifulSoup(
        '<a href="/foreskrifter/swedac/upphavda/stafs-2019-4.html">STAFS 2019:4</a>'
        '<a href="/foreskrifter/swedac/stafs-2018-7.html">STAFS 2018:7</a>', "html.parser")
    assert len(archive.select(agencies.STAFS.params["link_select"])) == 2
    stemfs = BeautifulSoup(
        '<div class="fake-tr"><div class="fake-td"><a href="/f/stemfs-2005-4">'
        'STEMFS 2005:4</a></div><div class="fake-td"><a href="/x">Ändrad</a></div></div>',
        "html.parser")
    rows = stemfs.select(agencies.STEMFS.params["link_select"])
    assert [a.get_text(strip=True) for a in rows] == ["STEMFS 2005:4"]


def test_afs_iaf_and_myh_enumerate_read_their_archive_of_repealed_regulations(monkeypatch):
    # AFS 2001:1, IAFFS 2018:3 and MYHFS 2014:1 are each named by a document we
    # already hold as the regulation it repeals, and each sits only on its
    # agency's archive page.
    _pages(monkeypatch, {
        agencies.AFS.index_url:
            '<a href="/publikationer/foreskrifter/afs-20231/">Systematiskt arbetsmiljöarbete</a>'
            '<a href="/publikationer/foreskrifter/upphavda-foreskrifter/">Upphävda föreskrifter</a>',
        "https://www.av.se/publikationer/foreskrifter/upphavda-foreskrifter/":
            '<a href="/publikationer/foreskrifter/upphavda-foreskrifter/afs-20011/">'
            'Systematiskt arbetsmiljöarbete (AFS 2001:1)</a>'})
    refs = list(agencies.afs_enumerate(None, agencies.AFS))
    assert [r.identifier for r in refs] == ["AFS 2023:1", "AFS 2001:1"]
    assert refs[1].url.endswith("/afs-20011/forfattningshistorik-afs-20011/")

    _pages(monkeypatch, {
        agencies.IAFFS.index_url:
            '<a href="/lag-ratt/foreskrifter/iaffs-20253/">IAFFS 2025:3</a>'
            '<a href="/lag-ratt/foreskrifter/upphavda-foreskrifter/">Upphävda föreskrifter</a>',
        "https://www.iaf.se/lag-ratt/foreskrifter/upphavda-foreskrifter/":
            '<a href="/lag-ratt/foreskrifter/upphavda-foreskrifter/iaffs-20183/">'
            'IAFFS 2018:3</a>'})
    assert [r.identifier for r in agencies.iaf_enumerate(None, agencies.IAFFS)] == [
        "IAFFS 2025:3", "IAFFS 2018:3"]

    _pages(monkeypatch, {
        agencies.MYHFS.index_url:
            '<a href="https://assets.myh.se/docs/myhfs-2026-5.pdf">MYHFS 2026:5</a>'
            '<a href="/lag-och-ratt/upphavda-foreskrifter-och-allmanna-rad">Upphävda</a>',
        "https://www.myh.se/lag-och-ratt/upphavda-foreskrifter-och-allmanna-rad":
            '<a href="https://assets.myh.se/docs/myhfs-2014-1.pdf">MYHFS 2014:1</a>'})
    assert [r.identifier for r in agencies.myh_enumerate(None, agencies.MYHFS)] == [
        "MYHFS 2026:5", "MYHFS 2014:1"]


def test_ts_classify_reads_every_separator_transportstyrelsen_prints():
    # Transportstyrelsen separates the year from the number with a space, an
    # underscore or a hyphen, and marks the konsoliderad text "…113k" or
    # "…113_k". Reading only the underscore form lost 136 of 2,450 linked PDFs.
    base = ("tsfs", "2012", "113")
    assert agencies.ts_classify(anchor('<a href="/TSFS/TSFS 2012_113.pdf">g</a>'),
                                *base) == ("regulation", "2012", "113")
    assert agencies.ts_classify(anchor('<a href="/TSFS/TSFS 2012_113k.pdf">k</a>'),
                                *base) == ("consolidation", "2012", "113")
    assert agencies.ts_classify(anchor('<a href="/TSFS/TSFS 2012_113_k.pdf">k</a>'),
                                *base) == ("consolidation", "2012", "113")
    # the hyphen form, which a document whose only regulation link is
    # hyphenated needs for any content at all
    assert agencies.ts_classify(anchor('<a href="/TSFS/TVFS 2025-3.pdf">g</a>'),
                                "tvfs", "2025", "3") == ("regulation", "2025", "3")
    # …and the same name as the store holds it, percent-encoded: read raw it
    # names no number at all
    assert agencies.ts_classify(anchor('<a href="/TSFS/TVFS%202025-3.pdf">g</a>'),
                                "tvfs", "2025", "3") == ("regulation", "2025", "3")
    # a predecessor samling's file on a TSFS base page is an amendment
    assert agencies.ts_classify(anchor('<a href="/TSFS/jvsfs_2008_3.pdf">a</a>'),
                                *base) == ("amendment", "2008", "3")
    # a file whose name names no number is not a document
    assert agencies.ts_classify(anchor('<a href="/TSFS/bilaga.pdf">b</a>'), *base) is None


def test_ffs_enumerate_drops_a_variant_row_that_shares_a_number(monkeypatch):
    # Försvarsmaktens listing carries a konsoliderad text and a rättelseblad
    # under the document's own number, and `ref` keeps whichever row comes
    # first -- which stored the konsoliderad text as FFS 2019:3 and the
    # rättelse as FFS 2021:2 instead of the law. The review measured the live
    # API: 134 rows, 131 plain numbers, 1 variant, and no variant without a
    # plain row for the same number, so dropping the variant loses nothing.
    rows = [{"name": "FFS 2019:3 Konsoliderad", "url": "/f/ffs-2019-03-kons.pdf",
             "preamble": "konsoliderad"},
            {"name": "FFS 2019:3", "url": "/f/ffs-2019-03.pdf", "preamble": "grund"},
            {"name": "Rättelseblad FFS 2021:2", "url": "/f/ffs-2021-02-rat.pdf",
             "preamble": "rättelse"},
            {"name": "FFS 2021:2", "url": "/f/ffs-2021-02.pdf", "preamble": "grund"},
            {"name": "FIB 2020:1", "url": "/f/fib-2020-01.pdf", "preamble": "intern"}]
    monkeypatch.setattr(agencies, "request",
                        lambda _s, _m, _url, **_kw: {"documentInfo": rows})
    agency = replace(agencies.FFS,
                     params={"api_url": "https://x/%s", "page_ids": ["1"]})
    refs = list(agencies.ffs_enumerate(None, agency))
    assert [(r.identifier, r.extra["regulation_url"].rsplit("/", 1)[-1]) for r in refs] == [
        ("FFS 2019:3", "ffs-2019-03.pdf"), ("FFS 2021:2", "ffs-2021-02.pdf")]


def test_fi_enumerate_routes_the_bankinspektionen_act_off_the_rows_own_words(monkeypatch):
    # the number is the detail URL's, the only clean number on the row; the
    # samling is what the row prints for that number. FI's förteckning still
    # carries one Bankinspektionen act, published as "FFFS 1991:15".
    _pages(monkeypatch, {agencies.FFFS.index_url: (
        '<a href="/sv/vara-register/fffs/sok-fffs/1991/199115/">'
        'BFFS 1991:15 om kapitaltäckning</a>'
        '<a href="/sv/vara-register/fffs/sok-fffs/2013/20139/">'
        'FFFS 2013:9 om värdepappersrörelse</a>')})
    refs = list(agencies.fi_enumerate(None, agencies.FFFS))
    assert [(r.basefile, r.identifier, r.fs) for r in refs] == [
        ("bffs/1991:15", "BFFS 1991:15", "bffs"),
        ("fffs/2013:9", "FFFS 2013:9", None)]
