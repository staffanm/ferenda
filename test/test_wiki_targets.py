"""What a push to the content repo re-stales -- tools/operations/wiki_targets.py.

The tool exists so a one-word edit to a news item does not cost a corpus-wide
rebuild. What it must never do is the opposite: answer with a narrower command
than the change deserves, which publishes a stale page and looks exactly like a
correct answer.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from ferenda.lib import layout

_SPEC = importlib.util.spec_from_file_location(
    "wiki_targets",
    Path(__file__).parent.parent / "tools" / "operations" / "wiki_targets.py")
wiki_targets = importlib.util.module_from_spec(_SPEC)
sys.modules["wiki_targets"] = wiki_targets
_SPEC.loader.exec_module(wiki_targets)

WHOLE = "lagen all rebuild --ignore-code-changes"


@pytest.fixture
def wiki(tmp_path):
    """A content repo with one of each kind of input."""
    (tmp_path / "site" / "om").mkdir(parents=True)
    (tmp_path / "site" / "media").mkdir()
    (tmp_path / "site" / "sitenews.md").write_text(
        "---\ntitle: Nyheter\n---\n## 2026-09-06 21:55:00 Hej\n\nText.\n")
    (tmp_path / "site" / "om" / "nytt.md").write_text(
        "---\ntitle: Vad är nytt?\n---\n\nText.\n")
    (tmp_path / "site" / "media" / "oversikt.webm").write_bytes(b"\x1a\x45\xdf\xa3")
    (tmp_path / "concept").mkdir()
    (tmp_path / "concept" / "fullmakt.md").write_text(
        "---\ntitle: Fullmakt\n---\n\nText.\n")
    (tmp_path / "commentary" / "sfs" / "1915").mkdir(parents=True)
    (tmp_path / "commentary" / "sfs" / "1915" / "218.md").write_text(
        "---\nannotates: 1915:218\n---\n\nText.\n")
    (tmp_path / "patches" / "dv").mkdir(parents=True)
    (tmp_path / "patches" / "README.md").write_text("how patches work\n")
    return tmp_path


def _commands(wiki, *paths):
    return wiki_targets.commands(list(paths), wiki_root=wiki)


def test_a_news_edit_costs_one_page(wiki):
    """The case this was written for: editing sitenews.md must not walk the
    corpus. `lagen site generate sitenews` re-parses that one file and renders
    that one page."""
    assert _commands(wiki, "site/sitenews.md") == ["lagen site generate sitenews"]


def test_two_edits_give_two_targets_and_no_duplicates(wiki):
    assert _commands(wiki, "site/sitenews.md", "site/om/nytt.md",
                     "site/sitenews.md") == [
        "lagen site generate om/nytt", "lagen site generate sitenews"]


def test_a_concept_names_its_own_document(wiki):
    """Keyed on the file's `title:`, not on its filename -- concept files are
    named after the term, which is not the basefile in general."""
    assert _commands(wiki, "concept/fullmakt.md") == [
        "lagen begrepp generate Fullmakt"]


def test_media_has_nothing_to_parse_but_still_needs_the_copy(wiki):
    """`site/render.py` copies the media tree during generate, so a new
    screencast is published by a site run even though no file is parsed."""
    assert _commands(wiki, "site/media/oversikt.webm") == ["lagen site rebuild"]


def test_a_path_under_nothing_known_widens_to_everything(wiki):
    """The rule that keeps this safe. A file the tool does not recognise might
    feed any page, so it answers with the whole corpus rather than guessing --
    slow is recoverable, a stale page is not."""
    assert _commands(wiki, "RAPPORT-inaktuellt.md") == [WHOLE]
    assert _commands(wiki, "patches/README.md") == [WHOLE]


def test_the_whole_corpus_subsumes_every_narrower_command(wiki):
    """One unrecognised path in a push of twenty leaves one command, not
    twenty-one: running the corpus rebuild does everything the others would."""
    assert _commands(wiki, "site/sitenews.md", "concept/fullmakt.md",
                     "RAPPORT-inaktuellt.md") == [WHOLE]


def test_a_basefile_with_spaces_is_quoted(monkeypatch, wiki):
    """dv basefiles read "NJA 1990 s. 442" -- spaces and a period the patch
    filename (NJA_1990_s_442.patch) does not carry. These lines are run by a
    shell, so the argument has to survive it."""
    monkeypatch.setattr(wiki_targets, "targets",
                        lambda changed, wiki_root=None: [("dv", "NJA 1990 s. 442")])
    assert wiki_targets.commands(["patches/dv/NJA_1990_s_442.patch"]) == [
        "lagen dv generate 'NJA 1990 s. 442'"]


def test_a_patch_path_names_its_artifact_arithmetically():
    """`layout.patch` and `layout.artifact` are built from the same
    `relpath(source, basefile)`, so the mirror walks backwards in one step --
    no searching for the file that produced a given patch."""
    source, art = wiki_targets._artifact_of(
        "patches", "patches/dv/NJA_1990_s_442.desc")
    assert source == "dv"
    assert art == layout.ARTIFACT / "dom" / "NJA_1990_s_442.json"
    # a patch APPENDS its suffix; the plain and obfuscated variants both strip
    assert wiki_targets._artifact_of(
        "patches", "patches/dv/NJA_1990_s_442.rot18.patch")[1] == art
    assert wiki_targets._artifact_of(
        "patches", "patches/sfs/1962/700.patch")[1] == (
            layout.ARTIFACT / "sfs" / "1962" / "700.json")


def test_a_layer_path_replaces_the_suffix_rather_than_stripping_it():
    """`annstore.for_artifact` uses `with_suffix`, so `.json` became `.ann`
    -- the inverse puts `.json` back. Getting this the same way round as the
    patch rule would name a file that does not exist."""
    source, art = wiki_targets._artifact_of("ann", "ann/sfs/1962/700.ann")
    assert source == "sfs"
    assert art == layout.ARTIFACT / "sfs" / "1962" / "700.json"


def test_a_source_is_scanned_once_however_many_of_its_files_changed(monkeypatch, wiki):
    """The scan that remains is per source, not per changed file: a push
    touching forty patches of one source must pay for one pass. Before this it
    paid forty, each linear in the source's document count."""
    calls = []

    class FakeSource:
        def list_basefiles(self):
            calls.append(1)
            return ["NJA 1990 s. 442", "NJA 1990 s. 443"]

    monkeypatch.setattr(wiki_targets.build, "SOURCES", {"dv": FakeSource()})
    monkeypatch.setattr(wiki_targets, "_invert", lambda pairs: {})
    monkeypatch.setattr(wiki_targets.site_parse, "list_basefiles", lambda root: [])
    monkeypatch.setattr(wiki_targets.wiki_parse, "kommentar_index", lambda root: {})
    monkeypatch.setattr(wiki_targets.wiki_parse, "begrepp_index", lambda root: {})
    assert layout.SOURCE_DIR["dv"] == "dom"

    got = wiki_targets.targets(
        ["patches/dv/NJA_1990_s_442.patch", "patches/dv/NJA_1990_s_443.patch",
         "patches/dv/NJA_1990_s_442.desc"], wiki_root=wiki)
    assert len(calls) == 1, "scanned the source %d times, want 1" % len(calls)
    assert ("dv", "NJA 1990 s. 442") in got
    assert ("dv", "NJA 1990 s. 443") in got


def test_a_source_rebuild_subsumes_that_source_s_document_commands(wiki):
    """A push that adds a screencast and edits a news item is one command, not
    three: `lagen site rebuild` already does what each `lagen site generate`
    would, and running both is pure waste. It does not reach across sources --
    a site rebuild says nothing about begrepp."""
    assert _commands(wiki, "site/media/oversikt.webm", "site/sitenews.md",
                     "site/om/nytt.md") == ["lagen site rebuild"]
    assert _commands(wiki, "site/media/oversikt.webm",
                     "concept/fullmakt.md") == [
        "lagen begrepp generate Fullmakt", "lagen site rebuild"]
