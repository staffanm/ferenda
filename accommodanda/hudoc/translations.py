"""`lagen hudoc propose-translations` -- draft the commentary that puts a
Strasbourg judgment's Swedish translation on the page of the judgment itself.

Domstolsverket translated 87 Strasbourg judgments and decisions into Swedish
between 2014-01-14 and 2015-12-15, and HUDOC hosts each translation as an item
of its own (Roman Zakharov, Perinçek, Bouyid, Parrillo, Couderc). A translation
is **not a document of ours**: it says what the judgment we already publish
says, in Swedish. It belongs where the English translation of a Swedish statute
belongs -- as one line of commentary on the host document. `commentary/sfs/1971/291.md`
opens with a link to the Government Offices' translation of
förvaltningsprocesslagen; this is the same line, inverted: a Swedish page for a
foreign-language document instead of a foreign-language page for a Swedish one.

The join is the **ECLI**, which a translation carries identically to its
original (`ECLI:CE:ECHR:2015:1204JUD004714306` names the case, not the item), so
the match is exact and needs no title or date heuristics. A translation whose
original is not in the store is reported, never guessed at: 8 of the 87 translate
*decisions*, which the store gained only with the decisions collection.

This writes drafts into the git-backed content repo (WIKI_ROOT) and stops there
-- the same division `kommentar discover-guidance` keeps. A file that already
exists is never overwritten: the editor owns the prose from that point on.
"""

import sys
from pathlib import Path

from ..lib import compress, layout, util
from ..lib.net import make_session, request
from . import download
from .model import ITEM_URL

TRANSLATION_LANGUAGE = "SWE"
# every one of the 87 carries this translator in its docname; a new translation
# from another body would be a different sentence, so the value is checked
# rather than assumed
TRANSLATOR = "the Swedish National Courts Administration"
TRANSLATOR_SV = "Domstolsverket"
TEMPLATE = """---
annotates: %s
---
[%s har översatt avgörandet till svenska](%s)
"""


def commentary_path(wiki_root, basefile):
    """Where the draft lands: `commentary/hudoc/<itemid>.md`, the storage rule
    `layout.relpath` gives a kommentar filed under its host source."""
    return (Path(wiki_root) / "commentary"
            / layout.relpath("kommentar", basefile)).with_suffix(".md")


def translation_records(session):
    """Every HUDOC case-law item in Swedish. One page: the set is 87 items and
    closed -- Domstolsverket stopped translating in December 2015."""
    envelope = request(
        session, "GET", download.QUERY_ENDPOINT, parse_json=True, timeout=120,
        params={"query": 'documentcollectionid2:"CASELAW" AND (languageisocode:"%s")'
                         % TRANSLATION_LANGUAGE,
                "select": ",".join(download.FIELDS), "sort": "kpdate Descending",
                "start": "0", "length": "500",
                "rankingModelId": download.RANKING_MODEL})
    records = [download.result_record(result)
               for result in envelope.get("results") or []]
    if len(records) != int(envelope["resultcount"]):
        raise ValueError(
            "HUDOC reports %s translations but returned %d -- the set has "
            "outgrown one page" % (envelope["resultcount"], len(records)))
    return records


def held_by_ecli(root, log=print):
    """ECLI -> item id over the harvested records. A record's ECLI names the
    case; the item id names one language version of it, which is exactly the
    direction the join needs.

    That is also the join's precondition: an ECLI is shared by every language
    expression of a case, so a store harvested with `--lang ENG,FRE` holds two
    records under one ECLI and the join has no ground to prefer either. It
    raises rather than annotating whichever the store listed first; making the
    Swedish translation attach to one chosen language version is a rule nobody
    has written yet."""
    basefiles = download.list_basefiles(root)
    index = {}
    for done, basefile in enumerate(basefiles, 1):
        util.status(done, len(basefiles), "hudoc  indexing stored cases by ECLI")
        ecli = compress.read_json(
            download.record_path(root, basefile)).get("ecli")
        if not ecli:
            continue                       # a placeholder-era record carries none
        if ecli in index:
            raise ValueError(
                "%s is the ECLI of both %s and %s -- the store holds more than "
                "one language expression of this case, and the join has no rule "
                "for choosing between them (see the download's --lang)"
                % (ecli, index[ecli], basefile))
        index[ecli] = basefile
    sys.stderr.write("\n")             # close the live counter's line
    log("  indexed %d stored cases by ECLI" % len(index))
    return index


def _translator(record):
    """The body named in the docname ("… - [Swedish Translation] by X"), or None
    when the docname does not name one."""
    _, marker, tail = (record.get("docname") or "").partition("[Swedish Translation]")
    return tail.strip().removeprefix("by ").strip() if marker else None


def proposals(session, root, log=print):
    """`(matched, unmatched, doubled)` -- matched pairs each held item id with
    the translation record to link from it; unmatched are translations whose
    original the store does not hold; doubled are the further translations of a
    case that HUDOC lists more than once.

    HUDOC really does hold two items for one translation (H. and J. v. the
    Netherlands is under both 001-164863 and 001-167571). They render the same
    Swedish text, so a draft links the newest and the rest are returned to be
    named in the log -- dropping them silently would leave the choice of item id
    to result order."""
    index = held_by_ecli(root, log=log)
    matched, unmatched, doubled = {}, [], []
    for record in translation_records(session):
        translator = _translator(record)
        if translator != TRANSLATOR:
            raise ValueError(
                "%s names translator %r, not %r -- the attribution the draft "
                "prints has to be checked before it is written"
                % (record["itemid"], translator, TRANSLATOR))
        host = index.get(record.get("ecli"))
        if not host:
            unmatched.append(record)
        elif host in matched:
            # translation_records sorts newest first, so the incumbent is newer
            doubled.append((host, record))
        else:
            matched[host] = record
    return sorted(matched.items()), unmatched, doubled


def draft(basefile, record):
    return TEMPLATE % (basefile, TRANSLATOR_SV, ITEM_URL % record["itemid"])


def write_drafts(wiki_root, matched, log=print):
    """Write one commentary draft per matched translation, skipping any file
    that exists -- the editor owns it from the first hand edit on. Returns
    `(written, kept)`."""
    written = kept = 0
    for basefile, record in matched:
        path = commentary_path(wiki_root, basefile)
        if path.exists():
            kept += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft(basefile, record), encoding="utf-8")
        log("  wrote %s -> %s" % (path, ITEM_URL % record["itemid"]))
        written += 1
    return written, kept


def propose(root, wiki_root, dry_run=False, log=print):
    session = make_session(download.USER_AGENT)
    matched, unmatched, doubled = proposals(session, root, log=log)
    for record in unmatched:
        log("  no stored original for %s (%s) -- %s"
            % (record["itemid"], record.get("ecli"),
               (record.get("docname") or "")[:60]))
    for host, record in doubled:
        log("  %s also translates %s; the newer item is linked"
            % (record["itemid"], host))
    if dry_run:
        log("hudoc propose-translations: would draft %d commentary file(s), "
            "%d translation(s) have no stored original"
            % (len(matched), len(unmatched)))
        return 0, 0
    written, kept = write_drafts(wiki_root, matched, log=log)
    log("hudoc propose-translations: %d drafted, %d already written, "
        "%d without a stored original" % (written, kept, len(unmatched)))
    return written, kept
