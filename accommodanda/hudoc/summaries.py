"""`lagen hudoc sync-summaries` -- link the Court's own summary of a case from
the page of the case.

The Court writes a *Case-Law Information Note* for the judgments and decisions
it considers worth reading: its own account of what the case decided, a page
long, written for people who will not read forty pages of Strasbourg reasoning.
6,505 of them exist in English, 1,332 at the Court's top importance level.

They are not documents of ours. A summary says what the judgment we already
publish says, so publishing it as a second document would put the same holding
at two addresses and split every search result in half. It is a **link on the
document it summarises**, harvested as metadata: one small sidecar per case,
holding the summary's item id and title.

The join is `(application number, date)`. HUDOC gives a summary no pointer to
the case it summarises -- no ECLI, no item id -- but it repeats the case's
application numbers and carries the case's own date, and that pair is unique:
over the whole store no two documents share it, and no summary matches more
than one document. A summary whose case the store does not hold is counted and
dropped; today that is mostly summaries of French-original judgments, which the
English-only scope leaves out.

Sidecars live under `<downloaded>/hudoc/clin/<itemid>.json`, one per summarised
case, so a re-run re-stales only the parses whose summary actually moved -- a
single shared index file would re-stale all 55,000.
"""

import json
import sys
from pathlib import Path

from ..lib import compress, util
from ..lib.net import make_session
from . import download

SUBDIR = "clin"
# the Court writes its Notes in English and French; the English one is the link,
# matching the store's own default expression
LANGUAGES = ("ENG",)


def sidecar_path(root, basefile):
    return Path(root) / SUBDIR / (basefile + ".json")


def read_sidecar(root, basefile):
    """The stored summary reference for a case, or None when it has none."""
    return compress.read_json(sidecar_path(root, basefile), default=None)


def _key(record):
    """The `(application number, date)` pairs a record joins on. A case with
    several applications yields one pair per application; a summary repeats the
    same numbers, so any one of them identifies the case."""
    day = (record.get("kpdate") or "")[:10]
    return [(appno, day) for appno in (record.get("appno") or "").split(";")
            if appno and day]


def held_index(root, log=print):
    """`(appno, date)` -> item id over the harvested records. Two documents on
    one pair would make the join ambiguous, so that raises rather than
    attaching the Court's summary of one case to another.

    That is the join's precondition: every language expression of a case repeats
    its application numbers and its date, so a store harvested with `--lang
    ENG,FRE` trips this on the first bilingual case. Choosing a language for the
    summary to hang on is a rule nobody has written yet."""
    basefiles = download.list_basefiles(root)
    index = {}
    for done, basefile in enumerate(basefiles, 1):
        util.status(done, len(basefiles), "hudoc  indexing stored cases")
        record = compress.read_json(download.record_path(root, basefile))
        for key in _key(record):
            if key in index:
                raise ValueError(
                    "%s and %s share application %s of %s -- either the store "
                    "holds two language expressions of one case (see the "
                    "download's --lang), or the pair no longer identifies a case"
                    % (index[key], basefile, key[0], key[1]))
            index[key] = basefile
    sys.stderr.write("\n")             # close the live counter's line
    log("  indexed %d stored cases" % len(basefiles))
    return index


def summary_records(session, delay=0.2):
    """Every English Case-Law Information Note, newest first -- the same
    year-sliced walk the documents use, for the same reason (the collection is
    far past what HUDOC will page over in one query)."""
    return download.enumerate_records(session, LANGUAGES, download.SUMMARIES,
                                      delay=delay)


def resolve(root, records, log=print):
    """Match each summary to the case it summarises. Returns
    `(matched, unmatched)`, matched as `{basefile: record}`."""
    index = held_index(root, log=log)
    matched, unmatched = {}, 0
    for record in records:
        hosts = {index[key] for key in _key(record) if key in index}
        if len(hosts) == 1:
            matched[hosts.pop()] = record
        elif not hosts:
            unmatched += 1
        else:
            raise ValueError(
                "summary %s matches %s -- its application numbers and date "
                "reach more than one stored case"
                % (record["itemid"], ", ".join(sorted(hosts))))
    log("  %d summaries match a stored case, %d summarise a case we do not hold"
        % (len(matched), unmatched))
    return matched, unmatched


def store(root, matched, log=print):
    """Write one sidecar per matched case, and remove the sidecar of a case that
    no longer has a summary. Returns `(changed, removed)` -- an unchanged
    sidecar is left alone so the case's parse stays fresh.

    Removing is safe here because `matched` is the whole matched set of a
    *complete* walk: `download.enumerate_records` raises rather than returning a
    short enumeration. Without it a Note the Court withdraws, or re-matches to
    another case, would keep its link on the case page forever -- a re-run could
    never take it back.

    One state the completeness guard cannot tell from a real answer is an empty
    one: a CLIN query that returns nothing walks to `expected == covered == 0`
    and would reap every sidecar on disk. The Court does not un-publish 6,505
    notes, so that reads as an upstream fault and raises."""
    changed = removed = 0
    for basefile, record in matched.items():
        reference = {key: record[key] for key in ("itemid", "docname")}
        path = sidecar_path(root, basefile)
        if compress.read_json(path, default=None) == reference:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        compress.write_text(path, json.dumps(reference, ensure_ascii=False,
                                             indent=1))
        changed += 1
    stored = compress.glob(Path(root) / SUBDIR, "*.json")
    if stored and not matched:
        raise ValueError(
            "HUDOC matched no summary at all while %d sidecars sit on disk -- "
            "reaping them would take the Court's summary off every case page "
            "on one bad answer from the endpoint" % len(stored))
    for path in stored:
        if path.stem not in matched:
            compress.remove(path)
            log("  removed the stale summary link on %s" % path.stem)
            removed += 1
    return changed, removed


def sync(root, delay=0.2, log=print):
    session = make_session(download.USER_AGENT)
    matched, unmatched = resolve(
        root, summary_records(session, delay=delay), log=log)
    changed, removed = store(root, matched, log=log)
    log("hudoc sync-summaries: %d matched, %d written or updated, %d removed, "
        "%d without a stored case"
        % (len(matched), changed, removed, unmatched))
    return len(matched), changed
