"""Harvester for the ICRC IHL "Treaties, States Parties and Commentaries" base.

The public site (ihl-databases.icrc.org) is a React app, but it reads an
anonymous Drupal 10 JSON:API that answers unattended clients directly.  One
`node--treaty` list call enumerates every instrument (paged 50 at a time); one
per-treaty call with the relationship graph included returns the whole
self-contained document -- metadata, the authentic article text
(`field_treaty_content`), the per-state participation
(`field_treaty_state_parties`), depositary, topics and languages.  A harvest is
that list call plus one included fetch per treaty; the stored record is the raw
JSON:API envelope, so parse never touches the network.
"""

import json
from pathlib import Path

from ..lib import compress
from ..lib.harvest import HarvestWatermark, ItemKey, walk
from ..lib.net import HARVESTER_UA as USER_AGENT
from ..lib.net import make_session, request

SITE = "https://ihl-databases.icrc.org"
API = SITE + "/en/jsonapi/node/treaty"
PAGE_SIZE = 50                              # JSON:API caps a page at 50
# the relationship graph one treaty needs to stand alone offline: the article
# text, the participants (with their country term), the depositary/topics/
# language taxonomy terms and the official PDF file entity.
INCLUDE = ",".join((
    "field_treaty_content",
    "field_treaty_state_parties",
    "field_treaty_state_parties.field_participant_country",
    "field_treaty_depositary",
    "field_treaty_topics",
    "field_treaty_authentic_text",
    "field_treaty_document",
))
# the enumeration only needs the identity and the change stamp that drives the
# incremental walk (a new ratification advances the node's `changed`)
LIST_FIELDS = "field_treaty_number,field_treaty_date_of_adoption,changed"


def make_api_session():
    session = make_session(USER_AGENT)
    session.headers["Accept"] = "application/vnd.api+json"
    return session


def record_path(root, number):
    return Path(root) / (str(number) + ".json")


def _changed(envelope):
    return envelope["data"][0]["attributes"].get("changed")


def enumerate_treaties(session):
    """Every treaty as {number, date, changed}, deduplicated by number.

    Paged on the stable, unique treaty number -- NOT on `changed`: offset paging
    over a mutable, tie-prone sort key repeats rows at page boundaries (and can
    just as silently skip them), so ordering the walk newest-first is left to
    `sync`. A defensive dedup by number absorbs any residual boundary repeat."""
    records, offset = {}, 0
    while True:
        envelope = request(session, "GET", API, parse_json=True, timeout=120,
                           params={"page[limit]": PAGE_SIZE,
                                   "page[offset]": offset,
                                   "sort": "field_treaty_number",
                                   "fields[node--treaty]": LIST_FIELDS})
        rows = envelope.get("data") or []
        for node in rows:
            attributes = node["attributes"]
            number = str(attributes["field_treaty_number"])
            records[number] = {
                "number": number,
                "date": attributes.get("field_treaty_date_of_adoption"),
                "changed": attributes.get("changed")}
        offset += len(rows)
        if not rows or offset >= envelope["meta"]["count"]:
            return list(records.values())


def fetch_treaty(session, number):
    """The self-contained JSON:API envelope for one treaty -- the stored record."""
    envelope = request(session, "GET", API, parse_json=True, timeout=180,
                       params={"filter[field_treaty_number]": number,
                               "include": INCLUDE, "page[limit]": 1})
    if not envelope.get("data"):
        raise ValueError("ICRC lists no treaty %s" % number)
    return envelope


def resolve(session, root, record, full=False):
    """Refetch a treaty when it is new, forced, or its `changed` stamp advanced;
    otherwise leave the stored envelope untouched.  Returns whether it changed."""
    path = record_path(root, record["number"])
    stored = json.loads(compress.read_text(path)) if compress.exists(path) else None
    if not (full or stored is None or _changed(stored) != record["changed"]):
        return False
    envelope = fetch_treaty(session, record["number"])
    compress.write_download(path, json.dumps(envelope, ensure_ascii=False, indent=2))
    return True


def list_basefiles(root):
    return sorted(path.stem for path in compress.glob(root, "*.json")
                  if not path.name.startswith("."))     # skip .watermark.json


def sync(root, full=False, only=None, limit=None, delay=0.3, log=print):
    root = Path(root)
    session = make_api_session()
    records = enumerate_treaties(session)
    if only:
        record = next((r for r in records if r["number"] == str(only)), None)
        if record is None:
            raise ValueError("ICRC lists no treaty %s" % only)
        return 1, int(resolve(session, root, record, full=full))

    # newest-changed first, so the watermark lookahead meets freshly-updated
    # treaties (a new ratification advances `changed`) before the downloaded
    # backlog; enumerate pages on the stable number, so the order is set here.
    records.sort(key=lambda record: record["changed"] or "", reverse=True)
    watermark = HarvestWatermark(root / ".watermark.json",
                                 lookahead_limit=20, safety_days=30)

    def item_key(record):
        path = record_path(root, record["number"])
        downloaded = (compress.exists(path)
                      and _changed(json.loads(compress.read_text(path)))
                      == record["changed"])
        # the watermark's safety window parses this as a bare date, so pass the
        # date portion; the full `changed` timestamp drives change-detection above
        changed_date = (record["changed"] or "")[:10] or None
        return ItemKey(record["number"], downloaded, changed_date)

    result = walk(
        records,
        resolve=lambda record: resolve(session, root, record, full=full),
        item_key=item_key,
        watermark=watermark,
        full=full,
        limit=limit,
        scope="icrc",
        count_label="changed",
        total=len(records),
        log=log,
    )
    return result.seen, result.new
