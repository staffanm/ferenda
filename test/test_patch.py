"""The patch-files framework: the mechanical engine (`lib.patch`), the per-source
intermediate hooks that apply patches at parse time (sfs plain text, dv innehåll
HTML, eurlex Formex XML), the `mkpatch`/`patch-show` CLI verbs, and the
authenticated web editor (`api/patch.py`)."""

import dataclasses
import json
import pathlib
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from lxml import etree

import ferenda.sfs as sfs
from ferenda import build, config, patchsource
from ferenda.api import app as api
from ferenda.api import auth
from ferenda.api import patch as patch_api
from ferenda.dv import legacy as dv_legacy
from ferenda.dv import parse as dv_parse
from ferenda.dv import paths as dv_paths
from ferenda.eurlex import parse as eurlex_parse
from ferenda.foreskrift import parse as fs_parse
from ferenda.lib import formex as lib_formex
from ferenda.lib import harvest, layout, markup, patch, pdftext, util
from ferenda.lib.errors import SkipDocument

ORIG = "line one\nSECRET NAME\nline three\nline four\n"
EDITED = "line one\n[redacted]\nline three\nline four\n"


def _all_text(obj, out):
    """Every str leaf of a (possibly nested) dataclass/list -- to assert the
    parsed model carries a given body string."""
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            _all_text(getattr(obj, f.name), out)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _all_text(item, out)
    elif isinstance(obj, str):
        out.append(obj)
    return out


@pytest.fixture
def patches(tmp_path, monkeypatch):
    """Redirect the patch store to a tmp dir so tests write no repo files. The
    dir is created: a checkout always carries the tracked patch tree, and
    `layout.patch` refuses an absent one (an absent tree would silently drop
    every redaction)."""
    root = tmp_path / "patches"
    root.mkdir()
    monkeypatch.setattr(layout, "PATCHES", root)
    return root


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

def test_create_apply_roundtrip(patches):
    p = patch.create_patch("sfs", "1999:175", ORIG, EDITED, description="Fix name")
    assert p.name == "175.patch"
    assert p.parent == patches / "sfs" / "1999"
    out, desc = patch.patch_if_needed("sfs", "1999:175", ORIG)
    assert out == EDITED
    assert desc == "Fix name"                      # rode on the @@ header


def test_minimal_diff(patches):
    patch.create_patch("sfs", "1999:175", ORIG, EDITED)
    body = patch.find_patch("sfs", "1999:175")[0].read_text()
    # only the one changed line is in the diff, not the whole document
    assert "-SECRET NAME" in body and "+[redacted]" in body
    assert "line four" not in body.replace(" line four", "")  # only as context, once


def test_obfuscated_patch_hides_its_content_and_roundtrips(patches):
    p = patch.create_patch("dv", "NJA 2001 s 1", ORIG, EDITED,
                           description="Redact", obfuscated=True)
    assert p.name.endswith(".rot18.patch")
    raw = p.read_text()
    assert "SECRET NAME" not in raw and "[redacted]" not in raw   # not googleable
    assert patch.find_patch("dv", "NJA 2001 s 1") == (p, True)
    out, desc = patch.patch_if_needed("dv", "NJA 2001 s 1", ORIG)
    assert out == EDITED and desc == "Redact"


def test_obfuscation_covers_digits(patches):
    # the bug this guards: plain ROT13 rotates letters only, so a personnummer,
    # an organisationsnummer or a telephone number -- all digits, and most of
    # what a redaction patch removes -- sat in the "obfuscated" file in the clear
    text = "Personnummer 820310-5542 och telefon 070-123 45 67\n"
    patch.create_patch("dv", "NJA 2001 s 1", text,
                       text.replace("820310-5542", "[borttaget]"), obfuscated=True)
    raw = patch.find_patch("dv", "NJA 2001 s 1")[0].read_text()
    assert "820310-5542" not in raw
    assert "070-123 45 67" not in raw
    assert patch.obfuscate(patch.obfuscate(text)) == text     # an involution


def test_obfuscated_supersedes_plain(patches):
    patch.create_patch("sfs", "1999:175", ORIG, EDITED)
    patch.create_patch("sfs", "1999:175", ORIG, EDITED, obfuscated=True)
    # exactly one variant kept; the obfuscated one wins
    assert not layout.patch("sfs", "1999:175", ".patch").exists()
    assert patch.find_patch("sfs", "1999:175")[1] is True


def test_multiline_description_sidecar(patches):
    patch.create_patch("sfs", "1999:175", ORIG, EDITED, description="A\n\nB")
    assert layout.patch("sfs", "1999:175", ".desc").exists()
    assert patch.patch_if_needed("sfs", "1999:175", ORIG)[1] == "A\n\nB"


def test_noop_edit_removes_patch(patches):
    patch.create_patch("sfs", "1999:175", ORIG, EDITED)
    assert patch.has_patch("sfs", "1999:175")
    assert patch.create_patch("sfs", "1999:175", ORIG, ORIG) is None
    assert not patch.has_patch("sfs", "1999:175")


def test_conflict_is_fatal(patches):
    patch.create_patch("sfs", "1999:175", ORIG, EDITED)
    with pytest.raises(patch.PatchError):
        patch.patch_if_needed("sfs", "1999:175", "completely\ndifferent\nsource\n")


def test_missing_patch_is_noop(patches):
    assert patch.patch_if_needed("sfs", "1999:175", ORIG) == (ORIG, None)


def test_malformed_patch_raises(patches):
    p = layout.patch("sfs", "1999:175")
    p.parent.mkdir(parents=True)
    p.write_text("this is not a unified diff")
    with pytest.raises(patch.PatchError):
        patch.load_patchset("sfs", "1999:175")


def test_context_drift_is_tolerated(patches):
    # a patch cut against ORIG still applies when the source gained a leading line
    patch.create_patch("sfs", "1999:175", ORIG, EDITED, description="Fix")
    drifted = "new preamble line\n" + ORIG
    out, _ = patch.patch_if_needed("sfs", "1999:175", drifted)
    assert out == "new preamble line\n" + EDITED


def test_apply_if_fits_skips_a_correction_that_conflicts(patches):
    # a historical revision the correction predates is published unpatched --
    # an uncorrected lydelse is still a true lydelse
    patch.create_patch("sfs", "1999:175", ORIG, EDITED)
    other = "completely\ndifferent\nsource\n"
    assert patch.apply_if_fits("sfs", "1999:175", other) == other
    assert patch.apply_if_fits("sfs", "1999:175", ORIG) == EDITED


def test_apply_if_fits_never_skips_a_redaction(patches):
    # republishing the personal data the patch exists to remove, because the
    # diff did not line up against an older wording, is the harm itself
    patch.create_patch("sfs", "1999:175", ORIG, EDITED, obfuscated=True)
    with pytest.raises(patch.PatchError):
        patch.apply_if_fits("sfs", "1999:175", "completely\ndifferent\nsource\n")
    assert patch.apply_if_fits("sfs", "1999:175", ORIG) == EDITED


def test_apply_if_fits_without_a_patch(patches):
    assert patch.apply_if_fits("sfs", "1999:175", ORIG) == ORIG


# --------------------------------------------------------------------------
# markup normalisation -- what makes a single-line source diffable at all
# --------------------------------------------------------------------------

def test_block_lines_breaks_only_between_blocks():
    one_line = "<div><p>first para</p><p>second <em>emphasised</em> para</p></div>"
    assert markup.block_lines(one_line).split("\n") == [
        "<div>", "<p>first para</p>", "<p>second <em>emphasised</em> para</p>", "</div>"]


def test_block_lines_leaves_a_block_body_alone():
    # `<br>` is dv's only in-paragraph newline, and a second one would show up
    # as an empty line in the stycke -- so nothing inside a block may be split
    assert markup.block_lines("<p>a<br>b</p>") == "<p>a<br>b</p>"


def test_block_lines_is_idempotent():
    once = markup.block_lines("<p>a</p><p>b</p>")
    assert markup.block_lines(once) == once


def test_indent_xml_keeps_element_text_on_one_line():
    root = etree.fromstring(b"<DOC><TI>Title</TI><P>Some <HT>mixed</HT> text.</P></DOC>")
    assert markup.indent_xml(root).split("\n") == [
        "<DOC>", "  <TI>Title</TI>", "  <P>Some <HT>mixed</HT> text.</P>", "</DOC>"]


def test_dv_intermediate_is_the_whole_record_one_block_per_line(patches,
                                                                monkeypatch, tmp_path):
    record = tmp_path / "case.json"
    record.write_text('{"malNummerLista": ["B 1-11"], "innehall": "<p>one</p><p>two</p>"}',
                      encoding="utf-8")
    monkeypatch.setattr(dv_paths, "record", lambda bf: record)
    text, _label = patchsource.intermediate("dv", "NJA 2001 s 1")
    # the record's own JSON, so the structured metadata is patchable too, with
    # the innehåll as one block element per line
    assert json.loads(text) == {"malNummerLista": ["B 1-11"],
                                "innehall": ["<p>one</p>", "<p>two</p>"]}


def test_dv_notis_intermediate_and_patch(patches, monkeypatch, tmp_path):
    # a legacy-only notisfall is patched through its frozen intermediate XML --
    # plain `<para>`-per-line text, so no normalisation applies
    xml = ("<body>\n<para>R4 M:REGR Unr:g Lnr:RÅ 1996 not 1</para>\n"
           "<para>Lagrum:</para>\n<para>37 c § SECRET (1971:291)</para>\n</body>\n")
    # parse_notis reads the series off the parent directory name
    (tmp_path / "REG").mkdir()
    record = tmp_path / "REG" / "1996_not_1.xml"
    record.write_text(xml, encoding="utf-8")
    monkeypatch.setattr(dv_paths, "record", lambda bf: record)
    assert patchsource.intermediate("dv", "RÅ 1996 not 1")[0] == xml

    patch.create_patch("dv", "RÅ 1996 not 1", xml, xml.replace("SECRET", "[X]"),
                       obfuscated=True)
    case = {"canonical_id": "RÅ 1996 not 1", "courts": ["REGR"],
            "malnummer": [], "referat": ["RÅ 1996 not 1"]}
    av = dv_legacy.parse_legacy_file(record, case)
    joined = " ".join(_all_text(av, []))
    assert "[X]" in joined and "SECRET" not in joined


def test_dv_word_referat_has_no_patchable_intermediate(patches, monkeypatch,
                                                       tmp_path):
    # read through POI, so there is no editable text form to diff against
    doc = tmp_path / "T 1-99.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0")
    monkeypatch.setattr(dv_paths, "record", lambda bf: doc)
    with pytest.raises(SkipDocument):
        patchsource.intermediate("dv", "NJA 1999 s. 1")


def test_dv_intermediate_without_innehall_or_pdf(patches, monkeypatch, tmp_path):
    # ~290 dv cases carry neither; parse tolerates them, so this must skip
    # cleanly rather than crash the editor on a None path
    record = tmp_path / "case.json"
    record.write_text('{"malNummerLista": ["B 1-11"]}', encoding="utf-8")
    monkeypatch.setattr(dv_paths, "record", lambda bf: record)
    monkeypatch.setattr(dv_paths, "verdict_pdf", lambda bf, rec: None)
    with pytest.raises(SkipDocument):
        patchsource.intermediate("dv", "MDO 2002-7")


def test_dv_record_intermediate_roundtrips():
    record = {"malNummerLista": ["B 1-11"], "avgorandedatum": "2011-01-01",
              "innehall": "<p>one</p><p>two</p>"}
    back = dv_parse.record_from_intermediate(dv_parse.record_intermediate(record))
    # every field survives; the innehåll comes back one block per line, which is
    # what parse reads, so a second round trip is the identity
    assert back == {**record, "innehall": "<p>one</p>\n<p>two</p>"}
    assert dv_parse.record_from_intermediate(dv_parse.record_intermediate(back)) == back


def test_dv_hook_normalises_before_patching(patches):
    # the API ships a tenth of its records as one line; a patch is authored
    # against the normalised record, so parse must normalise identically
    record = {"domstol": {"domstolKod": "HD", "domstolNamn": "Högsta domstolen"},
              "innehall": "<p>Käranden AA yrkade.</p><p>Svaranden bestred.</p>"}
    text = dv_parse.record_intermediate(record)
    patch.create_patch("dv", "NJA 2001 s 1", text,
                       text.replace("AA", "[part]"), obfuscated=True)
    joined = " ".join(_all_text(dv_parse.parse_api_record(record, "NJA 2001 s 1").body, []))
    assert "[part]" in joined and "AA" not in joined


def test_dv_pdf_record_applies_patch(patches, monkeypatch):
    # a verdict published before its referat has no innehåll at all -- its body
    # comes from the court's own PDF, so the patch hook there is the PDF one
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<pdf2xml>\n'
           '<page number="1">\n<text top="1" left="1" height="10">Namn SECRET</text>\n'
           "</page>\n</pdf2xml>\n")

    def run(cmd, *a, **k):
        pathlib.Path(cmd[-1] + ".xml").write_bytes(xml.encode("utf-8"))
        return SimpleNamespace(stdout=xml.encode("utf-8"))

    monkeypatch.setattr(pdftext.subprocess, "run", run)
    monkeypatch.setattr(dv_parse, "_paragraph_numbers", lambda path: {})
    record = {"domstol": {"domstolKod": "HD", "domstolNamn": "Högsta domstolen"}}
    patch.create_patch("dv", "HD B 1-25", xml, xml.replace("SECRET", "[X]"),
                       obfuscated=True)
    av = dv_parse.parse_pdf_record(record, "x.pdf", "HD B 1-25")
    joined = " ".join(_all_text(av.body, []))
    assert "[X]" in joined and "SECRET" not in joined


def test_dv_patch_reaches_structured_metadata(patches):
    # a målnummer the court published in the clear is in `malNummerLista` as
    # well as the running text; redacting one and not the other leaves the
    # redacted party able to find their own case by the number
    record = {"domstol": {"domstolKod": "HFD", "domstolNamn": "HFD"},
              "malNummerLista": ["4337-12"],
              "innehall": "<p>Mål nr 4337-12, föredragande Axelsson</p>"}
    text = dv_parse.record_intermediate(record)
    patch.create_patch("dv", "HFD 2013 ref. 47", text,
                       text.replace("4337-12", "0000-12"), obfuscated=True)
    av = dv_parse.parse_api_record(record, "HFD 2013 ref. 47")
    assert av.malnummer == ["0000-12"]
    assert "4337-12" not in " ".join(_all_text(av.body, []))


# --------------------------------------------------------------------------
# per-source parse hooks (the "best intermediate format" per source)
# --------------------------------------------------------------------------

def test_sfs_hook_applies_patch_to_plain_text(patches):
    text = "1 § Detta är en paragraf med SECRET text.\n"
    patch.create_patch("sfs", "1999:1", text,
                       text.replace("SECRET", "[redacted]"), description="Redigering")
    tree = sfs._assemble(text, "1999:1")           # the real parse choke-point
    body = _all_text(tree, [])
    assert any("[redacted]" in t for t in body)
    assert not any("SECRET" in t for t in body)


def test_dv_hook_applies_patch_to_the_record(patches):
    record = {"domstol": {"domstolKod": "HD", "domstolNamn": "Högsta domstolen"},
              "innehall": "<p>Käranden AA yrkade.</p>"}
    text = dv_parse.record_intermediate(record)
    patch.create_patch("dv", "NJA 2001 s 1", text,
                       text.replace("AA", "[part]"), obfuscated=True)
    av = dv_parse.parse_api_record(record, "NJA 2001 s 1")
    joined = " ".join(_all_text(av.body, []))
    assert "[part]" in joined and "AA" not in joined


def test_dv_hook_noop_without_basefile(patches):
    record = {"domstol": {"domstolKod": "HD", "domstolNamn": "HD"},
              "innehall": "<p>Text AA.</p>"}
    patch.create_patch("dv", "NJA 2001 s 1", record["innehall"], "<p>Text BB.</p>")
    # no basefile => no patch key => unpatched (back-compat with existing callers)
    av = dv_parse.parse_api_record(record)
    assert "AA" in " ".join(_all_text(av.body, []))


def test_eurlex_hook_applies_patch_to_formex(patches, tmp_path):
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           "<DOC>\n<P>Article one SECRET.</P>\n</DOC>\n")
    src = tmp_path / "swe.fmx4"
    src.write_text(xml, encoding="utf-8")
    # a Formex patch targets the *normalised* XML (one element per line) --
    # the same text `patchsource.intermediate` hands the editor
    normalised = lib_formex.formex_intermediate(xml.encode("utf-8"))
    patch.create_patch("eurlex", "32016R0679", normalised,
                       normalised.replace("SECRET", "REDACTED"))
    roots = lib_formex.formex_roots(src, "eurlex", "32016R0679")
    assert roots[0].findtext("P") == "Article one REDACTED."


def test_eurlex_hook_noop_is_byte_identical(patches, tmp_path):
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<DOC><P>plain</P></DOC>\n'
    src = tmp_path / "swe.fmx4"
    src.write_text(xml, encoding="utf-8")
    assert lib_formex.formex_roots(src, "eurlex", "32016R0679")[0].findtext("P") == "plain"


# --------------------------------------------------------------------------
# patchsource registry
# --------------------------------------------------------------------------

def test_patchsource_intermediate_sfs(monkeypatch, tmp_path):
    src = tmp_path / "585.json"
    src.write_text('{"fulltext": {"forfattningstext": "1 §\\r\\ntext"}}')
    monkeypatch.setattr(layout, "sfs_source", lambda bf: src)
    text, label = patchsource.intermediate("sfs", "2018:585")
    assert text == "1 §\ntext" and label == "plain text"


def test_patchsource_lists_all_wired_sources():
    # sfs/dv/eurlex (text) + the pdftohtml-XML PDF sources + avg (mixed)
    assert patchsource.patchable_sources() == [
        "avg", "dv", "eurlex", "forarbete", "foreskrift", "guidance",
        "remisser", "rs", "sfs"]


def test_patchsource_rejects_non_patchable_source():
    # a source with no parse-time patch hook (editorial markdown) is not patchable
    with pytest.raises(ValueError):
        patchsource.intermediate("site", "frontpage")


def test_patchsource_pdf_dispatch(monkeypatch):
    monkeypatch.setattr(patchsource, "_pdf_xml", lambda p: "<pdf>%s</pdf>" % p)
    monkeypatch.setattr(patchsource.layout, "remisser_answer",
                        lambda case, org: "/x/%s/%s.pdf" % (case, org))
    text, label = patchsource.intermediate("remisser", "case/org")
    assert text == "<pdf>/x/case/org.pdf</pdf>" and label == "pdftohtml XML"


def test_pdf_pages_applies_patch(patches, monkeypatch):
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<pdf2xml>\n'
           '<page number="1">\n<text top="1" left="1" height="10">Namn SECRET</text>\n'
           "</page>\n</pdf2xml>\n")
    # the converter is given an output base in a temp directory and reads the
    # XML back from it -- which is what keeps the images poppler extracts out of
    # the corpus -- so the stub writes the file rather than returning stdout
    def run(cmd, *a, **k):
        pathlib.Path(cmd[-1] + ".xml").write_bytes(xml.encode("utf-8"))
        return SimpleNamespace(stdout=xml.encode("utf-8"))

    monkeypatch.setattr(pdftext.subprocess, "run", run)
    # no patch -> the pdftohtml XML is parsed as-is
    assert list(pdftext.pdf_pages("x.pdf", ("remisser", "c/o")))[0][1][0].text \
        == "Namn SECRET"
    # a patch on the XML redacts the extracted text at the pdf_pages choke-point
    patch.create_patch("remisser", "c/o", xml, xml.replace("SECRET", "[X]"))
    assert list(pdftext.pdf_pages("x.pdf", ("remisser", "c/o")))[0][1][0].text \
        == "Namn [X]"


# --------------------------------------------------------------------------
# CLI: patch-show / mkpatch
# --------------------------------------------------------------------------

class _Parser:
    def error(self, msg):
        raise SystemExit(msg)


def test_cli_mkpatch_and_show(patches, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(patchsource, "intermediate", lambda s, bf: (ORIG, "plain text"))
    monkeypatch.setattr(patchsource, "current",
                        lambda s, bf: (patch.patch_if_needed(s, bf, ORIG)[0], "plain text"))
    edited = tmp_path / "edited.txt"
    edited.write_text(EDITED)
    monkeypatch.setattr(build.RUN, "obfuscated", False)
    monkeypatch.setattr(build.RUN, "dry_run", False)

    args = SimpleNamespace(source="sfs", basefiles=["1999:175", str(edited), "OCR fix"])
    build.cmd_mkpatch(args, _Parser())
    assert patch.has_patch("sfs", "1999:175")
    assert patch.patch_if_needed("sfs", "1999:175", ORIG) == (EDITED, "OCR fix")

    # patch-show now emits the patched (current) text
    build.cmd_patch_show(SimpleNamespace(source="sfs", basefiles=["1999:175"]), _Parser())
    assert "[redacted]" in capsys.readouterr().out


def test_cli_mkpatch_obfuscated_flag(patches, monkeypatch, tmp_path):
    monkeypatch.setattr(patchsource, "intermediate", lambda s, bf: (ORIG, "plain text"))
    monkeypatch.setattr(build.RUN, "obfuscated", True)
    monkeypatch.setattr(build.RUN, "dry_run", False)
    edited = tmp_path / "e.txt"
    edited.write_text(EDITED)
    build.cmd_mkpatch(SimpleNamespace(source="sfs", basefiles=["1999:175", str(edited)]),
                      _Parser())
    assert patch.find_patch("sfs", "1999:175")[1] is True   # stored obfuscated


def test_cli_mkpatch_rejects_unpatchable_source(patches):
    with pytest.raises(SystemExit):   # 'site' is editorial markdown, no parse hook
        build.cmd_mkpatch(SimpleNamespace(source="site", basefiles=["frontpage", "f"]),
                          _Parser())


# --------------------------------------------------------------------------
# the web editor (api/patch.py)
# --------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def webenv(tmp_path, monkeypatch):
    """A checkout standing in for the content repo, a configured editor, an
    isolated patch store keyed to a single fake patchable source whose pristine
    text is a fixed constant, and a recording reparse stub."""
    repo = tmp_path / "repo"
    (repo / "patches").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Seed")
    _git(repo, "config", "user.email", "seed@example.org")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "seed")

    monkeypatch.setattr(config, "WIKI_ROOT", repo)
    monkeypatch.setattr(layout, "PATCHES", repo / "patches")
    monkeypatch.setattr(config, "EDITOR_SECRET", "test-signing-key")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "EDITORS", {"anna": {
        "name": "Anna Ek", "email": "anna@example.org",
        "pwhash": auth.hash_password("hunter2", rounds=1000)}})
    monkeypatch.setattr(patchsource, "_INTERMEDIATE",
                        {"sfs": (lambda bf: ORIG, "plain text")})
    reparsed = []
    monkeypatch.setattr(patch_api, "_reparse",
                        lambda s, bf: reparsed.append((s, bf)))
    return repo, reparsed


def _login(c):
    return c.post("/internal-api/v1/auth/login", json={"username": "anna", "password": "hunter2"})


def test_web_requires_login(webenv):
    c = TestClient(api.app)
    assert c.get("/internal-api/v1/patch/document",
                 params={"source": "sfs", "basefile": "1999:175"}).status_code == 401


def test_web_get_document(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.get("/internal-api/v1/patch/document", params={"source": "sfs", "basefile": "1999:175"})
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "plain text"
    assert body["text"] == ORIG and body["has_patch"] is False


def test_web_save_commits_and_reparses(webenv):
    repo, reparsed = webenv
    c = TestClient(api.app)
    _login(c)
    base_sha = c.get("/internal-api/v1/patch/document",
                     params={"source": "sfs", "basefile": "1999:175"}).json()["base_sha"]
    r = c.post("/internal-api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "Rättad OCR", "obfuscated": False, "base_sha": base_sha})
    assert r.status_code == 200
    assert r.json()["path"] == "patches/sfs/1999/175.patch"
    assert patch.patch_if_needed("sfs", "1999:175", ORIG) == (EDITED, "Rättad OCR")
    assert reparsed == [("sfs", "1999:175")]
    assert _git(repo, "log", "-1", "--format=%an|%ae") == "Anna Ek|anna@example.org"


def test_web_save_obfuscated(webenv):
    c = TestClient(api.app)
    _login(c)
    base_sha = c.get("/internal-api/v1/patch/document",
                     params={"source": "sfs", "basefile": "1999:175"}).json()["base_sha"]
    r = c.post("/internal-api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "", "obfuscated": True, "base_sha": base_sha})
    assert r.status_code == 200 and r.json()["path"].endswith(".rot18.patch")
    assert patch.find_patch("sfs", "1999:175")[1] is True


def test_web_save_stale_source_409(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.post("/internal-api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "", "obfuscated": False, "base_sha": "stale-sha"})
    assert r.status_code == 409


def test_web_save_noop_removes(webenv):
    repo, _ = webenv
    c = TestClient(api.app)
    _login(c)
    doc = c.get("/internal-api/v1/patch/document",
                params={"source": "sfs", "basefile": "1999:175"}).json()
    c.post("/internal-api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "", "obfuscated": False, "base_sha": doc["base_sha"]})
    # editing back to the pristine text removes the patch
    r = c.post("/internal-api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": ORIG,
        "description": "", "obfuscated": False, "base_sha": doc["base_sha"]})
    assert r.json()["removed"] is True
    assert not patch.has_patch("sfs", "1999:175")


def test_web_edit_page_served(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.get("/internal-api/v1/patch/edit", params={"source": "sfs", "basefile": "1999:175"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "intermediate format: plain text" in r.text


def test_web_disabled_without_secret(webenv, monkeypatch):
    monkeypatch.setattr(config, "EDITOR_SECRET", None)
    c = TestClient(api.app)
    assert c.get("/internal-api/v1/patch/document",
                 params={"source": "sfs", "basefile": "1999:175"}).status_code == 403


# --------------------------------------------------------------------------
# a basefile stays inside its own tree
# --------------------------------------------------------------------------

@pytest.mark.parametrize("source,basefile", [
    ("sfs", "../../artifact/sfs/2020:123"),      # the split puts ".." in the year
    ("hudoc", "../../../etc/passwd"),            # used verbatim as the path
    ("site", "/etc/passwd"),                     # absolute: the join drops the root
    ("forarbete", "prop/../../../x"),
])
def test_a_basefile_cannot_leave_its_tree(source, basefile):
    """`basefile` is untrusted: /patch reads it out of a request body. The
    refusal lives in `util.confine`, which `relpath` calls, so the artifact and
    downloaded trees are covered at once. (`layout.patch` is the same basefile
    through `relpath`; the route test below covers that end.)"""
    with pytest.raises(ValueError, match="leaves the"):
        layout.relpath(source, basefile)


@pytest.mark.parametrize("call", [
    lambda: layout.sfs_source("../../../../etc/x:passwd"),
    lambda: layout.sfs_sfst("../../x:1"),
    lambda: layout.sfs_sfsr("../../x:1"),
    lambda: layout.sfs_pdf("../../x:1"),
    lambda: layout.sfs_version_file(pathlib.Path("/d"), "1999:175", "../../x:1"),
    lambda: layout.sfs_version_file(pathlib.Path("/d"), "1999:175", "../11"),
    lambda: layout.fa_record("../../x/y"),
    lambda: layout.fa_dir("/d", "..", "2021:82"),
    lambda: layout.remisser_arende("../../x/y"),
    lambda: layout.remisser_answer("../x/y", "org"),
    lambda: harvest.pdf_path("/d", "../x"),
    lambda: harvest.page_path("/d", "../x"),
    lambda: util.record_path("/d", "..", "x"),
    lambda: fs_parse.body_path("/d", "..", {"name": "x.pdf"}),
])
def test_a_builder_that_splits_the_identifier_itself_also_confines_it(call):
    """These paths do not go through `relpath` -- each splits the identifier
    itself. That is the *read* side of the same untrusted string: `patchsource`
    hands the /patch basefile straight to `layout.sfs_source`, so a confined
    write side alone would still let the editor read a file outside the tree.
    The archive version id and a foreskrift record's file name are not
    basefiles, but they become path segments the same way."""
    with pytest.raises(ValueError, match="leaves the"):
        call()


def test_the_patch_editor_writes_nothing_outside_the_patch_tree(webenv):
    """The same input through the route. It is an editor-only endpoint, so this
    is not an anonymous hole -- but a patch is committed and reparsed, and one
    written above the tree would land in the checkout's own source."""
    repo, reparsed = webenv
    c = TestClient(api.app)
    _login(c)
    escaping = {"source": "sfs", "basefile": "../../ferenda/config"}
    # the load looks for an existing patch first, and that lookup is the refusal
    assert c.get("/internal-api/v1/patch/document", params=escaping).status_code == 404
    r = c.post("/internal-api/v1/patch/save",
               json={**escaping, "edited_text": EDITED, "description": "",
                     "obfuscated": False, "base_sha": "0" * 64})
    assert r.status_code == 404
    assert reparsed == []
    assert list((repo / "patches").rglob("*")) == []


# --------------------------------------------------------------------------
# where the patch tree sits, and the guard on an absent one
# --------------------------------------------------------------------------

def test_patches_live_in_the_content_repo(monkeypatch):
    """Patches sit in the content repo beside the commentaries and the
    annotation layers, not in this code repo -- one checkout for everything the
    running site writes, so there is one mount and one push."""
    assert layout.PATCHES == config.WIKI_ROOT / "patches"
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    assert config.resolve_wiki_root({}) == config.DEFAULT_WIKI_ROOT


def test_wiki_root_precedence(monkeypatch, tmp_path):
    """env beats config.yml beats the default -- the precedence every other
    scalar setting in config.py follows."""
    monkeypatch.delenv("WIKI_ROOT", raising=False)
    assert config.resolve_wiki_root({"wiki_root": str(tmp_path)}) == tmp_path

    monkeypatch.setenv("WIKI_ROOT", str(tmp_path / "from-env"))
    assert (config.resolve_wiki_root({"wiki_root": str(tmp_path)})
            == tmp_path / "from-env")


def test_absent_patch_tree_is_fatal(monkeypatch, tmp_path):
    """A WIKI_ROOT with no patch tree must fail loudly. Returning "no patch"
    instead would drop every redaction and republish the personal data the
    .rot18 patches exist to remove."""
    monkeypatch.setattr(layout, "PATCHES", tmp_path / "nope")
    monkeypatch.setattr(config, "WIKI_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="WIKI_ROOT"):
        layout.patch("sfs", "2020:123")
