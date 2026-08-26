"""Locate, read and rewrite one entry of an SFS ``.graphics`` layer -- the
content model behind the crop-review editor.

The sibling of `editcontent` (which edits markdown regions): both are the
service-layer glue one *kind* of cart draft needs, and both expose the same
tiny protocol to `editcart` -- ``region_of``/``read``/``write``. Nothing else
in the cart machinery knows what a kind holds.

A ``.graphics`` layer places the graphic/table/formula the consolidated SFS
text drops but the published PDF carries. The vision pass (`sfs
ai-includegraphics`) writes it with ``meta.status: "generated"``, which
`annstore.publishable` keeps out of the public render until a human signs the
entry off. Signing off is what this module edits: the reviewer approves an
entry, moves its rectangle, or declares it a whole page.

The draft payload is the entry's *editable state* as canonical JSON --
``page``, ``bbox`` and ``verified``. Carrying `verified` inside the payload is
what makes approving a correct crop a real change rather than a no-op: the
geometry is untouched, but the on-disk entry has no ``verified`` key and the
draft does, so `editcart.upsert` carts it like any other edit.

Provenance (``sfs`` -- which amending act's PDF the region comes from) is
resolved deterministically by the vision pass and is NOT editable here: it
follows from the register's change notes, so a reviewer who finds it wrong has
found a parser bug, not a crop to nudge.
"""

import dataclasses
import functools
import json

from ..lib import annstore, facsimile, layout
from .db import base_sha

KIND = "graphics"

# the entry fields a reviewer may change; everything else in the entry (the
# provenance `sfs`, the `alt` text, the `identity` block) is derived and stays
# byte-for-byte as the generator wrote it
EDITABLE = ("page", "bbox", "verified")


@dataclasses.dataclass(frozen=True)
class Region:
    """The address of one reviewable crop: `ref` is the host statute's basefile
    ("2006:171"), `anchor` the stable gap key ("g-98a4f41c…") the layer and the
    artifact both use. The pair is exactly what `/api/v1/sfs-graphic` takes, so
    a review URL and a public crop URL address the same thing."""
    ref: str
    anchor: str
    kind: str = KIND

    @property
    def key(self):
        return "%s:%s#%s" % (self.kind, self.ref, self.anchor)


def region_of(draft):
    """Rebuild a Region from a stored cart draft."""
    return Region(draft["ref"], draft["anchor"])


def layer_path(ref):
    """The ``.graphics`` layer of one statute."""
    return annstore.path("sfs", ref, ".graphics")


def basefile_of(path):
    """The statute basefile a layer path belongs to -- ``ann/sfs/2006/171.graphics``
    is ``2006:171``. The inverse of `annstore.path`, and the same year/löpnr split
    the artifact tree uses."""
    return "%s:%s" % (path.parent.name, path.stem)


def document_uri(ref):
    """The host statute's uri, read off the layer's own `meta` -- the generator
    copies it from the artifact (`_sfs_write_graphics`), so the layer carries
    everything a rebuild needs without re-reading the artifact for one field."""
    layer = json.loads(layer_path(ref).read_text(encoding="utf-8"))
    uri = (layer.get("meta") or {}).get("uri")
    assert uri, "%s: graphics layer has no meta.uri" % ref
    return uri


def _number(value):
    """A coordinate in the layer's own spelling: whole numbers stay integers.

    The browser and pydantic both hand back floats, so an approval that moved
    nothing would otherwise rewrite ``[86, 274, 422, 394]`` as ``[86.0, …]`` and
    put four changed lines in the commit beside the one the reviewer meant. The
    git diff *is* the review (lib/annstore), so it has to show only what was
    decided."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def canonical(entry):
    """One entry's editable state as canonical JSON -- the draft payload, and
    the text `base_sha` fingerprints. Sorted keys and a fixed indent so the same
    state always produces the same bytes (a `base_sha` that moved because a dict
    reordered would be a phantom conflict). Absent `bbox` is null: the layer
    spells "the whole page" as no bbox at all, and the reviewer's whole-page
    action has to be able to say that."""
    bbox = entry.get("bbox")
    return json.dumps({"page": entry["page"],
                       # `is None`, not truthiness: `[]` is a malformed
                       # rectangle for `parse` to reject, never a silent
                       # "the whole page"
                       "bbox": None if bbox is None else [_number(v) for v in bbox],
                       "verified": bool(entry.get("verified"))},
                      sort_keys=True, indent=2) + "\n"


def parse(text):
    """A draft payload back into the editable fields, validated. Raises
    ValueError -- this is editor-supplied input, so the router turns it into a
    400 rather than letting a bad rectangle reach the crop renderer."""
    try:
        state = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("graphics payload is not JSON: %s" % exc) from None
    if not isinstance(state, dict) or set(state) != set(EDITABLE):
        raise ValueError("graphics payload needs exactly the keys %s"
                         % ", ".join(EDITABLE))
    page, bbox = state["page"], state["bbox"]
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer, not %r" % (page,))
    if bbox is not None and not facsimile.valid_bbox(bbox):
        raise ValueError("bbox must be null or [x0, y0, x1, y1] with "
                         "0 <= x0 < x1 and 0 <= y0 < y1")
    if not isinstance(state["verified"], bool):
        raise ValueError("verified must be true or false")
    return state


def entry_of(region):
    """The layer entry `region` addresses, or None if the layer or the gap is
    gone (a re-parse can retire a gap key, and a reviewer's open tab outlives
    it)."""
    path = layer_path(region.ref)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get(region.anchor)


def read(region):
    """The entry's current editable state as `{text, base_sha}` -- the shape
    `editcart` bases a draft on. A gap that no longer exists reads as empty,
    which makes a stale draft a conflict at commit rather than a crash."""
    entry = entry_of(region)
    if entry is None:
        return {"text": "", "base_sha": ""}
    text = canonical(entry)
    return {"text": text, "base_sha": base_sha(text)}


def write(region, new_text):
    """Apply a draft to the layer file and return `{kind, basefile, path}` for
    the commit and the rebuild.

    Only the three editable fields move; `meta` is preserved byte-for-byte.
    That matters: `meta.status` stays ``generated`` and `meta.inputs` keeps the
    hashes the generator recorded, so `annstore.drifted` still reports the layer
    honestly after a review. Approval is per entry (`verified`), which is also
    what a re-run of the vision pass carries over -- see
    `sfs.graphics.plan_localization`.
    """
    state = parse(new_text)
    path = layer_path(region.ref)
    if not path.exists():
        raise ValueError("no graphics layer for %s" % region.ref)
    layer = json.loads(path.read_text(encoding="utf-8"))
    entry = layer.get(region.anchor)
    if entry is None:
        raise ValueError("no graphic %s in %s" % (region.anchor, region.ref))
    entry["page"] = state["page"]
    if state["bbox"] is None:
        entry.pop("bbox", None)          # no bbox *is* the whole page
    else:
        entry["bbox"] = state["bbox"]
    if state["verified"]:
        entry["verified"] = True
    else:
        entry.pop("verified", None)      # un-approving removes the flag
    annstore.dump(path, layer)
    return {"kind": KIND, "basefile": region.ref, "path": path}


def queue():
    """Every crop still awaiting review, in layer then document order.

    An entry qualifies when `annstore.publishable` says no -- the one policy the
    public render, the crop endpoint and this queue all read, so the queue is
    exactly "what the site will not show yet". A `derived` layer (the road-sign
    geometry) never appears: it is reviewed as code, not per entry.
    """
    pending = []
    for path, meta, gap_key, entry in annstore.layer_entries(".graphics"):
        if annstore.publishable(meta, entry):
            continue
        ref, identity = basefile_of(path), entry.get("identity") or {}
        pending.append({
            "key": Region(ref, gap_key).key,
            "ref": ref, "anchor": gap_key,
            # the fingerprint the cart re-checks: a re-run of the vision pass
            # between listing the queue and deciding an entry is a 409, not a
            # silent overwrite of the reviewer's judgement
            "base_sha": base_sha(canonical(entry)),
            "uri": meta.get("uri"),
            "model": meta.get("model"),
            "alt": entry.get("alt") or "",
            "sort": identity.get("sort") or "",
            "anchor_text": identity.get("anchor") or "",
            "sfs": entry["sfs"], "page": entry["page"],
            "bbox": entry.get("bbox"),
            "pages": _page_count(entry["sfs"]),
        })
    return pending


@functools.cache
def _page_count(src):
    """How many pages the provenance PDF has, or None when it is not mirrored.
    The reviewer needs it to judge a page number ("page 2 of 7") and to page
    through the source when the model picked the wrong one.

    Cached: `page_count` shells out to `pdfinfo`, and the queue asks for one per
    pending entry over far fewer distinct PDFs (41 entries, 24 PDFs today) --
    which on the HDD-class prod box is the difference between an instant queue
    and a visible stall. What makes a stale value safe is not immutability
    (`mirror-pdf --force` does rewrite a PDF in place, and an unmirrored one
    caches as None for the life of the process): it is that `pages` only labels
    the page and bounds the paging buttons. It enters no stored decision, so
    the worst a stale count costs is a reload."""
    pdf = layout.sfs_pdf(src)
    return facsimile.page_count(pdf) if pdf.exists() else None
