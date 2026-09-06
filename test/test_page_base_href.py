"""page.html carries no static `<base>`: a head script adds one, pointing at
the canonical address, only when the page is loaded at a path other than its
own (a subdomain landing serving the document's bytes at "/"). At its own
path -- the public host, a localhost serve -- the page stays on the host that
served it."""

from ferenda.lib.page import page_context
from ferenda.lib.tpl import ENV

SCRIPT = "if(c&&new URL(c.href).pathname!==location.pathname)"


def _head(**kwargs):
    ctx = page_context("Title", "Kind", "meta", body="body", **kwargs)
    html = ENV.get_template("page.html").render(ctx)
    return html[:html.index("</head>")]


def test_a_document_page_carries_the_base_script_after_its_canonical():
    head = _head(doc_uri="https://lagen.nu/1942:740")
    assert "<base " not in head
    canonical = head.index('<link rel="canonical" href="https://lagen.nu/1942:740">')
    assert canonical < head.index(SCRIPT) < head.index('href="/style.css"')


def test_the_canonical_strips_a_fragment_off_the_document_uri():
    assert 'href="https://lagen.nu/1942:740">' in _head(
        doc_uri="https://lagen.nu/1942:740#K1P3a")


def test_a_solo_page_has_neither_canonical_nor_base_script():
    head = _head()
    assert "canonical" not in head and SCRIPT not in head
