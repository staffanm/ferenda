"""The ``stats`` vertical: corpus-wide measurements, rendered as one page.

Unlike every other source here there is nothing to download and no document to
parse -- ``stats`` *reads* the finished corpus (the catalog and the artifact
tree) and writes a single artifact holding every measurement, which the render
step turns into ``/statistik``.

Two verbs, deliberately split:

* ``lagen stats compute`` -- run the measurements, write
  ``artifact/stats/statistik.json``. This is the expensive half (it walks the
  SFS, eurlex and förarbete artifact trees), so it is not repeated per render.
* ``lagen stats generate`` -- render that artifact to the page.

The split is what makes the numbers *diffable*: two artifacts from different
builds can be compared to see what actually moved in the corpus, and the page is
a pure projection of one. It also keeps the architecture's rule intact -- the
artifact on disk is the source of truth, the page is derived.

Like ``site`` and ``remisser`` this carries no citation graph, so it is absent
from ``build.ARTIFACTS`` and is never related, indexed or dumped.
"""
