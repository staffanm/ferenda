"""Hermetic (network-free) tests for the föreskrift harvest engine: the
classification and number-extraction logic that decides what each landing-page
file is and which regulation it belongs to. The live enumerate/resolve paths are
exercised against the real sites during a harvest, not here."""

from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from accommodanda.foreskrift import harvest
from accommodanda.foreskrift.harvest import (
    DocRef,
    Skip,
    _ref,
    classify_file,
    classify_href,
    classify_section,
    classify_single,
)
from accommodanda.foreskrift.parse import extract_publisher
from accommodanda.lib.harvest import guarded_enumerate


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


def test_ref_without_fs_from_designation_normalises_to_agency_fs():
    # the default (no opt-in): a stray designation is still normalised onto the
    # agency's own fs, so ordinary agencies are unaffected by the new capability
    seen = set()
    ref = _ref(_Agency(fs="kifs"), "KIFS 2017:7", "/kifs-20177", seen)
    assert ref.basefile == "kifs/2017:7" and ref.fs is None


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
