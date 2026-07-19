"""The JVM side of lib/poi: a persistent subprocess that owns jpype/Apache POI.

Run only as ``python -m accommodanda.lib.poi_worker`` (poi.read() spawns it);
nothing may import this module -- that would pull the _jpype C extension into
the importing process, which is exactly what the subprocess boundary exists to
prevent. Protocol: one JSON-encoded path per stdin line, one JSON reply per
stdout line -- ``{"paras": [[text, bold, in_table], ...]}`` on success,
``{"error": "..."}`` on failure. Exits on stdin EOF, i.e. when the parent goes
away. The JVM starts lazily on the first request.
"""

import json
import os
import sys
from pathlib import Path

import jpype
import jpype.imports

from .poi import _OLE2_MAGIC, _ZIP_MAGIC, _clean

# repo-root vendor/poi (accommodanda/lib/poi_worker.py -> parents[2] is the
# repo root), the location tools/fetch_poi.sh writes to.
_JARS = sorted((Path(__file__).parents[2] / "vendor" / "poi").glob("*.jar"))


def _ensure_jvm():
    if not jpype.isJVMStarted():
        assert _JARS, "no POI jars found under vendor/poi/ (run tools/fetch_poi.sh)"
        # We ship only log4j-api (no -core); point it at the built-in
        # SimpleLogger so it stops printing "could not find a logging
        # provider" to stdout, and silence that logger.
        jpype.startJVM(
            "-Dlog4j2.loggerContextFactory="
            "org.apache.logging.log4j.simple.SimpleLoggerContextFactory",
            "-Dorg.apache.logging.log4j.simplelog.StatusLogger.level=OFF",
            classpath=[str(j) for j in _JARS],
        )


def _read_hwpf(path):
    # POI/jpype classes resolve only after startJVM, so unlike every other
    # import in this package these must stay in-function (see _ensure_jvm) --
    # rule:no-infunction-imports sanctioned exception.
    from java.io import FileInputStream  # ty: ignore[unresolved-import]
    from org.apache.poi.hwpf import HWPFDocument  # ty: ignore[unresolved-import]

    doc = HWPFDocument(FileInputStream(str(path)))
    try:
        rng = doc.getRange()
        out = []
        for i in range(rng.numParagraphs()):
            p = rng.getParagraph(i)
            bold = any(p.getCharacterRun(j).isBold()
                       for j in range(p.numCharacterRuns()))
            out.append((_clean(str(p.text())), bold, bool(p.isInTable())))
        return out
    finally:
        doc.close()   # also closes the FileInputStream; skipping it on a
        # malformed doc would leak a JVM-side file handle per failure


def _read_xwpf(path):
    # POI/jpype classes resolve only after startJVM (see _read_hwpf).
    from java.io import FileInputStream  # ty: ignore[unresolved-import]
    from org.apache.poi.xwpf.usermodel import (  # ty: ignore[unresolved-import]
        XWPFDocument,
    )

    doc = XWPFDocument(FileInputStream(str(path)))
    try:
        out = []

        def emit(p, in_table):
            bold = any(r.isBold() for r in p.getRuns())
            out.append((_clean(str(p.getText())), bold, in_table))

        for el in doc.getBodyElements():
            kind = str(el.getClass().getSimpleName())
            if kind == "XWPFParagraph":
                emit(el, False)
            elif kind == "XWPFTable":
                for row in el.getRows():
                    for cell in row.getTableCells():
                        for p in cell.getParagraphs():
                            emit(p, True)
        return out
    finally:
        doc.close()


def _extract(path):
    _ensure_jvm()
    magic = path.read_bytes()[:8]
    if magic.startswith(_ZIP_MAGIC):
        return _read_xwpf(path)
    if magic.startswith(_OLE2_MAGIC):
        return _read_hwpf(path)
    raise ValueError("%s: neither OLE2 (.doc) nor ZIP (.docx): %r" % (path, magic))


def main():
    # Claim the protocol channel, then point fd 1 at stderr: anything the JVM
    # or POI writes to "stdout" lands on stderr instead of corrupting the
    # one-JSON-line-per-request framing the parent is parsing.
    channel = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    for line in sys.stdin:
        try:
            paras = _extract(Path(json.loads(line)))
            reply = {"paras": paras}
        except Exception as e:  # noqa: BLE001 -- process boundary: the failure
            # is serialized and re-raised as-is in the parent (poi.read), not
            # swallowed (rule:no-catch-log-continue)
            reply = {"error": "%s: %s" % (type(e).__name__, e)}
        channel.write(json.dumps(reply) + "\n")
        channel.flush()


if __name__ == "__main__":
    main()
