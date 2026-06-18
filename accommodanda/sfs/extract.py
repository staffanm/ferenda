"""Extract statute body text from downloaded rkrattsbaser HTML."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..lib.errors import SkipDocument


def sniff_encoding(raw):
    # only utf-8 pages use the html5 doctype; archival pages are latin-1
    return "utf-8" if b"<!DOCTYPE html>" in raw[:256] else "latin-1"


def extract_body(path, keep_expired=True):
    """Return the statute body text (the part below the header) as a
    string with LF line separators."""
    with open(path, "rb") as fp:
        raw = fp.read()
    encoding = sniff_encoding(raw)
    rawtext = raw.decode(encoding)

    # expired statutes are published with an expiry note in the header;
    # lagen.nu keeps them (they remain reachable as historical law)
    if not keep_expired:
        for needle in ('<span class="bold">Upphävd:</span> ',
                       '<span class="bold">Övrigt:</span> Utgår genom SFS'):
            idx = rawtext.find(needle, 0, 10000)
            if idx != -1:
                datestr = rawtext[idx + len(needle):idx + len(needle) + 10]
                if (not re.match(r"\d+-\d+-\d+$", datestr) or
                        datetime.strptime(datestr, "%Y-%m-%d") <
                        datetime.today()):
                    raise SkipDocument("expired (%s)" % needle.strip())

    soup = BeautifulSoup(rawtext, "lxml")
    if encoding == "utf-8":
        content = soup.find("div", "search-results-content")
        if not content:
            errnode = soup.find("div", "info-section-part-desc")
            if errnode and "Ett fel har inträffat" in errnode.text:
                raise SkipDocument("removed: %s" % errnode.text.strip())
            raise SkipDocument("no div.search-results-content")
        body = content.find("div", "body-text")
        if not body:
            raise SkipDocument("no div.body-text")
        if body.string:
            txt = str(body.string)
        elif body.text.strip():
            # unescaped angle brackets in the source make BS4 see fake
            # elements; salvage the text content
            txt = body.text
        else:
            txt = "(Författningstext saknas)"
    else:
        # archival page format: statute text in <pre>, header above an <hr>
        pre = soup.find("pre")
        if pre is None:
            raise SkipDocument("archival page without <pre>")
        hr = pre.find("hr")
        if hr is None:
            txt = pre.text
        else:
            txt = "".join(
                piece.get_text() if hasattr(piece, "get_text") else str(piece)
                for piece in hr.next_siblings)
    return txt.replace("\r", "")


# 2010:110 and others miss a blank line before underavdelning headings
re_missing_newline = re.compile(r"(\.)\n([IV]+  )")


def sanitize_body(text):
    return re_missing_newline.sub("\\1\n\n\\2", text)
