#!/usr/bin/env python3
"""Compare a function's output between HEAD and the working tree across stored records.

Measures the blast radius of parser, normaliser, and extraction changes against real
stored data (harvest JSON, artifact JSON) to catch regressions before wrapup.
"""

import argparse
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


def compare_records(target_file, function_name, glob_pattern, field_names, limit=0, max_diffs=20):
    """Compare HEAD vs working tree function across matched records."""
    head_mod = load_head_module(target_file)
    work_mod = load_work_module(target_file)

    head_fn = getattr(head_mod, function_name)
    work_fn = getattr(work_mod, function_name)

    files = sorted(glob.glob(glob_pattern))
    if limit > 0:
        files = files[:limit]

    total = len(files)
    identical = 0
    whitespace_only = 0
    content_changes = []
    dropped_dates = 0
    dropped_designations = 0
    became_empty = 0
    errors = 0

    for path in files:
        try:
            record = read_record(path)
        except (json.JSONDecodeError, OSError, ValueError):
            errors += 1
            continue

        args = [record.get(f) for f in field_names]

        try:
            head_val = head_fn(*args)
            work_val = work_fn(*args)
        except (TypeError, ValueError, AttributeError, KeyError):
            errors += 1
            continue

        if head_val == work_val:
            identical += 1
            continue

        h_norm = " ".join((head_val or "").split())
        w_norm = " ".join((work_val or "").split())

        if h_norm == w_norm:
            whitespace_only += 1
            continue

        # Content changed
        if head_val and not work_val:
            became_empty += 1
        if head_val and work_val:
            h_dates = set(RE_DATE.findall(head_val))
            w_dates = set(RE_DATE.findall(work_val))
            if h_dates - w_dates:
                dropped_dates += 1

            h_desigs = set(RE_DESIGNATION.findall(head_val))
            w_desigs = set(RE_DESIGNATION.findall(work_val))
            if h_desigs - w_desigs:
                dropped_designations += 1

        content_changes.append((record.get("identifier") or path, args[0], head_val, work_val))

    print(f"Total files evaluated:    {total}")
    print(f"Identical results:        {identical}")
    print(f"Whitespace-only changes:  {whitespace_only}")
    print(f"Content changes:          {len(content_changes)}")
    if errors:
        print(f"Evaluation errors:        {errors}")
    print(f"  - Dropped dates:        {dropped_dates}")
    print(f"  - Dropped designations: {dropped_designations}")
    print(f"  - Became empty / None:  {became_empty}")

    if content_changes:
        sample_count = min(len(content_changes), max_diffs)
        print(f"\nSample of content changes ({sample_count} of {len(content_changes)}):")
        for ident, raw, h, w in content_changes[:sample_count]:
            print(f"\n[{ident}]")
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
    parser.add_argument("--limit", type=int, default=0, help="Max records to test (0 = all)")
    parser.add_argument("--max-diffs", type=int, default=20, help="Max diff samples to print")
    args = parser.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    compare_records(args.file, args.function, args.records, fields, args.limit, args.max_diffs)


if __name__ == "__main__":
    main()
