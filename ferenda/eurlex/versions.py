"""Parse an EU act's superseded consolidations into per-version artifacts.

The download keeps every consolidated wording (CONSLEG) of an act under
``downloaded/eurlex/{year}/{celex}/.versions/{date}/`` (see
`download.download_consolidations`). The *latest* Formex-bearing one already
serves at the act's own uri (`parse.parse_dir` swaps it in); this stage gives
each earlier wording the same treatment, writing

  artifact/eurlex/archive/{year}/{celex}/.versions/{date}.json   one per version
  artifact/eurlex/{year}/{celex}.versions.json                   the index

which the renderer (historical "lydelse" pages, the compare panel), the API
(/document/versions, /document/diff) and the diff view consume -- the eurlex
counterpart of `sfs.versions`. A version's id is the ISO date its wording began
to apply ("2024-10-18"), CELLAR's own key. A version whose best manifestation
is not Formex (the pre-2005 tail is PDF-only) is recorded as skipped rather
than parsed.
"""

import json

from ..lib import compress, layout, util
from .parse import (
    base_preamble,
    content_file,
    parse_consolidation,
    parse_content,
    to_artifact,
    version_dirs,
)


def build(basefile):
    """The versions stage recipe: parse every superseded consolidation of
    `basefile` into a version artifact and write the sidecar index (always,
    even empty -- an existing sidecar is what marks the stage's output built).
    Returns the sidecar dict.

    The newest Formex-bearing version is excluded: it is the main artifact
    (`parse.parse_dir`), and its /konsolidering/ page would duplicate the
    act's own. Everything older -- the as-published base text included --
    becomes a lydelse page."""
    doc_dir = layout.eurlex_dir(basefile)
    versions, skipped = [], []
    downloads = version_dirs(doc_dir)
    latest = _latest_formex(downloads)
    preamble = _base_preamble(doc_dir, basefile)
    # a heavily amended act is minutes of work (CRR: 21 versions of 786
    # articles), so the live line moves per version, not per basefile
    rep = util.stage_reporter()
    for i, (version, vdir) in enumerate(downloads, 1):
        rep.update(i, len(downloads), scope=basefile, note=version)
        if version == latest:
            continue
        doc = parse_consolidation(vdir, basefile, version, preamble=preamble)
        if doc is None:
            skipped.append({"version": version, "error": "no Formex "
                            "manifestation (pre-Formex consolidations are "
                            "PDF-only)"})
            continue
        art = to_artifact(doc)
        out = layout.eurlex_version_artifact(basefile, version)
        out.parent.mkdir(parents=True, exist_ok=True)
        compress.write_text(out, json.dumps(art, ensure_ascii=False, indent=2,
                                            sort_keys=True),
                            encodings=compress.ARTIFACT_ENCODINGS)
        versions.append({"version": version, "uri": art["uri"]})
    rep.clear()          # the driver's own per-basefile line takes over
    sidecar = {"versions": versions, "skipped": skipped}
    out = layout.eurlex_versions_sidecar(basefile)
    out.parent.mkdir(parents=True, exist_ok=True)
    util.write_atomic(out, json.dumps(sidecar, ensure_ascii=False, indent=2,
                                      sort_keys=True).encode())
    return sidecar


def _latest_formex(downloads):
    """The version id the main artifact presents: the newest downloaded
    consolidation whose best content file is Formex, or None."""
    for version, vdir in reversed(downloads):
        _path, _lang, route = content_file(vdir)
        if route == "fmx4":
            return version
    return None


def _base_preamble(doc_dir, basefile):
    """The base act's own preamble blocks, parsed once for the whole history
    -- every version artifact splices the same recitals in front, exactly as
    the main artifact does."""
    path, lang, route = content_file(doc_dir)
    if path is None:
        return ()
    return base_preamble(parse_content(path, route, basefile, lang))
