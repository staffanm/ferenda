"""Find legal citation occurrences in submitted text, without checking existence.

The publication parser supplies context and spans. Lookup-shaped candidates
add identities and malformed references that a publication linker omits.
Positions always refer to the submitted text; no OCR corrections are guessed.
"""

import functools
import re
from bisect import bisect_right

from . import catalog, courtids, resolve, treatyref
from .lagrum import FS_SLUG

_IDENTIFIERS = re.compile(
    r"https://lagen\.nu/[^\s<>\"'\u201d]+"
    r"|\bECLI:[A-Z]{2}:[A-Z0-9.]+:[0-9]{4}:[A-Z0-9.]+"
    r"|\b(?:CELEX\s*:\s*)?[01356][0-9]{4}[A-Z][A-Z0-9/()_-]*"
    r"|\bICC-[0-9]+/[0-9]+-[0-9]+/[0-9]+-[0-9]+(?:-[A-Z0-9]+)*"
    r"|\b(?:ICJ\s+)?[0-9]{3}[-_][0-9]{8}[-_][A-Z]{3}[-_][0-9]{2}[-_][0-9]{2}"
    r"(?:[-_](?:EN|FR|BI)C?)?(?:\.pdf)?"
    r"|\b(?:C?ETS\s*(?:No\.?\s*)?|CoE\s+|ICRC\s+)[0-9]+"
    r"|\bUNTC\s+[IV]+-[0-9]+"
    r"|\b(?:HUDOC\s+)?001-[0-9]+"
    r"|\bSFS\s+[0-9]{4}:-?[0-9]+", re.I)
_NJA = re.compile(
    r"\bNJA\s+[0-9]{4}\s*s\.?\s*-?[0-9]+"
    r"(?:\s*[-–]\s*[0-9]+|[.,][0-9]+)?(?:\s+[IVX]+\b)?", re.I)
_PINPOINT = (r"(?:[0-9]+\s*[a-z]?\s*kap\.?\s*)?"
             r"[0-9]+(?:\s?[a-z]\b)?\s*§")
_BEFORE_PROVISION = re.compile(_PINPOINT + r"(?:\s+i)?\s*$", re.I)
_AFTER_PROVISION = re.compile(
    r"\s+(?:" + _PINPOINT + r"|[0-9]+:[0-9]+[a-z]?)", re.I)


@functools.cache
def _names():
    return re.compile(r"(?<!\w)(?:" + "|".join(
        re.escape(name).replace(r"\ ", r"\s+") for name in resolve.citation_names())
                      + r")(?!\w)(?:\s+(?:art(?:ikel|icle)?\.?\s*)?"
                      r"[0-9]+(?:\s*kap\.?\s*[0-9]+\s*§|[.:][0-9]+|\s*§)?)?", re.I)


@functools.cache
def _regulations():
    return re.compile(r"\b(?:" + "|".join(map(re.escape, FS_SLUG))
                      + r")\s*[0-9]{4}:[0-9]+", re.I)


def _candidates(value):
    for pattern in (_IDENTIFIERS, _NJA, courtids.ICJ_REPORT, _names(), _regulations()):
        for match in pattern.finditer(value):
            start, end = match.span()
            # Sentence punctuation is not part of a document identity.
            surface = value[start:end].rstrip(".,;!?")
            for left, right in (("[", "]"), ("(", ")")):
                excess = min(surface.count(right) - surface.count(left),
                             len(surface) - len(surface.rstrip(right)))
                if excess > 0:
                    surface = surface[:-excess]
            end = start + len(surface)
            if pattern is _regulations():
                before = _BEFORE_PROVISION.search(value[max(0, start - 80):start])
                after = _AFTER_PROVISION.match(value[end:])
                if before:
                    start = max(0, start - 80) + before.start()
                elif after:
                    end += after.end()
            yield start, end


def _occurrences(value, con):
    grouped = {}
    for ref in (resolve.citation_parser().parse_text(value, context={})
                + treatyref.refs(value)):
        source = resolve.citation_source(ref.uri)
        if source:
            grouped.setdefault((ref.start, ref.end), {})[ref.uri] = source
    # Exact lookup has priority on the same span: a signed NJA page must not
    # become a positive page, and an official alias must keep all its targets.
    interpreted = {}
    for start, end in _candidates(value):
        query = " ".join(value[start:end].split())
        if (start, end) in grouped and not _NJA.fullmatch(query):
            continue
        if query not in interpreted:
            interpreted[query] = {hit["uri"]: hit["source"] for hit in (
                resolve.resolve(query) + catalog.citation_targets(con, query))}
        grouped[start, end] = interpreted[query]
    # Keep outer citation spans, suppressing nested names/partial matches.
    # Repeated occurrences at different positions are never deduplicated.
    selected = []
    for (start, end), targets in sorted(grouped.items(), key=lambda item: (
            item[0][0], -item[0][1])):
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end, targets))
    return selected


def extract(blocks, con):
    """Ordered {id, text} blocks -> occurrences with UTF-16 block locations.

    Newlines join blocks so a citation can cross a PDF page boundary. One
    parser sees the whole document, including context introduced in earlier
    blocks. The caller retains the mapping from block ids to file locations.
    """
    value = "\n".join(block["text"] for block in blocks)
    starts, offsets, cursor = [], [], 0
    for block in blocks:
        starts.append(cursor)
        units = [0]
        for char in block["text"]:
            units.append(units[-1] + (2 if ord(char) > 0xFFFF else 1))
        offsets.append(units)
        cursor += len(block["text"]) + 1
    out = []
    try:
        occurrences = _occurrences(value, con)
    finally:
        resolve.clear_parser_state()
    for start, end, targets in occurrences:
        locations = []
        index = max(0, bisect_right(starts, start) - 1)
        while index < len(blocks) and starts[index] < end:
            left = max(0, start - starts[index])
            right = min(len(blocks[index]["text"]), end - starts[index])
            if left < right:
                locations.append({"block_id": blocks[index]["id"],
                                  "start": offsets[index][left],
                                  "end": offsets[index][right]})
            index += 1
        out.append({"text": value[start:end], "locations": locations,
                    "targets": [{"uri": uri, "source": source}
                                for uri, source in sorted(targets.items())]})
    return out
