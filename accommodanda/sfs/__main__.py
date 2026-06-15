"""CLI for the new SFS pipeline.

  python -m accommodanda.sfs parse DOWNLOADED.html         # NF JSON to stdout
  python -m accommodanda.sfs validate GOLDENDIR DOWNLOADDIR  # corpus-wide compare
"""

import argparse
import functools
import importlib.util
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import parse_sfs, parse_sfs_source
from ..lib.errors import SkipDocument
from ..lib.lagrum import LagrumParser, load_namedlaws
from .nf import inline_references, temporal_dates, to_normalform
from .register import (parse_register, parse_sfst_header,
                       register_from_source, sfst_header_from_source)

NAMEDLAWS_TTL = Path(__file__).parent.parent.parent / "lagen/nu/res/extra/sfs.ttl"


def load_golden_module():
    spec = importlib.util.spec_from_file_location(
        "golden_sfs", Path(__file__).parent.parent.parent / "tools" / "golden_sfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def basefile_from_path(path, root):
    rel = path.relative_to(root)
    return "%s:%s" % (rel.parts[0], rel.stem.replace("_", " "))


def signature(problem):
    """Bucket key for a diff message: strip document-specific specifics."""
    sig = re.sub(r"[0-9]+", "#", problem.split("\n")[0])
    return re.sub(r"'[^']*'", "'…'", sig)


@functools.cache
def namedlaws():
    return load_namedlaws(NAMEDLAWS_TTL)


def load_register(downloadedfile):
    """The SFSR register page sits parallel to the downloaded SFST page.
    Returns None when it's absent or empty (the doc has no register)."""
    path = Path(str(downloadedfile).replace("/downloaded/", "/register/"))
    if not path.exists():
        return None
    try:
        return parse_register(path)
    except SkipDocument:
        return None


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


def validate_one(job):
    goldenfile, downloadedfile, basefile, sections = job
    golden_sfs = load_golden_module()
    golden = json.loads(Path(goldenfile).read_text())
    golden_sfs.canonicalize_node_texts(golden["structure"])
    try:
        doc = parse_sfs(downloadedfile, basefile)
    except SkipDocument as e:
        return (basefile, "skipped", [str(e)])
    except Exception as e:
        return (basefile, "error", ["%s: %s" % (type(e).__name__, e)])

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
                    ["refparse %s: %s" % (type(e).__name__, e)])
    if "amendments" in sections:
        try:
            best = best + compare_amendments(golden, doc, basefile, best_now,
                                             downloadedfile, golden_sfs)
        except Exception as e:
            return (basefile, "error",
                    ["amendments %s: %s" % (type(e).__name__, e)])
    if "metadata" in sections:
        try:
            best = best + compare_metadata(golden, doc, basefile,
                                           downloadedfile, golden_sfs)
        except Exception as e:
            return (basefile, "error",
                    ["metadata %s: %s" % (type(e).__name__, e)])
    return (basefile, "match" if not best else "diff", best)


def compare_metadata(golden, doc, basefile, downloadedfile, golden_sfs):
    register = load_register(downloadedfile)
    if register is None:
        return ["metadata: no register page for %s" % basefile]
    nf = to_normalform(doc, basefile, register=register,
                       refparser=LagrumParser(namedlaws(), basefile),
                       sfst_header=parse_sfst_header(downloadedfile))
    problems = []
    golden_sfs.diff_metadata(golden, nf, problems)
    return problems


def compare_amendments(golden, doc, basefile, now, downloadedfile, golden_sfs):
    register = load_register(downloadedfile)
    if register is None:
        return ["amendments: no register page for %s" % basefile]
    nf = to_normalform(doc, basefile, now=now,
                       refparser=LagrumParser(namedlaws(), basefile),
                       register=register)
    problems = []
    golden_sfs.diff_amendments(golden["amendments"], nf["amendments"], problems)
    return problems


def cmd_parse(args):
    """Parse one downloaded document, given either the new JSON _source or a
    legacy SFST .html page (its SFSR register sibling is found alongside)."""
    path = Path(args.file).resolve()
    basefile = args.basefile or "%s:%s" % (path.parent.name,
                                           path.stem.replace("_", " "))
    if path.suffix == ".json":
        source = json.loads(path.read_text())
        doc = parse_sfs_source(source, basefile)
        register = register_from_source(source)
        sfst_header = sfst_header_from_source(source)
    else:
        doc = parse_sfs(path, basefile)
        register = load_register(path)
        sfst_header = parse_sfst_header(path) if register else None
    nf = to_normalform(doc, basefile,
                       refparser=LagrumParser(namedlaws(), basefile),
                       register=register, sfst_header=sfst_header)
    json.dump(nf, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    print()


def cmd_refs(args):
    """Compare one document's extracted references against its golden
    counterpart. Register-derived tuples (L* source fragments) and
    begrepp links (dcterms:subject) are reported but not yet compared --
    those parts of the pipeline don't exist yet."""
    path = Path(args.file).resolve()
    basefile = args.basefile or "%s:%s" % (path.parent.name,
                                           path.stem.replace("_", " "))
    doc = parse_sfs(args.file, basefile)
    refparser = LagrumParser(load_namedlaws(NAMEDLAWS_TTL), basefile)
    nf = to_normalform(doc, basefile, refparser=refparser)
    golden_sfs = load_golden_module()
    new = golden_sfs.canonicalize_refs(inline_references(nf["structure"]))

    golden_all = set(map(tuple, json.loads(
        Path(args.golden).read_text())["references"]))
    deferred = {r for r in golden_all
                if r[0].startswith("L") or r[1] == "dcterms:subject"}
    golden = golden_sfs.canonicalize_refs(golden_all - deferred)

    missing = sorted(golden - new)
    extra = sorted(new - golden)
    print("%d golden (+%d deferred), %d new: %d missing, %d extra"
          % (len(golden), len(deferred), len(new), len(missing), len(extra)))
    for ref in missing:
        print("  missing %s --%s--> %s" % ref)
    for ref in extra:
        print("  extra   %s --%s--> %s" % ref)


def cmd_validate(args):
    goldendir, downloaddir = Path(args.goldendir), Path(args.downloaddir)
    jobs = []
    for goldenfile in sorted(goldendir.rglob("*.json")):
        if goldenfile.name == "freeze-report.json":
            continue
        downloaded = (downloaddir / goldenfile.relative_to(goldendir)
                      ).with_suffix(".html")
        if not downloaded.exists():
            continue
        jobs.append((goldenfile, downloaded,
                     basefile_from_path(goldenfile, goldendir),
                     args.sections))
    if args.limit:
        jobs = jobs[:args.limit]

    counts = Counter()
    buckets = Counter()
    examples = {}
    diffdocs = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for i, (basefile, status, problems) in enumerate(
                pool.map(validate_one, jobs, chunksize=8), 1):
            counts[status] += 1
            if status in ("diff", "error"):
                diffdocs[basefile] = problems
                for problem in problems:
                    sig = signature(problem)
                    buckets[sig] += 1
                    examples.setdefault(sig, (basefile, problem))
            if i % 200 == 0 or i == len(jobs):
                print("\r%d/%d %s" % (i, len(jobs), dict(counts)),
                      end="", flush=True)
    print()

    report = {"counts": dict(counts),
              "buckets": dict(buckets.most_common()),
              "documents": diffdocs}
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    total = sum(counts.values())
    print("%d documents: %d match (%.1f%%), %d diff, %d error, %d skipped"
          % (total, counts["match"],
             100 * counts["match"] / total if total else 0,
             counts["diff"], counts["error"], counts["skipped"]))
    print("top diff buckets:")
    for sig, n in buckets.most_common(args.top):
        basefile, example = examples[sig]
        print("  %5d  %s" % (n, sig))
        print("         e.g. %s: %s" % (basefile, example.split("\n")[0][:120]))
    print("full report in %s" % args.report)


def main():
    logging.basicConfig(level=logging.ERROR)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse")
    p.add_argument("file")
    p.add_argument("--basefile")
    p.set_defaults(func=cmd_parse)
    r = sub.add_parser("refs")
    r.add_argument("file")
    r.add_argument("golden")
    r.add_argument("--basefile")
    r.set_defaults(func=cmd_refs)
    v = sub.add_parser("validate")
    v.add_argument("goldendir")
    v.add_argument("downloaddir")
    v.add_argument("--sections", default="structure",
                   help="comma-separated: structure,references,amendments,metadata")
    v.add_argument("--limit", type=int)
    v.add_argument("--jobs", type=int, default=None)
    v.add_argument("--top", type=int, default=25)
    v.add_argument("--report", default="/tmp/accommodanda-validate.json")
    v.set_defaults(func=cmd_validate)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
