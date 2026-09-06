"""Least-recently-used eviction for the two on-disk render caches.

Both the facsimile PNGs (`lib/facsimile`) and the PDF exports (`api/pdf`) are
pure caches: a deleted entry is re-rendered on the next request, and nothing
else reads either tree. Both had their own copy of the same sweep -- walk the
tree, stat every file, sort by mtime, delete oldest-first -- and the copies had
already drifted apart on the one part that is subtle (see `_room_enough`), so
the copy that did not get the thinking was a bug waiting to happen
(rule:second-use-goes-to-lib).

Recency is mtime, and a *served* entry is not re-touched by either cache, so
this is first-in-first-out in practice. That is the right order here: what a
reader opened once, they opened.
"""

import shutil


def _room_enough(root, freed, need, target_free):
    """Whether the sweep can stop.

    Two conditions, because the two filesystems this runs on answer
    differently. Free space rising is the direct one. But the production
    caches sit on an NFS export where space is not always reclaimed by the time
    the next `unlink` returns -- against that number alone the sweep would keep
    deleting and empty the whole cache. So `need`, the shortfall measured once
    up front, bounds it from the other side.
    """
    return freed >= need or shutil.disk_usage(root).free >= target_free


def sweep(root, paths, *, target_free, cap=None):
    """Delete the least recently used of `paths` until `root`'s filesystem has
    `target_free` bytes free -- and, when `cap` is given, until what is left
    fits `cap` bytes as well. Returns the bytes deleted.

    `paths` is an iterable of candidate files (a caller's own glob), so this
    knows nothing about either cache's layout or its file extension.
    """
    entries = []
    for path in paths:
        try:
            st = path.stat()
        except FileNotFoundError:      # a sibling worker swept it first
            continue
        entries.append((st.st_mtime_ns, st.st_size, path))
    entries.sort()

    total = sum(size for _, size, _ in entries)
    need = max(0, target_free - shutil.disk_usage(root).free)
    freed = 0
    for _mtime, size, path in entries:
        if ((cap is None or total <= cap)
                and _room_enough(root, freed, need, target_free)):
            break
        path.unlink(missing_ok=True)
        freed += size
        total -= size
    return freed
