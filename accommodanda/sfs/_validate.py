"""Worker functions for `lagen.sfs validate` -- the corpus-wide golden
cross-check. Lives in an importable module (not ``__main__``) so the
``ProcessPoolExecutor`` workers can resolve it when ``python -m`` runs
the CLI (``__main__`` is the runpy bootstrap under ``-m``, not this file,
so a top-level function defined there is invisible to forked workers)."""

import functools
import importlib.util
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import load_inputs
from ..lib.errors import SkipDocument
from ..lib.lagrum import LagrumParser, load_namedlaws
from .nf import inline_references, temporal_dates, to_normalform

NAMEDLAWS_TTL = Path(__file__).parent.parent.parent / "lagen/nu/res/extra/sfs.ttl"


def load_golden_module():
    spec = importlib.util.spec_from_file_location(
        "golden_sfs", Path(__file__).parent.parent.parent / "tools" / "golden_sfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def namedlaws():
    return load_namedlaws(NAMEDLAWS_TTL)


def compare_refs(golden, doc, basefile, now, golden_sfs):
    """Reference tuples vs golden, excluding what the new pipeline does
    not produce yet: register/övergångsbestämmelse tuples (L* source
    fragments) and begrepp links (dcterms:subject)."""
    nf = to_normalform(doc, basefile, now=now,
                       refparser=LagrumParser(namedlaws(), basefile))
    new = golden_sfs.canonicalize_refs(inline_references(nf["structure"]))
    want = golden_sfs.canonicalize_refs(
        r for r in golden["references"]
        if not r[0].startswith("L") and r[1] != "dcterms:subject")
    return (["references: missing %s --%s--> %s" % r
             for r in sorted(want - new)] +
            ["references: extra %s --%s--> %s" % r
             for r in sorted(new - want)])


def compare_metadata(golden, doc, basefile, register, sfst_header, golden_sfs):
    if register is None:
        return ["metadata: no register for %s" % basefile]
    nf = to_normalform(doc, basefile, register=register,
                       refparser=LagrumParser(namedlaws(), basefile),
                       sfst_header=sfst_header)
    problems = []
    golden_sfs.diff_metadata(golden, nf, problems)
    return problems


def compare_amendments(golden, doc, basefile, now, register, golden_sfs):
    if register is None:
        return ["amendments: no register for %s" % basefile]
    nf = to_normalform(doc, basefile, now=now,
                       refparser=LagrumParser(namedlaws(), basefile),
                       register=register)
    problems = []
    golden_sfs.diff_amendments(golden["amendments"], nf["amendments"], problems)
    return problems


def validate_one(job):
    goldenfile, downloadedfile, basefile, sections = job
    golden_sfs = load_golden_module()
    golden = json.loads(Path(goldenfile).read_text())
    golden_sfs.canonicalize_node_texts(golden["structure"])
    # load_inputs prefers the new JSON _source over the legacy SFST+SFSR HTML
    # pair, dispatching on path suffix. The register sibling for an HTML file
    # is looked up alongside (the legacy layout); for JSON it's inside the doc.
    json_path = downloadedfile if downloadedfile.suffix == ".json" else None
    html_path = downloadedfile if downloadedfile.suffix != ".json" else None
    register_path = (Path(str(downloadedfile).replace("/downloaded/", "/register/"))
                     if html_path else None)
    try:
        doc, register, sfst_header = load_inputs(
            json_path, html_path, register_path, basefile)
    except SkipDocument as e:
        return (basefile, "skipped", [str(e)], [])
    except Exception as e:
        return (basefile, "error", ["%s: %s" % (type(e).__name__, e)], [])

    # The golden output's id suppression depends on which temporal variants
    # were in force at parse time -- which is unknowable (download date is
    # recorded, parse date is not). Bracket it: try the download date, each
    # temporal boundary in the document since then, and today; accept the
    # best-matching evaluation moment.
    candidates = []
    issued = golden["metadata"]["properties"].get("dcterms:issued")
    if isinstance(issued, str) and re.match(r"\d{4}-\d{2}-\d{2}$", issued):
        candidates.append(datetime.strptime(issued, "%Y-%m-%d"))
    candidates += [d + timedelta(seconds=1) for d in temporal_dates(doc)
                   if not candidates or d >= candidates[0]]
    candidates.append(datetime.now())

    best, best_now = None, None
    for now in candidates:
        new_nf = to_normalform(doc, basefile, now=now)
        golden_sfs.canonicalize_node_texts(new_nf["structure"])
        problems = []
        golden_sfs.diff_nodelists(golden["structure"], new_nf["structure"],
                                  "structure", problems)
        if best is None or len(problems) < len(best):
            best, best_now = problems, now
        if not problems:
            break

    if "structure" not in sections:
        best = []
    if "references" in sections:
        try:
            best = best + compare_refs(golden, doc, basefile, best_now,
                                       golden_sfs)
        except Exception as e:
            return (basefile, "error",
                    ["refparse %s: %s" % (type(e).__name__, e)], [])
    if "amendments" in sections:
        try:
            best = best + compare_amendments(golden, doc, basefile, best_now,
                                             register, golden_sfs)
        except Exception as e:
            return (basefile, "error",
                    ["amendments %s: %s" % (type(e).__name__, e)], [])
    if "metadata" in sections:
        try:
            best = best + compare_metadata(golden, doc, basefile,
                                           register, sfst_header, golden_sfs)
        except Exception as e:
            return (basefile, "error",
                    ["metadata %s: %s" % (type(e).__name__, e)], [])

    unexplained, accepted = golden_sfs.adjudicate(best, golden)
    if not best:
        status = "match"
    elif not unexplained:
        status = "adjudicated"      # all diffs forgiven as new-is-right
    else:
        status = "diff"
    return (basefile, status, unexplained, [rule for rule, _ in accepted])
