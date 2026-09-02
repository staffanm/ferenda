"""`page_context`'s `doc_uri` anchors page.html's `<base href>` to the
document's own served path, so a bare `#anchor` link (the TOC, a pilcrow
permalink) resolves against the document instead of the site root."""

from ferenda.lib.page import page_context
from ferenda.lib.tpl import ENV


def _base_href(**kwargs):
    ctx = page_context("Title", "Kind", "meta", body="body", **kwargs)
    html = ENV.get_template("page.html").render(ctx)
    start = html.index('<base href="') + len('<base href="')
    return html[start:html.index('"', start)]


def test_base_href_anchors_to_the_document_own_served_path():
    assert _base_href(doc_uri="https://lagen.nu/1942:740") == \
        "https://ferenda.lagen.nu/1942:740"


def test_base_href_strips_a_fragment_off_the_document_uri():
    assert _base_href(doc_uri="https://lagen.nu/1942:740#K1P3a") == \
        "https://ferenda.lagen.nu/1942:740"


def test_base_href_stays_at_the_site_root_for_a_solo_page():
    assert _base_href() == "https://ferenda.lagen.nu"
