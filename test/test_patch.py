"""The patch-files framework: the mechanical engine (`lib.patch`), the per-source
intermediate hooks that apply patches at parse time (sfs plain text, dv innehåll
HTML, eurlex Formex XML), the `mkpatch`/`patch-show` CLI verbs, and the
authenticated web editor (`api/patch.py`)."""

import dataclasses
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import accommodanda.sfs as sfs
from accommodanda import build, config, patchsource
from accommodanda.api import app as api
from accommodanda.api import auth
from accommodanda.api import patch as patch_api
from accommodanda.dv import parse as dv_parse
from accommodanda.eurlex import parse as eurlex_parse
from accommodanda.lib import layout
from accommodanda.lib import patch

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
    """Redirect the patch store to a tmp dir so tests write no repo files."""
    root = tmp_path / "patches"
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


def test_rot13_is_obfuscated_but_roundtrips(patches):
    p = patch.create_patch("dv", "NJA 2001 s 1", ORIG, EDITED,
                           description="Redact", rot13=True)
    assert p.name.endswith(".rot13.patch")
    raw = p.read_text()
    assert "SECRET NAME" not in raw and "[redacted]" not in raw   # not googleable
    assert patch.find_patch("dv", "NJA 2001 s 1") == (p, True)
    out, desc = patch.patch_if_needed("dv", "NJA 2001 s 1", ORIG)
    assert out == EDITED and desc == "Redact"


def test_rot13_supersedes_plain(patches):
    patch.create_patch("sfs", "1999:175", ORIG, EDITED)
    patch.create_patch("sfs", "1999:175", ORIG, EDITED, rot13=True)
    # exactly one variant kept; the rot13 one wins
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


def test_dv_hook_applies_patch_to_innehall_html(patches):
    record = {"domstol": {"domstolKod": "HD", "domstolNamn": "Högsta domstolen"},
              "innehall": "<p>Käranden AA yrkade.</p>"}
    patch.create_patch("dv", "NJA 2001 s 1", record["innehall"],
                       record["innehall"].replace("AA", "[part]"), rot13=True)
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
    patch.create_patch("eurlex", "32016R0679", xml, xml.replace("SECRET", "REDACTED"))
    roots = eurlex_parse._formex_roots(src, "32016R0679")
    assert roots[0].findtext("P") == "Article one REDACTED."


def test_eurlex_hook_noop_is_byte_identical(patches, tmp_path):
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<DOC><P>plain</P></DOC>\n'
    src = tmp_path / "swe.fmx4"
    src.write_text(xml, encoding="utf-8")
    assert eurlex_parse._formex_roots(src, "32016R0679")[0].findtext("P") == "plain"


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
        "avg", "dv", "eurlex", "forarbete", "foreskrift", "remisser", "sfs"]


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
    from accommodanda.lib import pdftext
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<pdf2xml>\n'
           '<page number="1">\n<text top="1" left="1" height="10">Namn SECRET</text>\n'
           "</page>\n</pdf2xml>\n")
    monkeypatch.setattr(pdftext.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=xml.encode("utf-8")))
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
    monkeypatch.setattr(build.RUN, "rot13", False)
    monkeypatch.setattr(build.RUN, "dry_run", False)

    args = SimpleNamespace(source="sfs", basefiles=["1999:175", str(edited), "OCR fix"])
    build.cmd_mkpatch(args, _Parser())
    assert patch.has_patch("sfs", "1999:175")
    assert patch.patch_if_needed("sfs", "1999:175", ORIG) == (EDITED, "OCR fix")

    # patch-show now emits the patched (current) text
    build.cmd_patch_show(SimpleNamespace(source="sfs", basefiles=["1999:175"]), _Parser())
    assert "[redacted]" in capsys.readouterr().out


def test_cli_mkpatch_rot13_flag(patches, monkeypatch, tmp_path):
    monkeypatch.setattr(patchsource, "intermediate", lambda s, bf: (ORIG, "plain text"))
    monkeypatch.setattr(build.RUN, "rot13", True)
    monkeypatch.setattr(build.RUN, "dry_run", False)
    edited = tmp_path / "e.txt"
    edited.write_text(EDITED)
    build.cmd_mkpatch(SimpleNamespace(source="sfs", basefiles=["1999:175", str(edited)]),
                      _Parser())
    assert patch.find_patch("sfs", "1999:175")[1] is True   # stored rot13


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
    """A git 'code repo', a configured editor, an isolated patch store keyed to a
    single fake patchable source whose pristine text is a fixed constant, and a
    recording reparse stub."""
    repo = tmp_path / "repo"
    (repo / "patches").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Seed")
    _git(repo, "config", "user.email", "seed@example.org")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "seed")

    monkeypatch.setattr(config, "REPO", repo)
    monkeypatch.setattr(patch_api.config, "REPO", repo)
    monkeypatch.setattr(layout, "PATCHES", repo / "patches")
    monkeypatch.setattr(config, "EDITOR_SECRET", "test-signing-key")
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
    return c.post("/api/v1/auth/login", json={"username": "anna", "password": "hunter2"})


def test_web_requires_login(webenv):
    c = TestClient(api.app)
    assert c.get("/api/v1/patch/document",
                 params={"source": "sfs", "basefile": "1999:175"}).status_code == 401


def test_web_get_document(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.get("/api/v1/patch/document", params={"source": "sfs", "basefile": "1999:175"})
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "plain text"
    assert body["text"] == ORIG and body["has_patch"] is False


def test_web_save_commits_and_reparses(webenv):
    repo, reparsed = webenv
    c = TestClient(api.app)
    _login(c)
    base_sha = c.get("/api/v1/patch/document",
                     params={"source": "sfs", "basefile": "1999:175"}).json()["base_sha"]
    r = c.post("/api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "Rättad OCR", "rot13": False, "base_sha": base_sha})
    assert r.status_code == 200
    assert r.json()["path"] == "patches/sfs/1999/175.patch"
    assert patch.patch_if_needed("sfs", "1999:175", ORIG) == (EDITED, "Rättad OCR")
    assert reparsed == [("sfs", "1999:175")]
    assert _git(repo, "log", "-1", "--format=%an|%ae") == "Anna Ek|anna@example.org"


def test_web_save_rot13(webenv):
    c = TestClient(api.app)
    _login(c)
    base_sha = c.get("/api/v1/patch/document",
                     params={"source": "sfs", "basefile": "1999:175"}).json()["base_sha"]
    r = c.post("/api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "", "rot13": True, "base_sha": base_sha})
    assert r.status_code == 200 and r.json()["path"].endswith(".rot13.patch")
    assert patch.find_patch("sfs", "1999:175")[1] is True


def test_web_save_stale_source_409(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.post("/api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "", "rot13": False, "base_sha": "stale-sha"})
    assert r.status_code == 409


def test_web_save_noop_removes(webenv):
    repo, _ = webenv
    c = TestClient(api.app)
    _login(c)
    doc = c.get("/api/v1/patch/document",
                params={"source": "sfs", "basefile": "1999:175"}).json()
    c.post("/api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": EDITED,
        "description": "", "rot13": False, "base_sha": doc["base_sha"]})
    # editing back to the pristine text removes the patch
    r = c.post("/api/v1/patch/save", json={
        "source": "sfs", "basefile": "1999:175", "edited_text": ORIG,
        "description": "", "rot13": False, "base_sha": doc["base_sha"]})
    assert r.json()["removed"] is True
    assert not patch.has_patch("sfs", "1999:175")


def test_web_edit_page_served(webenv):
    c = TestClient(api.app)
    _login(c)
    r = c.get("/api/v1/patch/edit", params={"source": "sfs", "basefile": "1999:175"})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "intermediate format: plain text" in r.text


def test_web_disabled_without_secret(webenv, monkeypatch):
    monkeypatch.setattr(config, "EDITOR_SECRET", None)
    c = TestClient(api.app)
    assert c.get("/api/v1/patch/document",
                 params={"source": "sfs", "basefile": "1999:175"}).status_code == 403
