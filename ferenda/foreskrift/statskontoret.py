"""Statskontoret's STKFA and predecessor ESVFA regulations.

The agency publishes both series through one current EA-regelverket. The
``statskontoret-scraper`` dependency owns that site's crawl and returns typed
HTML sections. This adapter only maps those records onto the föreskrift store.
"""

from pathlib import Path

from statskontoret_scraper import fetch_foreskrifter

from ..lib import compress
from ..lib.harvest import write_record
from ..lib.util import basefile_slug as slug
from ..lib.util import record_path
from .harvest import DocRef

SERIES = frozenset({"esvfa", "stkfa"})


def enumerate_regulations(_session, agency):
    """Return the current STKFA/ESVFA register newest-first."""
    records = fetch_foreskrifter()

    def order(record):
        year, serial = str(record["basefile"]).split("/", 1)[1].split(":", 1)
        return int(year), int(serial)

    for record in sorted(records, key=order, reverse=True):
        fs = str(record["fs"])
        assert fs in SERIES, "unexpected EA-regelverket series %r" % fs
        yield DocRef(
            basefile=str(record["basefile"]),
            identifier=str(record["identifier"]),
            url=str(record["url"]),
            title=str(record["title"]),
            fs=fs if fs != agency.fs else None,
            extra={
                "publisher": str(record["publisher"]),
                "sections": record["sections"],
                "updated_at": record.get("updated_at"),
            },
        )


def resolve(_session, agency, ref, root, delay=0.5, *, log=print, rejects=None):
    """Store the dependency's typed HTML as a current consolidation."""
    fs = ref.fs or agency.fs
    sections = ref.extra["sections"]
    assert sections, "%s has no regulation sections" % ref.identifier
    name = "%s-consolidation.html" % slug(ref.basefile)
    compress.write_download(
        Path(root) / fs / name,
        "<main>\n%s\n</main>" % "\n".join(str(s["html"]) for s in sections),
    )
    record = {
        "fs": fs,
        "basefile": ref.basefile,
        "identifier": ref.identifier,
        "title": ref.title,
        "publisher": ref.extra["publisher"],
        "updated_at": ref.extra.get("updated_at"),
        "url": ref.url,
        "files": {
            "regulation": None,
            "consolidation": [{"name": name, "url": ref.url}],
            "amendment": [],
            "memo": [],
            "attachment": [],
        },
    }
    write_record(record_path(root, fs, ref.basefile), record)
    return record
