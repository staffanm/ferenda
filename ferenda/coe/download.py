"""Harvester for the Council of Europe Treaty Office.

The Treaty Office portal (www.coe.int) answers unattended clients with a
Cloudflare challenge, but the portal's own data layer does not: the React app
on the full-list page reads an anonymous JSON web service
(conventions-ws.coe.int) whose token is embedded in the public page, and the
official English texts are ordinary PDFs on rm.coe.int.  One search POST
returns every treaty with its metadata and official-text links, so a harvest
is that call plus one PDF per treaty.  The web service's TLS still offers a
legacy small DH key, hence ``mount_legacy_tls``.
"""

import re
import time
from pathlib import Path

from ..lib import compress
from ..lib.coe import treaty_number
from ..lib.harvest import (
    HarvestWatermark,
    ItemKey,
    flat_path,
    select_one,
    store_record,
    walk,
)
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, mount_legacy_tls, request
from ..lib.util import document_extension, normalize_space

PORTAL = "https://www.coe.int"
FULL_LIST = PORTAL + "/en/web/conventions/full-list2"
DETAIL = FULL_LIST + "?module=treaty-detail&treatynum=%s"
WS = "https://conventions-ws.coe.int/WS_LFRConventions/"
SEARCH_ENDPOINT = WS + "api/traites/search"
LIEUX_ENDPOINT = WS + "api/conventions/getLieux"
# the anonymous API key the public full-list page hands every browser
# (window.conventions_api_key); if the service starts answering 401,
# refresh it from the page source
WS_TOKEN = "hfghhgp2q5vgwg1hbn532kw71zgtww7e"
# the React app's empty search form: every treaty, English labels
SEARCH_ALL = {"CodePays": None, "NumsSte": [], "AnneeOuverture": None,
              "AnneeVigueur": None, "CodeLieuSTE": None, "CodeMatieres": [],
              "TitleKeywords": [], "langue": "en"}
# the designation closing every title -- '(ETS No. 005)', '(CETS 230)' --
# plus the footnote marker some carry: '... (ETS No. 022) (*)'
RE_TITLE_REF = re.compile(
    r"\s*\((C?ETS)\s+(?:No\.?\s*)?(\d{1,3}[A-Z]?)\)\s*(?:\(\*+\)\s*)?$", re.I)


def make_ws_session():
    session = make_session(USER_AGENT)
    mount_legacy_tls(session, WS)
    session.headers["token"] = WS_TOKEN
    session.headers["Accept"] = "application/json"
    return session


def search_treaties(session):
    treaties = request(session, "POST", SEARCH_ENDPOINT, parse_json=True,
                       timeout=120, json=SEARCH_ALL)
    if not treaties:
        raise ValueError("Treaty Office web service returned no treaties")
    return treaties


def opening_places(session):
    """Code_lieu_ste -> place name ('18' -> 'Rome'), from the service's own
    lookup table (which wants langue=ENG where search wants langue=en)."""
    places = request(session, "GET", LIEUX_ENDPOINT, parse_json=True,
                     timeout=60, params={"langue": "ENG"})
    return {int(place["Key"]): place["Value"] for place in places}


def _date(value):
    return value[:10] if value else None       # '1950-11-04T00:00:00' -> date


def treaty_record(ws, places):
    """One stored metadata record from a web-service search row."""
    number = treaty_number(ws["Numero_traite"])
    title = normalize_space(ws["Libelle_titre_ENG"])
    match = RE_TITLE_REF.search(title)
    if not match:
        raise ValueError("treaty %s title carries no ETS/CETS designation: %r"
                         % (number, title))
    if not ws["Lien_pdf_traite_ENG"]:
        raise ValueError("treaty %s carries no official English PDF" % number)
    return {
        "number": number,
        "title": RE_TITLE_REF.sub("", title),
        "opening_date": _date(ws["Date_ste"]),
        "opening_place": places.get(ws["Code_lieu_ste"]),
        "entry_into_force": _date(ws["Date_vigueur_ste"]),
        "reference": "%s No. %s" % (match.group(1).upper(), match.group(2)),
        "source_url": DETAIL % number,
        "text_url": ws["Lien_pdf_traite_ENG"],
    }


def record_path(root, number):
    return flat_path(root, treaty_number(number))


def body_path(root, record):
    return Path(root) / record["file"]


def resolve(root, record, session, full=False, delay=0.3):
    old_path = record_path(root, record["number"])
    old = compress.read_json(old_path, default=None)
    if full or old is None or not compress.exists(Path(root) / old["file"]):
        response = request(session, "GET", record["text_url"], timeout=180)
        if document_extension(response.content) != ".pdf":
            raise ValueError("official text for %s is not a PDF (Content-Type %r)"
                             % (record["number"],
                                response.headers.get("Content-Type")))
        filename = record["number"] + ".pdf"
        compress.write_download(Path(root) / filename, response.content)
        record = {**record, "file": filename}
        time.sleep(delay)
    else:
        record = {**record, "file": old["file"]}
    # `full` is deliberately not passed on: a --force run re-verifies the PDF over
    # the network, but a record that came back identical must still not be
    # rewritten -- its mtime is what the downstream freshness watermarks key on.
    return store_record(old_path, record)


def list_basefiles(root):
    return compress.list_stems(root)                    # skips .watermark.json


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    root = Path(root)
    session = make_ws_session()
    places = opening_places(session)
    records = [treaty_record(ws, places) for ws in search_treaties(session)]
    if only:
        record = select_one(records, lambda record: record["number"],
                            treaty_number(only),
                            "Treaty Office lists no treaty %s")
        return 1, int(resolve(root, record, session, full=full, delay=delay))

    # newest first, so the watermark lookahead meets fresh treaties before the
    # long already-downloaded backlog. Treaty publication is low-volume; a
    # 365-day safety window also catches a late official-text replacement.
    records.sort(key=lambda record: record["opening_date"] or "", reverse=True)
    watermark = HarvestWatermark(root / ".watermark.json",
                                 lookahead_limit=20, safety_days=365)

    def item_key(record):
        path = record_path(root, record["number"])
        downloaded = False
        if compress.exists(path):
            data = compress.read_json(path)
            downloaded = compress.exists(Path(root) / data["file"])
        return ItemKey(record["number"], downloaded, record["opening_date"])

    result = walk(
        records,
        resolve=lambda record: resolve(root, record, session,
                                       full=full, delay=delay),
        item_key=item_key,
        watermark=watermark,
        full=full,
        limit=limit,
        scope="coe",
        count_label="changed",
        total=len(records),
        log=log,
    )
    return result.seen, result.new
