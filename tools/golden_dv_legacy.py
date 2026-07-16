"""Focused old-corpus golden for newly covered DV legacy formats.

The whole-corpus goldens are change detectors with deliberately broad diff
surfaces.  This smaller acceptance net pins four cases that were absent before
the legacy Word/notis path landed: one direct Word referat and all three notis
bundle generations.  Every case must have the old public URI, exact reference
set, and exact identifier/date/målnummer metadata.  Structure is exact where
the old parser recorded stable labels; modern HFD uses the documented core
skeleton because the new parser intentionally adds the explicit ruling wrapper.

    python tools/golden_dv_legacy.py \
        [--old-root ../ferenda.old/data/dv]
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from accommodanda.lib import (
    compress,
    layout,
)


MANIFEST = (Path(__file__).resolve().parent.parent / "accommodanda" / "dv" /
            "data" / "legacy-golden.json")


def _load_tool(name):
    path = Path(__file__).resolve().parent / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader, "%s has no loader" % path
    spec.loader.exec_module(module)
    return module


def validate(old_root, manifest=MANIFEST):
    golden = _load_tool("golden_dv")
    structure = _load_tool("golden_dv_structure")
    golden_sfs = structure.load_golden_sfs()
    payload = json.loads(Path(manifest).read_text())
    assert payload["version"] == 1, "%s has an unsupported version" % manifest
    assert payload["cases"], "%s contains no cases" % manifest
    failures = []
    for case in payload["cases"]:
        artifact_path = layout.artifact("dv", case["canonical_id"])
        assert compress.exists(artifact_path), "missing artifact %s" % artifact_path
        artifact = json.loads(compress.read_text(artifact_path))
        rdf_path = Path(old_root) / "distilled" / (case["oracle"] + ".rdf")
        xhtml_path = Path(old_root) / "parsed" / (case["oracle"] + ".xhtml")
        assert rdf_path.is_file(), "missing old RDF oracle %s" % rdf_path
        old_uri, old_refs, old_metadata = golden.old_case(rdf_path)
        problems = []
        if old_uri != artifact["uri"]:
            problems.append("uri: %r != %r" % (old_uri, artifact["uri"]))
        if old_refs != golden.new_refs(artifact):
            problems.append("references differ")
        new_metadata = golden.new_metadata(artifact)
        for field in ("identifier", "avgorandedatum", "malnummer"):
            status = golden.metadata_status(
                golden.canonical_metadata(field, old_metadata[field]),
                golden.canonical_metadata(field, new_metadata[field]),
            )
            if status != "exact":
                problems.append("%s: %s" % (field, status))
        if case["structure"]:
            assert xhtml_path.is_file(), "missing old XHTML oracle %s" % xhtml_path
            old_structure = structure.normalize(xhtml_path)
            new_structure = structure.skeleton_from_artifact(artifact)
            if case["structure"] == "core":
                old_structure = structure.core_skeleton(old_structure)
                new_structure = structure.core_skeleton(new_structure)
            else:
                assert case["structure"] == "exact", \
                    "unknown structure mode %s" % case["structure"]
            problems.extend(structure.compare(
                old_structure, new_structure, golden_sfs))
        if problems:
            failures.append((case["canonical_id"], problems))
        else:
            print("PASS %-28s %s" % (case["kind"], case["canonical_id"]))
    if failures:
        for canonical_id, problems in failures:
            print("FAIL %s: %s" % (canonical_id, "; ".join(problems)))
        raise SystemExit("%d focused legacy golden case(s) failed" % len(failures))
    print("%d focused legacy golden cases passed" % len(payload["cases"]))


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--old-root", default="../ferenda.old/data/dv")
    args = parser.parse_args()
    validate(args.old_root)


if __name__ == "__main__":
    main()
