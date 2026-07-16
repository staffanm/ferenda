"""Copy one Word original for each filename-proven legacy-only candidate.

The remote legacy tree contains attachment variants and cases already represented
by the API. This importer reads only its path/size listing, resolves those names
against the local API records with the bootstrap filename identity rules, and
gives rsync an exact one-file-per-selected-component manifest. After copying,
the parsed-header sidecar supplies production identity and can merge an opaque
or incorrect filename with an API referat.

    python -m accommodanda.dv.import_legacy pop-os \
        /home/staffan/repos/ferenda.old/data/dv/downloaded \
        --expected-count 1638 --copy

Without ``--copy`` it reports the selection without changing the data tree.
"""

import argparse
import hashlib
import io
import json
import re
import shlex
import subprocess
import tarfile
from pathlib import Path

from ..lib import compress, layout, util
from . import legacy as legacy_parser
from .identity import (
    build_index,
    bundle_identity,
    canonical_court,
    keys,
    legacy_identity,
    norm_malnr,
    norm_referat,
    scan_api,
)

_PLACEHOLDER_NAME = re.compile(r"^(\d{4})_not_(\d+)\.docx?$", re.I)
ADJUDICATION_FILE = Path(__file__).parent / "data" / "legacy-ambiguities.json"


def placeholder_identities(destination):
    """The intentional zero-byte per-notis identity files already imported."""
    identities = set()
    for path in sorted(Path(destination).glob("*/*")):
        if not path.is_file() or path.stat().st_size:
            continue
        match = _PLACEHOLDER_NAME.match(path.name)
        if match:
            identities.add((path.parent.name, *(int(x) for x in match.groups())))
    return identities


def remote_bundle_listing(host, root):
    """List the Word bundle members inside the remote legacy zip archive."""
    code = """\
import json
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
for archive in sorted((root / "zips").rglob("*.zip")):
    if not zipfile.is_zipfile(archive):
        continue
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            if "_notis_" in Path(info.filename).name.lower():
                print(json.dumps({
                    "archive": str(archive.relative_to(root)),
                    "member": info.filename,
                    "size": info.file_size,
                    "zip_mtime": archive.stat().st_mtime_ns,
                }))
"""
    command = "python3 -c %s %s" % (shlex.quote(code), shlex.quote(root))
    result = subprocess.run(
        ["ssh", host, command], check=True, capture_output=True, text=True)
    return [json.loads(line) for line in result.stdout.splitlines()]


def select_bundle_members(rows, placeholders):
    """Select only bundle members whose range contains an imported placeholder.

    Repeated zip snapshots may carry the same member.  Keep its newest copy;
    multiple differently named ranges are retained when each covers a needed
    case.
    """
    candidates = {}
    matched = set()
    for row in rows:
        identity = bundle_identity(row["member"])
        if not identity:
            continue
        court, year, first, last = identity
        covered = {(c, y, ordinal) for c, y, ordinal in placeholders
                   if (c, y) == (court, year) and first <= ordinal <= last}
        if not covered:
            continue
        key = Path(row["member"]).name
        if key not in candidates or row["zip_mtime"] > candidates[key]["zip_mtime"]:
            candidates[key] = row
        matched |= covered
    selected = []
    for row in candidates.values():
        court, year, _, _ = bundle_identity(row["member"])
        selected.append({
            **row,
            "destination": "%s/%d/%s" % (
                court, year, Path(row["member"]).name),
        })
    return sorted(selected, key=lambda row: row["destination"]), placeholders - matched


def select_all_bundle_members(rows):
    """Every recognized bundle member, deduplicated across zip snapshots."""
    selected = {}
    for row in rows:
        identity = bundle_identity(row["member"])
        if not identity:
            continue
        court, year, _, _ = identity
        destination = "%s/%d/%s" % (
            court, year, Path(row["member"]).name)
        candidate = {**row, "destination": destination}
        if (destination not in selected or
                row["zip_mtime"] > selected[destination]["zip_mtime"]):
            selected[destination] = candidate
    return sorted(selected.values(), key=lambda row: row["destination"])


def copy_bundle_members(host, root, destination, selected):
    """Stream selected members out of remote zips as one tar, then materialize."""
    code = """\
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
rows = json.load(sys.stdin)
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
    for row in rows:
        archive = root / row["archive"]
        with zipfile.ZipFile(archive) as zipped:
            data = zipped.read(row["member"])
        info = tarfile.TarInfo(row["destination"])
        info.size = len(data)
        info.mtime = archive.stat().st_mtime
        tar.addfile(info, io.BytesIO(data))
"""
    command = "python3 -c %s %s" % (shlex.quote(code), shlex.quote(root))
    result = subprocess.run(
        ["ssh", host, command], input=json.dumps(selected).encode(),
        check=True, capture_output=True)
    destination = Path(destination)
    expected = {row["destination"]: row for row in selected}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        for member in tar:
            assert member.isfile() and member.name in expected, \
                "unexpected remote tar member %r" % member.name
            source = tar.extractfile(member)
            assert source, "missing tar payload for %s" % member.name
            out = destination / member.name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(source.read())
            assert out.stat().st_size == expected[member.name]["size"], \
                "%s has the wrong size after copy" % out
            seen.add(member.name)
    assert seen == set(expected), "remote tar omitted selected bundle members"


def remote_listing(host, root):
    """Return ``[(relative_path, size), ...]`` for remote DOC/DOCX files."""
    command = (
        "find %s -type f \\( -iname '*.doc' -o -iname '*.docx' \\) "
        "-printf '%%P\\t%%s\\n'" % shlex.quote(root)
    )
    result = subprocess.run(
        ["ssh", host, command], check=True, capture_output=True, text=True)
    rows = []
    for line in result.stdout.splitlines():
        relpath, size = line.rsplit("\t", 1)
        assert relpath and "\t" not in relpath, "invalid remote path %r" % relpath
        rows.append((relpath, int(size)))
    return rows


def remote_zero_notis_identities(host, root):
    """Exact identities represented by legacy zero-byte placeholder files."""
    command = (
        "find %s -type f -size 0 "
        "\\( -iname '*.doc' -o -iname '*.docx' \\) -printf '%%P\\n'"
        % shlex.quote(root)
    )
    result = subprocess.run(
        ["ssh", host, command], check=True, capture_output=True, text=True)
    identities = set()
    for relpath in result.stdout.splitlines():
        path = Path(relpath)
        match = _PLACEHOLDER_NAME.match(path.name)
        if match and path.parent.name in ("HDO", "HFD", "REG"):
            identities.add(
                (path.parent.name, *(int(value) for value in match.groups())))
    return identities


def legacy_records(rows):
    """Remote listing rows as the record shape consumed by ``build_index``."""
    records = []
    for relpath, size in rows:
        path = Path(relpath)
        court = path.parent.name
        malnummer, referat = legacy_identity(court, path.name)
        assert malnummer is not None, "unrecognized legacy Word file %s" % relpath
        records.append({
            "store": "dv",
            "court": canonical_court(court),
            "path": path.as_posix(),
            "malnummer": malnummer,
            "referat": referat,
            "size": size,
        })
    return records


def select_files(api_records, records):
    """One preferred member per canonical legacy-only case.

    A non-empty original wins over a zero-byte placeholder; ties use the stable
    path order that the identity index and legacy parser already use.
    """
    cases = [case for case in build_index(api_records, records)
             if case["sources"] == ["dv"]]
    selected = []
    for case in cases:
        members = [member for member in case["members"]
                   if member["store"] == "dv"]
        assert members, "%s has no legacy member" % case["canonical_id"]
        member = min(members, key=lambda item: (item["size"] == 0, item["path"]))
        selected.append({"canonical_id": case["canonical_id"], **member})
    selected.sort(key=lambda item: item["path"])
    assert len({item["path"] for item in selected}) == len(selected), \
        "one remote file was selected for several canonical cases"
    return selected


def partition_ambiguous(api_records, selected):
    """Split selected cases by whether their målnummer has an API candidate.

    Such cases remain separate under the current conservative identity rules
    because the målnummer names several API or legacy components. They are not
    proven API absences and must not enter a tightly bounded legacy-only copy
    until their identity is adjudicated.
    """
    api_malnummer = {
        key for record in api_records
        for key in keys(record["court"], record["malnummer"], record["referat"])
        if key[0] == "M"
    }
    confirmed, ambiguous = [], []
    for item in selected:
        own = keys(item["court"], item["malnummer"], item["referat"])
        (ambiguous if any(key in api_malnummer for key in own
                          if key[0] == "M") else confirmed).append(item)
    return confirmed, ambiguous


def adjudicate_ambiguous(api_records, selected, review_root):
    """Prove each ambiguous Word original duplicate of one API publication.

    A shared målnummer only establishes the review set.  A duplicate requires
    an exact parsed referat and date match.  The one verdict-only legacy file
    without a referat is accepted only when målnummer+date select one API record
    and its full editorial summary is byte-for-byte equal after whitespace
    normalization.  Anything else fails rather than being guessed.
    """
    review_root = Path(review_root)
    adjudications = []
    for item in selected:
        path = review_root / item["path"]
        assert path.is_file(), "missing ambiguous review original %s" % path
        assert path.stat().st_size == item["size"], \
            "%s changed size during ambiguity review" % path
        avgorande = legacy_parser.parse_legacy_file(path)
        own_malnummer = {norm_malnr(value) for value in avgorande.malnummer}
        candidates = [
            record for record in api_records
            if record["court"] == item["court"]
            and own_malnummer & {norm_malnr(value)
                                for value in record["malnummer"]}
        ]
        own_referat = {norm_referat(value) for value in avgorande.referat}
        referat_matches = [
            record for record in candidates
            if own_referat & {norm_referat(value) for value in record["referat"]}
        ]
        if len(referat_matches) == 1:
            match = referat_matches[0]
            assert avgorande.avgorandedatum == match["avgorandedatum"], \
                "%s: referat match disagrees on date" % item["path"]
            reason = "same-referat-and-date"
        else:
            assert not referat_matches, \
                "%s: referat matches several API publications" % item["path"]
            date_matches = [
                record for record in candidates
                if record["avgorandedatum"] == avgorande.avgorandedatum
            ]
            assert len(date_matches) == 1, \
                "%s: no unique date match among API candidates" % item["path"]
            match = date_matches[0]
            raw = json.loads(compress.read_text(
                util.load_relpath(layout.DATA, match["path"])))
            legacy_summary = re.sub(r"\s+", " ", avgorande.sammanfattning or "").strip()
            api_summary = re.sub(r"\s+", " ", raw.get("sammanfattning") or "").strip()
            assert legacy_summary and legacy_summary == api_summary, \
                "%s: date match has different editorial summary" % item["path"]
            reason = "same-malnummer-date-and-summary"
        adjudications.append({
            "legacy_path": item["path"],
            "legacy_size": item["size"],
            "legacy_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "legacy_malnummer": avgorande.malnummer,
            "legacy_referat": avgorande.referat,
            "legacy_avgorandedatum": avgorande.avgorandedatum,
            "disposition": "api-duplicate",
            "reason": reason,
            "api_path": match["path"],
            "api_uuid": match["uuid"],
            "api_referat": match["referat"],
            "api_avgorandedatum": match["avgorandedatum"],
        })
    return sorted(adjudications, key=lambda row: row["legacy_path"])


def partition_adjudicated(ambiguous, path=ADJUDICATION_FILE):
    """Separate reviewed API duplicates from still-unresolved candidates."""
    path = Path(path)
    if not path.is_file():
        return [], ambiguous
    payload = json.loads(path.read_text())
    assert payload["version"] == 1, "%s has an unsupported version" % path
    reviewed = {row["legacy_path"]: row for row in payload["cases"]}
    assert len(reviewed) == len(payload["cases"]), \
        "%s repeats a legacy path" % path
    resolved, unresolved = [], []
    for item in ambiguous:
        row = reviewed.get(item["path"])
        if row is None:
            unresolved.append(item)
            continue
        assert row["disposition"] == "api-duplicate", \
            "%s has an unsupported disposition for %s" % (path, item["path"])
        assert row["legacy_size"] == item["size"], \
            "%s changed size since adjudication" % item["path"]
        resolved.append(item)
    stale = set(reviewed) - {item["path"] for item in ambiguous}
    assert not stale, "%s contains stale ambiguity reviews: %s" % (
        path, sorted(stale))
    return resolved, unresolved


def copy_files(host, root, destination, selected):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = b"\0".join(item["path"].encode() for item in selected) + b"\0"
    subprocess.run([
        "rsync", "--archive", "--protect-args", "--from0", "--files-from=-",
        "--prune-empty-dirs", "%s:%s/" % (host, root.rstrip("/")),
        str(destination) + "/",
    ], input=manifest, check=True)
    for item in selected:
        path = destination / item["path"]
        assert path.is_file(), "rsync did not copy %s" % item["path"]
        assert path.stat().st_size == item["size"], \
            "%s has the wrong size after copy" % item["path"]


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("host")
    parser.add_argument("root")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--notis-bundles", action="store_true",
                        help="copy only bundle members needed by zero-byte placeholders")
    parser.add_argument("--all-notis-bundles", action="store_true",
                        help="copy every notis bundle member, without placeholders")
    parser.add_argument("--copy", action="store_true")
    parser.add_argument(
        "--adjudicate-ambiguous", metavar="REVIEW_DIR",
        help="parse locally staged ambiguous originals and write their API matches")
    parser.add_argument(
        "--adjudication-out", type=Path, default=ADJUDICATION_FILE,
        help="machine-readable ambiguity ledger (default: %(default)s)")
    args = parser.parse_args()

    if args.notis_bundles or args.all_notis_bundles:
        placeholders = placeholder_identities(layout.DV_LEGACY_DOWNLOADED)
        rows = remote_bundle_listing(args.host, args.root)
        if args.all_notis_bundles:
            selected = select_all_bundle_members(rows)
            print("%d bundle members selected (%d bytes)" % (
                len(selected), sum(row["size"] for row in selected)))
        else:
            selected, missing = select_bundle_members(rows, placeholders)
            print("%d zero-byte notis placeholders; %d bundle members selected "
                  "(%d bytes); %d placeholders have no matching member range" % (
                      len(placeholders), len(selected),
                      sum(row["size"] for row in selected), len(missing)))
            for identity in sorted(missing):
                print("  no bundle: %s/%d_not_%d" % identity)
        if args.copy:
            copy_bundle_members(args.host, args.root, layout.DV_NOTIS_BUNDLES,
                                selected)
            print("copied %d bundle members to %s" %
                  (len(selected), layout.DV_NOTIS_BUNDLES))
            if args.all_notis_bundles:
                indexed, extra = legacy_parser.write_notis_index(
                    layout.DV_NOTIS_BUNDLES,
                    remote_zero_notis_identities(args.host, args.root))
                print("indexed %d exact placeholder identities; excluded %d "
                      "bundle headings with no placeholder" % (indexed, extra))
        else:
            print("dry run; pass --copy to transfer the selected bundle members")
        return

    rows = remote_listing(args.host, args.root)
    api_records = scan_api(layout.DOM_DOWNLOADED)
    candidates = select_files(api_records, legacy_records(rows))
    selected, ambiguous = partition_ambiguous(api_records, candidates)
    if args.adjudicate_ambiguous:
        adjudications = adjudicate_ambiguous(
            api_records, ambiguous, args.adjudicate_ambiguous)
        payload = {"version": 1, "case_count": len(adjudications),
                   "cases": adjudications}
        util.write_atomic(args.adjudication_out, json.dumps(
            payload, ensure_ascii=False, indent=2).encode())
        print("adjudicated %d ambiguous originals as API duplicates -> %s" %
              (len(adjudications), args.adjudication_out))
        return
    adjudicated, ambiguous = partition_adjudicated(ambiguous)
    print("identity result: %d filename-selected legacy candidates; "
          "%d confirmed API absences, "
          "%d adjudicated API duplicates, %d unresolved målnummer matches" %
          (len(candidates), len(selected), len(adjudicated), len(ambiguous)))
    if args.expected_count is not None:
        assert len(selected) == args.expected_count, \
            "expected %d legacy-only cases, found %d" % (
                args.expected_count, len(selected))
    size = sum(item["size"] for item in selected)
    zero = sum(item["size"] == 0 for item in selected)
    print("%d confirmed legacy-only cases: %d bytes, %d zero-byte originals"
          % (len(selected), size, zero))
    if args.copy:
        copy_files(args.host, args.root, layout.DV_LEGACY_DOWNLOADED, selected)
        print("copied %d files to %s" %
              (len(selected), layout.DV_LEGACY_DOWNLOADED))
        indexed = legacy_parser.write_direct_index(layout.DV_LEGACY_DOWNLOADED)
        print("indexed %d non-empty direct Word identities in %s" %
              (indexed, layout.DV_LEGACY_INDEX))
    else:
        print("dry run; pass --copy to transfer the selected files")


if __name__ == "__main__":
    main()
