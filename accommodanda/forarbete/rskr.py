"""Downloader for riksdagsskrivelser (doktyp=rskr) from data.riksdagen.se,
driving the doctype-agnostic dokumentlista engine in `riksdagen.py`.

A riksdagsskrivelse is the chamber's formal letter announcing its decision to
the government -- the last hop of the prop -> bet -> rskr chain every SFS
register cites per amendment ("rskr. 2007/08:159"). The FORARBETEN citation
grammar (`lib/lagrum.py`) already mints those refs as
`https://lagen.nu/rskr/<riksmöte>:<nr>`, so keying this vertical the same way
(`basefile = "<rm>:<beteckning>"`) makes them resolve to real catalog
documents, exactly like bet.

Unlike bet, the body is NOT the filbilaga PDF: an rskr is a few sentences of
boilerplate ending in the talman's signature (countersigned by a tjänsteman
in the modern layout) -- the committer identity the sfs history-as-git export
mines -- and the API's own small HTML rendering (`dokument_url_html`) carries
all of it. No page-precise citations point *into* an rskr, so the PDF's
printed pages add nothing; we store the HTML and skip the filbilaga entirely.
That also removes bet's planned-placeholder upgrade cycle: an rskr is written
after the decision it records, so every feed entry is published and final,
and the watermark gate runs with the default window.

Stored under `site/data/downloaded/forarbete/rskr/`: one `<slug>.json` record
(type, basefile, identifier, title, date, url, dok_id, files) plus the
`<slug>.html` body. The coverage floor is riksmöte 1971 (`FIRST_RIKSMOTE` in
riksdagen.py, shared riksmöte-sliced backfill); the feed serves ~50k rskr from
there on, ~400 per riksmöte.
"""

import json
import time
from pathlib import Path

from ..lib import compress
from ..lib.harvest import HarvestWatermark
from ..lib.net import request
from ..lib.util import basefile_slug, record_path
from . import riksdagen
from .download import has_live_record

LISTING = (riksdagen.API + "/dokumentlista/?doktyp=rskr&utformat=json"
           "&sort=datum&sortorder=desc&sz=200")
TYPE = "rskr"


def descriptor(entry):
    """One dokumentlista entry -> a record descriptor. `basefile =
    "<rm>:<beteckning>"` (e.g. "2007/08:159") and `identifier = "Rskr.
    <basefile>"` match the FORARBETEN grammar's rskr URIs and the register's
    citation form. A missing field is a malformed remote feed entry, raised as
    ValueError -- recorded per-document in the shared walk, never fatal to it
    (rule:errors-drive-retry-use-raise)."""
    basefile = riksdagen.basefile_of(entry)
    missing = [k for k in ("titel", "datum", "dokument_url_html", "dok_id")
               if k not in entry]
    if missing:
        raise ValueError("%s: malformed dokumentlista entry, missing %s"
                         % (basefile, ", ".join(missing)))
    return {"type": TYPE, "basefile": basefile,
            "identifier": "Rskr. " + basefile,
            "title": entry["titel"], "date": entry["datum"],
            "url": riksdagen._https(entry["dokument_url_html"]),
            "dok_id": entry["dok_id"], "files": []}


def download_document(session, root, entry, delay):
    """Store one riksdagsskrivelse: the record JSON and the API's HTML body
    under `root/rskr/<slug>.html`. Returns the record."""
    record = descriptor(entry)
    html = request(session, "GET", record["url"]).text
    # load-bearing validation of untrusted remote bytes: an empty body would
    # freeze a signer-less document forever (rule:errors-drive-retry-use-raise)
    if not html.strip():
        raise ValueError("%s: empty rskr body at %s"
                         % (record["basefile"], record["url"]))
    name = basefile_slug(record["basefile"]) + ".html"
    compress.write_download(Path(root) / TYPE / name, html)
    record["files"] = [name]
    time.sleep(delay)
    compress.write_download(record_path(root, TYPE, record["basefile"]),
                            json.dumps(record, ensure_ascii=False, indent=2))
    return record


def _currency(root, basefile, entry):
    """"final" when the record is stored, else None. No provisional state:
    every rskr feed entry is a published document with an HTML body."""
    return "final" if has_live_record(root, TYPE, basefile) else None


def sync(root, full=False, delay=0.5, log=print, riksmote=None):
    """Download riksdagsskrivelser (doktyp=rskr) into `root/rskr/` -- the
    shared riksdagen harvest lifecycle (see `riksdagen.harvest`/`sync`), with
    every entry published and final. Returns (seen, new)."""
    watermark = HarvestWatermark(Path(root) / TYPE / riksdagen.WATERMARK)
    return riksdagen.harvest(root, typ=TYPE, listing=LISTING,
                             fetch=download_document, currency=_currency,
                             published=lambda entry: True,
                             watermark=watermark, full=full, delay=delay,
                             log=log, riksmote=riksmote)
