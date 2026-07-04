"""The edit cart + commit engine (accommodanda/api/editcart.py): drafts persist
per user, a checkout is one git commit authored as the editor, and a hunk that
went stale under a draft fails the commit rather than clobbering."""

import subprocess

import pytest

from accommodanda.api import editcart, editcontent
from accommodanda.api.auth import Editor
from accommodanda.wiki import parse as wiki


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git-initialised content repo with one committed commentary file, wired
    as WIKI_ROOT, plus an isolated cart store."""
    root = tmp_path / "wiki"
    (root / "commentary" / "sfs" / "1915").mkdir(parents=True)
    f = root / "commentary" / "sfs" / "1915" / "218.md"
    f.write_text("---\nannotates: 1915:218\n---\n## 1 §\n\nUrsprunglig text.\n",
                 encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Seed")
    _git(root, "config", "user.email", "seed@example.org")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    monkeypatch.setattr("accommodanda.config.WIKI_ROOT", root)
    monkeypatch.setattr(editcart, "EDITS", tmp_path / "edits")
    wiki.kommentar_index.cache_clear()
    wiki.begrepp_index.cache_clear()
    yield root
    wiki.kommentar_index.cache_clear()


EDITOR = Editor("anna", {"name": "Anna Ek", "email": "anna@example.org"})
REGION = editcontent.Region("kommentar", "1915:218", "P1")


def test_upsert_and_cart(repo):
    assert editcart.upsert("anna", REGION, "## 1 §\n\nNy text.\n") == 1
    drafts = editcart.cart("anna")
    assert len(drafts) == 1 and drafts[0]["key"] == "kommentar:1915:218#P1"


def test_noop_edit_does_not_cart(repo):
    # text identical to disk carts nothing (and clears any existing draft)
    assert editcart.upsert("anna", REGION, "## 1 §\n\nUrsprunglig text.\n") == 0


def test_upsert_replaces_same_region(repo):
    editcart.upsert("anna", REGION, "## 1 §\n\nEtt.\n")
    assert editcart.upsert("anna", REGION, "## 1 §\n\nTvå.\n") == 1
    assert "Två." in editcart.cart("anna")[0]["new_text"]


def test_commit_writes_file_and_attributes_author(repo):
    editcart.upsert("anna", REGION, "## 1 §\n\nAnnas nya kommentar.\n")
    result = editcart.commit(EDITOR, "förbättra 1 §")
    assert result["changes"] == [{"kind": "kommentar", "basefile": "1915:218"}]
    # the file changed and the cart is now empty
    assert "Annas nya kommentar." in \
        (repo / "commentary" / "sfs" / "1915" / "218.md").read_text()
    assert editcart.cart("anna") == []
    # one new commit, authored *and* committed as Anna
    assert _git(repo, "log", "-1", "--format=%an|%ae|%cn|%ce|%s") == \
        "Anna Ek|anna@example.org|Anna Ek|anna@example.org|förbättra 1 §"
    assert result["sha"] == _git(repo, "rev-parse", "HEAD")


def test_new_file_commit(repo):
    region = editcontent.Region("kommentar", "2009:400", "P1")
    editcart.upsert("anna", region, "## 1 §\n\nOffentlighetsprincipen.\n")
    editcart.commit(EDITOR, "add 2009:400 kommentar")
    # the freshly created file is tracked and its first commit is Anna's
    assert _git(repo, "log", "-1", "--format=%an", "--",
                "commentary/sfs/2009/400.md") == "Anna Ek"


def test_stale_hunk_conflicts_without_writing(repo):
    editcart.upsert("anna", REGION, "## 1 §\n\nAnnas version.\n")
    # someone else edits the same region on disk after the draft was carted
    editcontent.write(REGION, "## 1 §\n\nNågon annans version.\n")
    with pytest.raises(editcart.Conflict) as exc:
        editcart.commit(EDITOR, "should abort")
    assert exc.value.keys == ["kommentar:1915:218#P1"]
    # nothing new committed and the cart is untouched (reconcilable)
    assert _git(repo, "log", "-1", "--format=%s") == "seed"
    assert len(editcart.cart("anna")) == 1


def test_empty_cart_and_message_rejected(repo):
    with pytest.raises(ValueError):
        editcart.commit(EDITOR, "nothing carted")
    editcart.upsert("anna", REGION, "## 1 §\n\nX.\n")
    with pytest.raises(ValueError):
        editcart.commit(EDITOR, "   ")


def test_discard(repo):
    editcart.upsert("anna", REGION, "## 1 §\n\nX.\n")
    assert editcart.discard("anna", "kommentar:1915:218#P1") == 0
    assert editcart.cart("anna") == []


def test_carts_are_per_user(repo):
    editcart.upsert("anna", REGION, "## 1 §\n\nA.\n")
    assert editcart.cart("bo") == []            # bo sees nothing of anna's
