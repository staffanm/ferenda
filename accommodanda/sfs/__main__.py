"""CLI for the new SFS pipeline.

  python -m accommodanda.sfs parse DOWNLOADED.html         # NF JSON to stdout
  python -m accommodanda.sfs validate GOLDENDIR DOWNLOADDIR  # corpus-wide compare
"""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..lib.datasets import NAMEDLAWS as NAMEDLAWS_JSON
from ..lib.lagrum import LagrumParser, load_namedlaws
from . import input_paths, load_inputs
from ._validate import load_golden_module, validate_one
from .nf import inline_references, to_normalform


def basefile_from_path(path, root):
    rel = path.relative_to(root)
    return "%s:%s" % (rel.parts[0], rel.stem.replace("_", " "))


def signature(problem):
    """Bucket key for a diff message: strip document-specific specifics."""
    sig = re.sub(r"[0-9]+", "#", problem.split("\n")[0])
    return re.sub(r"'[^']*'", "'…'", sig)


def describe(problem):
    """Render a raw diff line for the report as (category, golden, new) -- the
    golden being the *expected* value and the new pipeline the *actual* one.
    `golden`/`new` are None when only the category is meaningful (then it is
    printed alone). A diff is one of: a value that changed (both sides shown),
    or something present on only one side (the other side shown as '—')."""
    head = problem.split("\n")[0]
    m = re.search(r"\bold: (.*)\n\s*new: (.*)", problem, re.S)
    if m and head.endswith("changed:"):
        return (head[:-len(" changed:")] + " differs", m.group(1), m.group(2))
    m = re.match(r"(uri|metadata\.uri): (.+) != (.+)$", head)
    if m:
        return ("%s differs" % m.group(1), m.group(2), m.group(3))
    m = re.match(r"references: (missing|extra) (\S*) --\S+--> (.+)$", head)
    if m:
        kind, src, uri = m.groups()
        ref = "%s → %s" % (src or "(no source fragment)", uri)
        # the buckets differ by the *shape* of the source/target fragments
        # (a whole-chapter source K1 vs a stycke source K10P1S1 are different
        # kinds of diff); spell that out so same-direction buckets aren't
        # indistinguishable
        where = "source %s → target %s" % (
            _fragment_level(src) or "(none)",
            _fragment_level(uri.split("#", 1)[1] if "#" in uri else "")
            or "whole document")
        return (("reference only in golden (new omitted it) — %s" % where, ref, "—")
                if kind == "missing" else
                ("reference only in new (golden lacks it) — %s" % where, "—", ref))
    m = re.match(r"amendments: (missing|extra) (.+)$", head)
    if m:
        kind, uri = m.groups()
        return (("amendment only in golden (new pipeline omitted it)", uri, "—")
                if kind == "missing" else
                ("amendment only in new (golden lacks it)", "—", uri))
    m = re.match(r"(.+): (missing|extra) node (.+)$", head)
    if m:
        path, kind, label = m.groups()
        return (("node only in golden, under %s" % path, label, "—")
                if kind == "missing" else
                ("node only in new, under %s" % path, "—", label))
    m = re.match(r"(.+): missing (.+) \(was (.*)\)$", head)
    if m:
        return ("%s.%s only in golden" % (m.group(1), m.group(2)), m.group(3), "—")
    m = re.match(r"(.+): extra (.+) \(= (.*)\)$", head)
    if m:
        return ("%s.%s only in new" % (m.group(1), m.group(2)), "—", m.group(3))
    return (head, None, None)


def _clip(text, width=160):
    text = str(text)
    return text if len(text) <= width else text[:width - 1] + "…"


# fragment letters finest -> coarsest, for naming a fragment's granularity
# (K kapitel · P paragraf · O moment · S stycke · N punkt · M mening · L ändring)
_FRAG_LEVELS = (("M", "mening"), ("N", "punkt"), ("S", "stycke"),
                ("O", "moment"), ("P", "paragraf"), ("K", "kapitel"),
                ("L", "ändring"))


def _fragment_level(frag):
    """The depth a fragment id reaches, e.g. 'K1' -> 'kapitel',
    'K10P1S1' -> 'stycke', '' -> None."""
    for letter, name in _FRAG_LEVELS:
        if letter in (frag or ""):
            return name
    return None


def cmd_parse(args):
    """Parse one downloaded document, given either the new JSON _source or a
    legacy SFST .html page (its SFSR register sibling is found alongside)."""
    path = Path(args.file).resolve()
    basefile = args.basefile or "%s:%s" % (path.parent.name,
                                           path.stem.replace("_", " "))
    json_path, html_path, register_path = input_paths(path)
    doc, register, sfst_header = load_inputs(
        json_path, html_path, register_path, basefile)
    nf = to_normalform(doc, basefile,
                       refparser=LagrumParser(load_namedlaws(NAMEDLAWS_JSON), basefile),
                       register=register, sfst_header=sfst_header)
    json.dump(nf, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    print()


def cmd_refs(args):
    """Compare one document's extracted references against its golden
    counterpart (the old-pipeline parsed XHTML, normalized on the fly, or a
    pre-normalized .json). Register-derived tuples (L* source fragments) and
    begrepp links (dcterms:subject) are excluded here -- the corpus `validate`
    compares begrepp separately as a term set."""
    path = Path(args.file).resolve()
    basefile = args.basefile or "%s:%s" % (path.parent.name,
                                           path.stem.replace("_", " "))
    json_path, html_path, register_path = input_paths(path)
    doc, _register, _sfst_header = load_inputs(
        json_path, html_path, register_path, basefile)
    refparser = LagrumParser(load_namedlaws(NAMEDLAWS_JSON), basefile)
    nf = to_normalform(doc, basefile, refparser=refparser)
    golden_sfs = load_golden_module()
    new = golden_sfs.canonicalize_refs(inline_references(nf["structure"]))

    golden_all = set(map(tuple, golden_sfs.load(args.golden)["references"]))
    deferred = {r for r in golden_all
                if r[0].startswith("L") or r[1] == "dcterms:subject"}
    golden = golden_sfs.canonicalize_refs(golden_all - deferred)

    missing = sorted(set(golden) - set(new))
    extra = sorted(set(new) - set(golden))
    print("%d golden (+%d deferred), %d new: %d missing, %d extra"
          % (len(golden), len(deferred), len(new), len(missing), len(extra)))
    for key in missing:
        print("  " + golden_sfs.format_ref("missing", key, golden[key]))
    for key in extra:
        print("  " + golden_sfs.format_ref("extra", key, new[key]))


def cmd_validate(args):
    # the "golden" is the old pipeline's parsed XHTML+RDFa (scaffolding in the old checkout);
    # validate_one normalizes each to NF on the fly -- no frozen golden tree.
    parseddir, downloaddir = Path(args.parseddir), Path(args.downloaddir)
    jobs = []
    for parsedfile in sorted(parseddir.rglob("*.xhtml")):
        rel = parsedfile.relative_to(parseddir)
        # the downloaded tree is now JSON (the new beta API source); the
        # legacy SFST HTML tree (downloaded/sfst/…/*.html) is the fallback
        downloaded = downloaddir / rel.with_suffix(".json")
        if not downloaded.exists():
            downloaded = (downloaddir / "sfst" / rel).with_suffix(".html")
        if not downloaded.exists():
            continue
        jobs.append((parsedfile, downloaded,
                     basefile_from_path(parsedfile, parseddir),
                     args.sections))
    if args.limit:
        jobs = jobs[:args.limit]

    counts = Counter()
    buckets = Counter()
    accepted_rules = Counter()
    examples = {}
    diffdocs = {}
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for i, (basefile, status, problems, accepted) in enumerate(
                pool.map(validate_one, jobs, chunksize=8), 1):
            counts[status] += 1
            accepted_rules.update(accepted)
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
              "adjudicated": dict(accepted_rules.most_common()),
              "buckets": dict(buckets.most_common()),
              "documents": diffdocs}
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    total = sum(counts.values())
    # an adjudicated document had diffs, but every one was forgiven as
    # new-is-right, so it passes alongside the clean matches; "diff" is now
    # the count of genuine regressions
    passing = counts["match"] + counts["adjudicated"]
    print("%d documents: %d match + %d adjudicated = %d passing (%.1f%%), "
          "%d diff, %d error, %d skipped"
          % (total, counts["match"], counts["adjudicated"], passing,
             100 * passing / total if total else 0,
             counts["diff"], counts["error"], counts["skipped"]))
    if accepted_rules:
        print("adjudicated as new-is-right:")
        for rule, n in accepted_rules.most_common():
            print("  %5d  %s" % (n, rule))
    print("top regression buckets (golden = expected, new pipeline = actual):")
    for sig, n in buckets.most_common(args.top):
        basefile, example = examples[sig]
        category, golden_val, new_val = describe(example)
        print("  %5d  %s" % (n, category))
        if golden_val is None:
            print("         e.g. %s: %s" % (basefile, _clip(example.split("\n")[0])))
        else:
            print("         e.g. %s" % basefile)
            print("           golden: %s" % _clip(golden_val))
            print("           new:    %s" % _clip(new_val))
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
    v.add_argument("parseddir", help="old-pipeline parsed XHTML tree "
                   "(scaffolding, e.g. ../ferenda.old/data/sfs/parsed); "
                   "normalized to NF on the fly")
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
