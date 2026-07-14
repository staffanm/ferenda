"""ICRC JSON:API treaty envelopes to :class:`Treaty` artifacts.

The stored record is the raw per-treaty JSON:API envelope (``data`` plus the
``included`` relationship graph), so parse is pure and offline: resolve the
content paragraphs into an article tree, the participant paragraphs into the
states-parties list, and the taxonomy terms into depositary/topics/languages.
"""

import json
import re

from bs4 import BeautifulSoup

from ..lib import compress
from ..lib.util import normalize_space
from .download import record_path
from .model import (
    HEADING_SECTIONS,
    RE_ARTICLE_ORDINAL,
    TEXT_SECTIONS,
    Party,
    Provision,
    Treaty,
)

RE_ANNEX_ORDINAL = re.compile(r"Annex\s+([IVXLCDM\d]+)", re.I)


def _index(envelope):
    """(type, id) -> resource, over the envelope's included graph."""
    return {(resource["type"], resource["id"]): resource
            for resource in envelope.get("included", [])}


def _related(node, index, field):
    """The included resources a node's relationship points at, in order."""
    data = node.get("relationships", {}).get(field, {}).get("data")
    if data is None:
        return []
    refs = data if isinstance(data, list) else [data]
    return [index[(ref["type"], ref["id"])] for ref in refs
            if (ref["type"], ref["id"]) in index]


def _text(value):
    """A JSON:API text field's markup: a formatted field is an object carrying
    `processed`/`value`, a plain field the bare string."""
    if isinstance(value, dict):
        return value.get("processed") or value.get("value") or ""
    return value or ""


def _plain(markup):
    return normalize_space(BeautifulSoup(_text(markup), "html.parser")
                           .get_text(" ", strip=True))


def _paragraphs(markup):
    """An article body's HTML split into its stycken -- one per block, or the
    whole text as a single paragraph when the body carries no block markup."""
    soup = BeautifulSoup(_text(markup), "html.parser")
    blocks = [normalize_space(node.get_text(" ", strip=True))
              for node in soup.find_all(["p", "li"])]
    blocks = [block for block in blocks if block]
    if blocks:
        return blocks
    whole = normalize_space(soup.get_text(" ", strip=True))
    return [whole] if whole else []


def _provision(paragraph, annex_serial):
    attributes = paragraph["attributes"]
    section = attributes.get("field_treaty_content_section")
    heading = attributes.get("field_treaty_content_title") or ""
    if section in HEADING_SECTIONS:
        return Provision("rubrik", section, heading), annex_serial
    pre_title = attributes.get("field_treaty_content_pre_title") or heading
    if section == "Article":
        match = RE_ARTICLE_ORDINAL.search(pre_title)
        ordinal = match.group(1).lstrip("0") if match else None
        fragment = "A%s" % ordinal if ordinal else "A"
    elif section == "Annex":
        annex_serial += 1
        match = RE_ANNEX_ORDINAL.search(pre_title)
        ordinal = match.group(1) if match else str(annex_serial)
        fragment = "Annex%d" % annex_serial
    else:                                    # Preamble / Testimonium
        ordinal = None
        fragment = section
    return Provision("artikel", section, heading, fragment=fragment,
                     ordinal=ordinal,
                     paragraphs=_paragraphs(attributes.get("field_treaty_content_content"))), \
        annex_serial


def _provisions(node, index):
    provisions, annex_serial = [], 0
    for paragraph in _related(node, index, "field_treaty_content"):
        section = paragraph["attributes"].get("field_treaty_content_section")
        if section not in TEXT_SECTIONS and section not in HEADING_SECTIONS:
            continue                         # ToC / Foreword / Introduction …
        provision, annex_serial = _provision(paragraph, annex_serial)
        provisions.append(provision)
    return provisions


def _parties(node, index):
    # a participation with no resolvable state is dropped (see below), so the
    # `statesParties` count this feeds is "participations naming a state" -- an
    # unnamed participation is a malformed ICRC row, not a party to omit-and-hide
    parties = []
    for participant in _related(node, index, "field_treaty_state_parties"):
        countries = _related(participant, index, "field_participant_country")
        if not countries:
            continue                         # a participation with no state is unusable
        attributes = participant["attributes"]
        reservation = attributes.get("field_participant_text")
        parties.append(Party(
            country=countries[0]["attributes"]["name"],
            action=attributes["field_participant_action"],
            date=attributes.get("field_participant_date_notif"),
            reservation=_plain(reservation) or None if reservation else None))
    return parties


def _names(node, index, field):
    return [term["attributes"]["name"] for term in _related(node, index, field)]


def _in_force(value):
    return {"yes": True, "no": False}.get(value)


def parse_envelope(envelope):
    node = envelope["data"][0]
    index = _index(envelope)
    attributes = node["attributes"]
    depositary = _names(node, index, "field_treaty_depositary")
    return Treaty(
        number=attributes["field_treaty_number"],
        title=normalize_space(attributes["title"]),
        short_title=normalize_space(attributes.get("field_short_title") or "") or None,
        unid=attributes.get("field_treaty_unid"),
        adoption_date=attributes.get("field_treaty_date_of_adoption"),
        entry_into_force=attributes.get("field_treaty_entry_in_force"),
        in_force=_in_force(attributes.get("field_treaty_in_force")),
        treaty_type=attributes.get("field_treaty_type"),
        historical=bool(attributes.get("field_historical")),
        slug=attributes.get("field_path"),
        depositary=depositary[0] if depositary else None,
        topics=_names(node, index, "field_treaty_topics"),
        languages=_names(node, index, "field_treaty_authentic_text"),
        summary=_plain(attributes.get("field_treaty_presentation")) or None,
        provisions=_provisions(node, index),
        parties=_parties(node, index),
    )


def parse(basefile, root):
    envelope = json.loads(compress.read_text(record_path(root, basefile)))
    return parse_envelope(envelope).to_artifact()
