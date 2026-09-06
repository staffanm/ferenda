"""Budgets on a downloaded archive: what it is allowed to expand to."""

import io
import zipfile

import pytest

from ferenda.forarbete import legacy_formats
from ferenda.lib import archive


def _zip(members, **kwargs):
    """An in-memory zip of `{name: bytes}`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", **kwargs) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


def test_a_zip_bomb_shape_is_refused_before_a_byte_expands():
    # 8 MB of one repeated byte deflates to a few kB -- the ratio a bomb needs
    bomb = _zip({"payload": b"\0" * (8 * 1024 * 1024)},
                compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(archive.ArchiveTooLarge, match="expands"):
        archive.open_zip(bomb)


def test_too_many_members_is_refused():
    many = _zip({"m%d" % n: b"x" for n in range(20)})
    with pytest.raises(archive.ArchiveTooLarge, match="declares 20 members"):
        archive.open_zip(many, max_members=10)


def test_a_member_over_the_budget_is_refused():
    big = _zip({"one": b"x" * 5000})
    with pytest.raises(archive.ArchiveTooLarge, match="declares 5000 bytes"):
        archive.open_zip(big, max_member_bytes=1000)


def test_the_total_is_budgeted_even_when_each_member_fits():
    several = _zip({"a": b"x" * 400, "b": b"y" * 400, "c": b"z" * 400})
    with pytest.raises(archive.ArchiveTooLarge, match="bytes of members"):
        archive.open_zip(several, max_member_bytes=1000, max_total_bytes=1000)


def test_a_member_name_that_walks_upward_is_refused():
    escape = _zip({"../../etc/passwd": b"x"})
    with pytest.raises(archive.UnsafeArchiveMember, match="walks out"):
        archive.open_zip(escape)


def test_an_absolute_member_name_is_refused():
    absolute = _zip({"/etc/passwd": b"x"})
    with pytest.raises(archive.UnsafeArchiveMember, match="absolute path"):
        archive.open_zip(absolute)


def test_an_ordinary_archive_reads_as_before():
    ok = _zip({"word/document.xml": b"<w/>", "other.bin": b"12345"})
    with archive.open_zip(ok) as zf:
        assert archive.read(zf, "word/document.xml") == b"<w/>"
        assert sorted(zf.namelist()) == ["other.bin", "word/document.xml"]


def test_read_bounds_the_member_it_is_asked_for():
    ok = _zip({"one": b"x" * 500})
    with archive.open_zip(ok) as zf:
        with pytest.raises(archive.ArchiveTooLarge):
            archive.read(zf, "one", max_bytes=100)


def test_a_docx_with_a_dtd_does_not_resolve_its_entity(tmp_path):
    """A .docx is a downloaded file. Its document.xml is parsed with the same
    hardened parser the remote dokumentstatus XML gets, so a declared entity
    stays unexpanded instead of reading a local file."""
    docx = tmp_path / "leak.docx"
    document = ("<?xml version='1.0'?>"
                "<!DOCTYPE w:document ["
                "<!ENTITY leak SYSTEM 'file:///etc/passwd'>]>"
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main">'
                "<w:p><w:t>&leak;</w:t></w:p></w:document>")
    docx.write_bytes(_zip({"word/document.xml": document.encode()}).getvalue())
    assert legacy_formats._docx_texts(docx) == [""]
