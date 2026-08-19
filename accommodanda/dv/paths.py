"""Where a dv case's parseable source lives on disk.

A case is named by its canonical id and its body can come from three places:
the API record the domstol API published, the frozen Word referat or notis XML
of a legacy-only case, and -- for a verdict published before its referat -- the
court's own PDF attachment. Resolving which one is dv's own knowledge, not the
build driver's, so it lives here.

That placement is what lets `patchsource` import these directly. It used to
reach them through `build`, which imports `patchsource` back (via `api.patch`),
so the only way in was an in-function import inside the cycle."""

import functools
import json
from pathlib import Path

from ..lib import layout, util
from . import legacy as dv_legacy
from .parse import api_member


@functools.cache
def cases():
    """canonical id -> index case, for every case with a parseable source:
    an API record, or -- legacy-only -- a non-empty frozen original (Word
    referat / imported notis XML)."""
    return {c["canonical_id"]: c
            for c in json.loads(layout.DOM_INDEX.read_text())
            if api_member(c) or dv_legacy.legacy_original(c)}


def member(basefile):
    """The member record parse reads for a case: the API record when the case
    has one, else the legacy original (a frozen Word referat or notis XML)."""
    case = cases()[basefile]
    return api_member(case) or dv_legacy.legacy_original(case)


def record(basefile):
    # the identity index stores paths data_root-relative (portable); resolve here
    return util.load_relpath(layout.DATA, member(basefile)["path"])


def verdict_pdf(basefile, record_json):
    """The raw verdict's PDF attachment path (``{uuid}/{målnummer}.pdf``), or None
    -- the body source when a not-yet-published HD/HFD decision carries no innehåll
    HTML (R2). Stored plain (PDFs skip Brotli), so the path is resolved directly."""
    for bilaga in record_json.get("bilagaLista") or []:
        name = Path(bilaga.get("filnamn") or "").name
        if name.lower().endswith(".pdf"):
            pdf = record(basefile).with_suffix("") / name
            if pdf.exists():
                return pdf
    return None
