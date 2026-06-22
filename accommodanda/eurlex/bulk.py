"""Unpack a CELLAR bulk "legislation" data dump into the per-CELEX layout the
incremental harvester produces, so the whole corpus can be imported from the
official dumps instead of fetched document-by-document.

A dump is a set of big zips for one release date:

  LEG_MTD_<date>.zip       {work-uuid}/tree_non_inferred.rdf      metadata
  LEG_EN_FMX_<date>.zip    {work-uuid}/fmx4/*.fmx.xml             Formex, English
  LEG_SV_FMX_<date>.zip    {work-uuid}/fmx4/*.fmx.xml             Formex, Swedish
  LEG_EN_HTML_<date>.zip   {work-uuid}/{html,xhtml}/*             HTML, English
  LEG_SV_HTML_<date>.zip   ...

Everything is keyed by the opaque cellar work UUID; the CELEX (our basefile) lives
only in the metadata rdf (`resource_legal_id_celex`). We read that to map each
UUID to its CELEX, then write, per work and language, the files a download leaves:

  {root}/{year}/{celex}/notice.ttl          the metadata, as turtle
  {root}/{year}/{celex}/{lang}.fmx4[.zip]   the Formex (bare act, or bundle)
  {root}/{year}/{celex}/{lang}.{html,xhtml} the HTML manifestation

so `lagen eurlex parse` then treats them exactly like downloaded documents.
"""

import io
import os
import re
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .download import (SECTORS, content_filename, doc_dir, parse_notice,
                       write_atomic, write_watermark)

LANG = {"EN": "eng", "SV": "swe"}    # dump language code -> our 3-letter code

# the dump's INCL.ELEMENT graphics (model certificates, figures) are TIFFs,
# overwhelmingly *uncompressed* -- recompressing them losslessly to WebP shrinks
# them to a few percent of their size. WebP can't hold these modes, so convert.
_WEBP_MODES = {"1", "L", "LA", "P", "RGB", "RGBA"}
_WEBP_METHOD = 4                     # 4 matches 6's size at lower cost (measured)
_INCL_ELEMENT = re.compile(rb"<INCL\.ELEMENT\b[^>]*>")
# encode the graphics in batches: the uncompressed TIFFs are huge (a single
# certificate-heavy work holds ~10 GB of them), so we never hold more than a
# batch of raw images in memory at once -- ~2x the pool keeps it well fed
_ENCODE_BATCH = 16
RE_CELEX = re.compile(rb"resource_legal_id_celex[^>]*>([^<]+)<")
RE_WORKDATE = re.compile(rb"work_date_document[^>]*>([^<]+)<")
_SECTOR_BY_DIGIT = {s.digit: name for name, s in SECTORS.items()}


def _find_zip(bundle, pattern):
    matches = sorted(bundle.glob(pattern))
    return matches[0] if matches else None


def _uuid_index(zf):
    """work-uuid -> [member names] for one language dump (one namelist pass)."""
    index = defaultdict(list)
    for name in zf.namelist():
        if not name.endswith("/"):
            index[name.split("/", 1)[0]].append(name)
    return index


def _celex_of(rdf_bytes):
    match = RE_CELEX.search(rdf_bytes)
    return match.group(1).decode().strip() if match else None


def _workdate_of(rdf_bytes):
    match = RE_WORKDATE.search(rdf_bytes)
    return match.group(1).decode().strip() if match else None


def _recompress(raw):
    """A TIFF graphic re-encoded as lossless WebP. Returns (bytes, extension);
    keeps the original bytes (`.tif`) for the rare source image PIL cannot
    decode, so a corrupt graphic neither aborts the import nor is lost."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError):
        return raw, ".tif"
    if im.mode not in _WEBP_MODES:
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, "WEBP", lossless=True, method=_WEBP_METHOD)
    return out.getvalue(), ".webp"


def _rewrite_filerefs(xml, renamed):
    """Point each INCL.ELEMENT FILEREF at the recompressed graphic's new name
    (and its TYPE at WEBP). `renamed` maps old basename -> new basename."""
    if not renamed:
        return xml

    def repl(m):
        el = m.group(0)
        ref = re.search(rb'FILEREF="([^"]+)"', el)
        new = ref and renamed.get(ref.group(1).rsplit(b"/", 1)[-1].decode())
        if new:
            el = el.replace(ref.group(0), b'FILEREF="%s"' % new.encode())
            el = el.replace(b'TYPE="TIFF"', b'TYPE="WEBP"')
        return el

    return _INCL_ELEMENT.sub(repl, xml)


def _bundle_fmx(zf, members, encode=map):
    """A work's Formex content as one blob. A lone act with no annexes and no
    graphics is returned bare (the raw `.xml`); anything with several parts or
    `.tif` graphics is packed into a zip -- the `.xml` content plus those
    graphics (model certificates, figures), dropping the .doc(.fmx).xml wrapper
    (a manifest, not content). Graphics are recompressed to WebP and FILEREFs
    repointed to match. None if the work has no Formex content. The caller names
    the blob by sniffing it (bare -> `.fmx4`, zip -> `.fmx4.zip`).

    `encode` maps _recompress over the graphics -- pass a thread pool's `.map`
    to parallelise (WebP encoding frees the GIL); the zip itself is read
    serially since ZipFile is not thread-safe."""
    xml_parts = sorted(n for n in members if n.endswith(".xml")
                       and not n.endswith((".doc.xml", ".doc.fmx.xml")))
    if not xml_parts:
        return None
    tif_parts = sorted(n for n in members if n.endswith(".tif"))
    if len(xml_parts) == 1 and not tif_parts:
        return zf.read(xml_parts[0])
    images = {}                      # new basename -> bytes
    renamed = {}                     # old basename -> new basename
    for i in range(0, len(tif_parts), _ENCODE_BATCH):
        batch = tif_parts[i:i + _ENCODE_BATCH]
        for name, (data, ext) in zip(batch,
                                     encode(_recompress, [zf.read(n) for n in batch])):
            base = name.rsplit("/", 1)[-1]
            newbase = base[:-4] + ext
            images[newbase] = data
            if newbase != base:
                renamed[base] = newbase
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name in xml_parts:
            out.writestr(name.rsplit("/", 1)[-1],
                         _rewrite_filerefs(zf.read(name), renamed))
        for newbase, data in images.items():
            out.writestr(newbase, data)
    return buf.getvalue()


def _pick_html(members):
    """The main HTML manifestation member (prefer xhtml; the main file has the
    shortest name -- numbered sub-parts and note .jpg/.xml sit beside it)."""
    cands = [n for n in members if n.endswith((".html", ".xhtml"))]
    pool = [n for n in cands if "/xhtml/" in n] or cands
    return min(pool, key=len) if pool else None


def unpack_bulk(source, root, languages=("swe", "eng"), limit=None, log=print):
    """Import a bulk dump (a directory, or any file inside it) into root.
    Returns the number of works written."""
    bundle = Path(source)
    bundle = bundle if bundle.is_dir() else bundle.parent
    root = Path(root)

    mtd_zip = _find_zip(bundle, "LEG_MTD_*.zip")
    if mtd_zip is None:
        raise SystemExit("no LEG_MTD_*.zip found in %s" % bundle)
    mtd = zipfile.ZipFile(mtd_zip)
    fmx = {our: zipfile.ZipFile(z) for code, our in LANG.items()
           if (z := _find_zip(bundle, "LEG_%s_FMX_*.zip" % code))}
    html = {our: zipfile.ZipFile(z) for code, our in LANG.items()
            if (z := _find_zip(bundle, "LEG_%s_HTML_*.zip" % code))}
    log("dump: metadata + fmx%s + html%s; indexing ..."
        % (sorted(fmx), sorted(html)))
    fmx_idx = {l: _uuid_index(z) for l, z in fmx.items()}
    html_idx = {l: _uuid_index(z) for l, z in html.items()}

    written = skipped = 0
    marks = {}                       # sector digit -> latest work date in the dump
    # recompressing the TIFF graphics to WebP is the bottleneck; fan it out
    # across cores (threads suffice -- libwebp releases the GIL during encode)
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
        for name in mtd.namelist():
            if not name.endswith("tree_non_inferred.rdf"):
                continue
            uuid = name.split("/", 1)[0]
            rdf = mtd.read(name)
            celex = _celex_of(rdf)
            if not celex or len(celex) < 5:
                skipped += 1
                continue
            wdate = _workdate_of(rdf)
            if wdate and wdate > marks.get(celex[0], ""):
                marks[celex[0]] = wdate
            dest = doc_dir(root, celex)
            write_atomic(dest / "notice.ttl", parse_notice(rdf).ttl())
            for lang in languages:
                if lang in fmx and uuid in fmx_idx[lang]:
                    blob = _bundle_fmx(fmx[lang], fmx_idx[lang][uuid], encode=pool.map)
                    if blob:
                        name = content_filename(lang, "fmx4", blob)
                        write_atomic(dest / name, blob)
                        # a re-run may flip zip-ness; drop any other fmx4 variant
                        # (incl. the legacy `.zip.fmx4` name)
                        for old in dest.glob(lang + ".*fmx4*"):
                            if old.name != name:
                                old.unlink()
                if lang in html and uuid in html_idx[lang]:
                    member = _pick_html(html_idx[lang][uuid])
                    if member:
                        ext = ".xhtml" if member.endswith(".xhtml") else ".html"
                        write_atomic(dest / (lang + ext), html[lang].read(member))
            written += 1
            if written % 500 == 0:
                log("  %d works unpacked ..." % written)
            if limit and written >= limit:
                break
    # the dump is the baseline: set each sector's watermark to its latest work
    # date, so a later `download` is incremental from here rather than re-walking
    # the whole sector (and its PDF-only long tail) from the first year.
    if not limit:
        for digit, date in sorted(marks.items()):
            sector = _SECTOR_BY_DIGIT.get(digit)
            if sector:
                write_watermark(root, sector, date)
                log("watermark[%s] = %s (dump baseline)" % (sector, date))
    log("unpacked %d works into %s (%d skipped: no CELEX)" % (written, root, skipped))
    return written
