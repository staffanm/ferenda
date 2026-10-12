#!/usr/bin/env python3
"""Compare a function's output between HEAD and the working tree across stored records.

Measures the blast radius of parser, normaliser, and extraction changes against real
stored data (harvest JSON, artifact JSON) to catch regressions before wrapup.
"""

import argparse
import dataclasses
import glob
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys

try:
    import brotli
except ImportError:
    brotli = None

try:
    import gzip
except ImportError:
    gzip = None


RE_DATE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}-\d{2})?\b")
RE_DESIGNATION = re.compile(r"\b[A-ZÅÄÖ]+(?:-| )?FS\s+\d{4}:\d+\b")
RE_CHROME = re.compile(
    r"\b(?:dela\s+sidan|facebook|twitter|linkedin|skriv\s+ut|status:\s*upphävda|"
    r"till\s+toppen|webbplatsen|cookies|tillgänglighetsredogörelse|hoppa\s+till\s+innehåll)\b",
    re.I
)


def load_head_module(rel_path):
    """Load the HEAD version of a python file via git show."""
    res = subprocess.run(["git", "show", f"HEAD:{rel_path}"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Could not load HEAD:{rel_path}: {res.stderr}")

    dir_path = os.path.dirname(os.path.abspath(rel_path))
    stem = os.path.splitext(os.path.basename(rel_path))[0]
    temp_path = os.path.join(dir_path, f"_head_{stem}.py")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(res.stdout)

    pkg = rel_path.replace("/", ".").replace(".py", "")
    parent_pkg = ".".join(pkg.split(".")[:-1])
    mod_name = f"{parent_pkg}._head_{stem}" if parent_pkg else f"_head_{stem}"
    try:
        spec = importlib.util.spec_from_file_location(mod_name, temp_path)
        mod = importlib.util.module_from_spec(spec)
        if parent_pkg:
            mod.__package__ = parent_pkg
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def load_work_module(rel_path):
    """Load the current working tree version of a python file."""
    pkg = rel_path.replace("/", ".").replace(".py", "")
    if pkg in sys.modules:
        del sys.modules[pkg]
    return importlib.import_module(pkg)


def read_record(path):
    """Read a JSON record from plain, brotli, or gzip compressed file."""
    with open(path, "rb") as f:
        data = f.read()
    if path.endswith(".br"):
        if not brotli:
            raise RuntimeError("brotli module not available")
        text = brotli.decompress(data).decode("utf-8", "replace")
    elif path.endswith(".gz"):
        if not gzip:
            raise RuntimeError("gzip module not available")
        text = gzip.decompress(data).decode("utf-8", "replace")
    else:
        text = data.decode("utf-8", "replace")
    return json.loads(text)


def to_dict_or_scalar(val):
    """Convert dataclasses or models to a dict for structured comparison."""
    if hasattr(val, "to_artifact"):
        return val.to_artifact()
    if dataclasses.is_dataclass(val):
        return dataclasses.asdict(val)
    return val


def find_chrome_in_structure(struct):
    """Find any web chrome phrases that leaked into parsed structure nodes."""
    matches = []

    def check(nodes):
        for n in nodes or []:
            if isinstance(n, dict):
                text = n.get("text")
                if isinstance(text, str):
                    m = RE_CHROME.search(text)
                    if m:
                        matches.append(m.group(0))
                elif isinstance(text, list):
                    # Interleaved text/citation runs
                    for run in text:
                        if isinstance(run, str):
                            m = RE_CHROME.search(run)
                            if m:
                                matches.append(m.group(0))
                check(n.get("children"))

    check(struct)
    return matches


def compare_records(target_file, function_name, glob_pattern, field_names,
                    limit=0, max_diffs=20, record_arg=False, root=None,
                    scopes=None):
    """Compare HEAD vs working tree function across matched records."""
    head_mod = load_head_module(target_file)
    work_mod = load_work_module(target_file)

    head_fn = getattr(head_mod, function_name)
    work_fn = getattr(work_mod, function_name)

    files = sorted(glob.glob(glob_pattern))
    if scopes:
        scope_set = set(scopes)
        files = [f for f in files if any(f"/{s}/" in f or f"/{s}-" in f for s in scope_set)]

    if limit > 0:
        files = files[:limit]

    total = len(files)
    identical = 0
    whitespace_only = 0
    content_changes = []
    dropped_dates = 0
    dropped_designations = 0
    became_empty = 0
    dropped_repeals = 0
    gained_repeals = 0
    structure_became_empty = 0
    structure_empty_to_nonempty = 0
    chrome_leaks = 0
    errors = 0

    for path in files:
        try:
            record = read_record(path)
        except (json.JSONDecodeError, OSError, ValueError):
            errors += 1
            continue

        if record_arg:
            args = [record]
            if root is not None:
                args.append(root)
        else:
            args = [record.get(f) for f in field_names]

        try:
            head_raw = head_fn(*args)
            work_raw = work_fn(*args)
        except (TypeError, ValueError, AttributeError, KeyError, IndexError, OSError, json.JSONDecodeError, RuntimeError):
            errors += 1
            continue

        head_val = to_dict_or_scalar(head_raw)
        work_val = to_dict_or_scalar(work_raw)

        if head_val == work_val:
            identical += 1
            continue

        ident = record.get("identifier") or record.get("basefile") or path

        # Structured dict/model comparisons
        if isinstance(head_val, dict) or isinstance(work_val, dict):
            h_dict = head_val if isinstance(head_val, dict) else {}
            w_dict = work_val if isinstance(work_val, dict) else {}

            h_meta = h_dict.get("metadata") or {}
            w_meta = w_dict.get("metadata") or {}

            # Check repeals
            h_upp = h_meta.get("upphaver") or h_dict.get("upphaver") or []
            w_upp = w_meta.get("upphaver") or w_dict.get("upphaver") or []
            if len(h_upp) > 0 and len(w_upp) == 0:
                dropped_repeals += 1
            elif len(h_upp) == 0 and len(w_upp) > 0:
                gained_repeals += 1

            # Check titles
            h_title = h_meta.get("title") or h_dict.get("title")
            w_title = w_meta.get("title") or w_dict.get("title")
            if h_title and not w_title:
                became_empty += 1

            # Check structure nodes
            h_struct = h_dict.get("structure") or []
            w_struct = w_dict.get("structure") or []
            if len(h_struct) > 0 and len(w_struct) == 0:
                structure_became_empty += 1
            elif len(h_struct) == 0 and len(w_struct) > 0:
                structure_empty_to_nonempty += 1

            # Check for leaked chrome in structure
            leaks = find_chrome_in_structure(w_struct)
            if leaks:
                chrome_leaks += 1

            # Check dates
            h_beslut = h_meta.get("beslutsdatum")
            w_beslut = w_meta.get("beslutsdatum")
            h_ikraft = h_meta.get("ikrafttradandedatum")
            w_ikraft = w_meta.get("ikrafttradandedatum")
            h_utkom = h_meta.get("utkomFranTryck")
            w_utkom = w_meta.get("utkomFranTryck")

            diff_summary = {
                "title": (h_title, w_title) if h_title != w_title else None,
                "upphaver": (h_upp, w_upp) if h_upp != w_upp else None,
                "beslutsdatum": (h_beslut, w_beslut) if h_beslut != w_beslut else None,
                "ikrafttradandedatum": (h_ikraft, w_ikraft) if h_ikraft != w_ikraft else None,
                "utkomFranTryck": (h_utkom, w_utkom) if h_utkom != w_utkom else None,
                "structure_len": (len(h_struct), len(w_struct)) if len(h_struct) != len(w_struct) else None,
                "chrome_leaks": leaks or None,
            }
            for mk in (set(h_meta) | set(w_meta)):
                if mk not in diff_summary and h_meta.get(mk) != w_meta.get(mk):
                    diff_summary[mk] = (h_meta.get(mk), w_meta.get(mk))

            content_changes.append((ident, args[0] if not record_arg else path, diff_summary, None))
            continue

        # String comparisons
        h_str = str(head_val) if head_val is not None else ""
        w_str = str(work_val) if work_val is not None else ""

        h_norm = " ".join(h_str.split())
        w_norm = " ".join(w_str.split())

        if h_norm == w_norm:
            whitespace_only += 1
            continue

        if head_val and not work_val:
            became_empty += 1

        if head_val and work_val:
            h_dates = set(RE_DATE.findall(h_str))
            w_dates = set(RE_DATE.findall(w_str))
            if h_dates - w_dates:
                dropped_dates += 1

            h_desigs = set(RE_DESIGNATION.findall(h_str))
            w_desigs = set(RE_DESIGNATION.findall(w_str))
            if h_desigs - w_desigs:
                dropped_designations += 1

        content_changes.append((ident, args[0], head_val, work_val))

    print(f"Total files evaluated:    {total}")
    print(f"Identical results:        {identical}")
    print(f"Whitespace-only changes:  {whitespace_only}")
    print(f"Content changes:          {len(content_changes)}")
    if errors:
        print(f"Evaluation errors:        {errors}")
    print(f"  - Dropped dates:        {dropped_dates}")
    print(f"  - Dropped designations: {dropped_designations}")
    print(f"  - Became empty / None:  {became_empty}")
    if dropped_repeals or gained_repeals:
        print(f"  - Dropped repeals:      {dropped_repeals}")
        print(f"  - Gained repeals:       {gained_repeals}")
    if structure_became_empty or structure_empty_to_nonempty:
        print(f"  - Structure empty -> body: {structure_empty_to_nonempty}")
        print(f"  - Structure body -> empty: {structure_became_empty}")
    if chrome_leaks:
        print(f"  - Chrome leaks in body: {chrome_leaks}")

    if content_changes:
        sample_count = min(len(content_changes), max_diffs)
        print(f"\nSample of content changes ({sample_count} of {len(content_changes)}):")
        for ident, raw, h, w in content_changes[:sample_count]:
            print(f"\n[{ident}]")
            if isinstance(h, dict) and w is None:
                # Structured summary
                for key, val in h.items():
                    if val is not None:
                        print(f"  {key}: {val[0]!r} -> {val[1]!r}")
            else:
                if raw and raw != h:
                    print(f"  RAW:  {raw}")
                print(f"  HEAD: {h}")
                print(f"  WORK: {w}")

    return len(content_changes)


def main():
    parser = argparse.ArgumentParser(description="Measure diff blast radius on stored records.")
    parser.add_argument("--file", required=True, help="Python source file path (relative to repo root)")
    parser.add_argument("--function", required=True, help="Function name to evaluate")
    parser.add_argument("--records", required=True, help="Glob pattern for record files")
    parser.add_argument("--fields", default="title,identifier", help="Comma-separated field names for args")
    parser.add_argument("--record-arg", action="store_true", help="Pass the entire record dict as first argument")
    parser.add_argument("--root", default=None, help="Optional root directory passed as second argument with --record-arg")
    parser.add_argument("--scopes", default=None, help="Comma-separated scopes to filter records by (e.g. tfs,elsakfs)")
    parser.add_argument("--limit", type=int, default=0, help="Max records to test (0 = all)")
    parser.add_argument("--max-diffs", type=int, default=20, help="Max diff samples to print")
    args = parser.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] if args.scopes else None
    compare_records(args.file, args.function, args.records, fields,
                    limit=args.limit, max_diffs=args.max_diffs,
                    record_arg=args.record_arg, root=args.root, scopes=scopes)


if __name__ == "__main__":
    main()
