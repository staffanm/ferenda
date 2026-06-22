"""One-off migration to the uniform on-disk layout (layout consolidation step 2).

Relocates each source's existing files to the convention now expected by the
code (accommodanda/lib/layout.py):

    DATA/<dir>/downloaded/<relpath>      raw fetched bytes
    DATA/<dir>/artifact/<relpath>.json   parsed

Filenames are preserved, so the parse manifest's input hashes stay valid and
nothing re-parses. After the move, regenerate the case-law identity index
(`lagen dv reindex`) and re-relate the catalog (`lagen all relate`) -- both
store absolute paths that the move invalidates.

Dry-run by default; pass --apply to move. Idempotent: re-running only relocates
what is still in an old location.

  sfs:        source/<yr> -> downloaded/<yr> (json, flat; archive/ comes along),
              downloaded/<yr> -> downloaded/sfst/<yr> (legacy html),
              register/ -> downloaded/sfsr/   (artifact/ already conforms)
  dv -> dom:  ../domstol/downloaded/ -> dom/downloaded/   (api records)
              dv/artifact/            -> dom/artifact/
              dv/identity-index.json  -> dom/identity-index.json
              dv/downloaded/          left as the legacy raw feed
  forarbete:  <type>/artifact/ -> artifact/<type>/, <type>/* -> downloaded/<type>/
  eurlex:     <yr>/<celex>/artifact.json -> artifact/<yr>/<celex>.json,
              <yr>/<celex>/            -> downloaded/<yr>/<celex>/,
              .watermark-*             -> downloaded/.watermark-*
  kommentar/begrepp: unchanged (artifacts conform; raw is the shared mediawiki/ dump)

Run the eurlex section only with the CELLAR crawl stopped.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accommodanda.lib import layout                                  # noqa: E402

RESERVED = {"downloaded", "artifact"}


def move(src, dst, apply):
    src, dst = Path(src), Path(dst)
    if not src.exists() or src.resolve() == dst.resolve():
        return 0
    print(("MOVE " if apply else "would move ") + "%s -> %s" % (src, dst))
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return 1


def move_children(src_dir, dst_dir, apply, skip=()):
    src_dir = Path(src_dir)
    if not src_dir.is_dir():
        return 0
    n = 0
    for child in sorted(src_dir.iterdir()):
        if child.name in skip:
            continue
        n += move(child, Path(dst_dir) / child.name, apply)
    if apply and not skip and src_dir.is_dir() and not any(src_dir.iterdir()):
        src_dir.rmdir()                       # drop the emptied source directory
    return n


def migrate_sfs(data, apply):
    root = data / "sfs"
    dl = root / "downloaded"
    n = 0
    # legacy SFST html sat directly in downloaded/<year>/; tuck it under sfst/ so
    # the json source can take the flat downloaded/<year>/ slot
    if dl.is_dir():
        for child in sorted(dl.iterdir()):
            if child.is_dir() and child.name not in ("sfst", "sfsr", "archive"):
                n += move(child, dl / "sfst" / child.name, apply)
    n += move_children(root / "source", dl, apply)          # json source -> flat
    n += move_children(root / "register", dl / "sfsr", apply)
    for wm in sorted(root.glob(".watermark*")) + sorted(root.glob(".complete")):
        n += move(wm, dl / wm.name, apply)
    return n


def migrate_dv(data, apply):
    n = move_children(data / "domstol" / "downloaded", data / "dom" / "downloaded",
                      apply)
    domstol = data / "domstol"
    if apply and domstol.is_dir() and not any(domstol.iterdir()):
        domstol.rmdir()
    n += move_children(data / "dv" / "artifact", data / "dom" / "artifact", apply)
    n += move(data / "dv" / "identity-index.json",
              data / "dom" / "identity-index.json", apply)
    return n


def migrate_forarbete(data, apply):
    root = data / "forarbete"
    n = 0
    for typ in sorted(p for p in root.iterdir()
                      if p.is_dir() and p.name not in RESERVED) if root.is_dir() else []:
        n += move_children(typ / "artifact", root / "artifact" / typ.name, apply)
        n += move_children(typ, root / "downloaded" / typ.name, apply,
                           skip={"artifact"})
        if apply and not any(typ.iterdir()):
            typ.rmdir()
    return n


def migrate_eurlex(data, apply):
    root = data / "eurlex"
    n = 0
    for year in sorted(p for p in root.iterdir()
                       if p.is_dir() and p.name not in RESERVED) if root.is_dir() else []:
        for celex in sorted(p for p in year.iterdir() if p.is_dir()):
            n += move(celex / "artifact.json",
                      root / "artifact" / year.name / (celex.name + ".json"), apply)
            n += move(celex, root / "downloaded" / year.name / celex.name, apply)
        if apply and not any(year.iterdir()):
            year.rmdir()
    for wm in sorted(root.glob(".watermark*")):
        n += move(wm, root / "downloaded" / wm.name, apply)
    return n


def migrate(data, apply):
    return (migrate_sfs(data, apply) + migrate_dv(data, apply)
            + migrate_forarbete(data, apply) + migrate_eurlex(data, apply))


def main():
    apply = "--apply" in sys.argv
    total = migrate(layout.DATA, apply)
    print("\n%s %d path(s)." % ("moved" if apply else "would move", total))
    if not apply:
        print("re-run with --apply to perform the move.")
    elif total:
        print("next: 1) lagen dv reindex   (rebuild the moved identity index)")
        print("      2) lagen all relate    (update catalog artifact paths)")


if __name__ == "__main__":
    main()
