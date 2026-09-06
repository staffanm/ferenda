"""Bounded ZIP reading: what a downloaded archive is allowed to expand to.

A zip states its own uncompressed sizes, and a harvest that trusts them reads
whatever an upstream file says -- a few hundred bytes of archive can declare
gigabytes of members. Every archive this pipeline opens comes off the network
(EUR-Lex Formex manifestations, the EDPB's language bundles, a riksdagen
.docx), so the budgets below are read *before* the first byte is expanded, and
the file is refused rather than truncated: a Formex zip half-read is a
document with its annexes missing, which is worse than a failed harvest.

Four separate ceilings, because they fail in different ways:

  * ``max_members``   -- an archive of a million tiny files exhausts the walk
    rather than memory.
  * ``max_member_bytes`` -- one member no reader has room for.
  * ``max_total_bytes``  -- many members that are each acceptable.
  * ``max_ratio``     -- the compression ratio a zip bomb needs; a legitimate
    XML/PDF bundle stays far under it.

Names are checked too: a member is read by name into a caller-chosen path in
two of the three call sites, and ``../`` in a zip is the oldest archive trick
there is.
"""

import zipfile

from .errors import UpstreamChanged

MAX_MEMBERS = 4096
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
# Formex XML compresses about 8:1 and a .docx about 5:1; 200 leaves four
# orders of headroom over that and still refuses the shapes a bomb needs
MAX_RATIO = 200


class ArchiveTooLarge(UpstreamChanged):
    """A downloaded archive declares more than the caller allowed to expand."""


class UnsafeArchiveMember(UpstreamChanged):
    """A member name that would escape the directory it is read into, or a
    member this pipeline has no key for."""


def check_name(name):
    """`name`, refused when it is absolute, walks upward, or is a Windows
    drive path. Returns the name so a caller can use it inline."""
    if name.startswith(("/", "\\")) or ":" in name.split("/", 1)[0]:
        raise UnsafeArchiveMember("archive member %r is an absolute path" % name)
    if any(part == ".." for part in name.replace("\\", "/").split("/")):
        raise UnsafeArchiveMember("archive member %r walks out of the archive"
                                  % name)
    return name


def check(zf, *, max_members=MAX_MEMBERS, max_member_bytes=MAX_MEMBER_BYTES,
          max_total_bytes=MAX_TOTAL_BYTES, max_ratio=MAX_RATIO):
    """Read `zf`'s directory and refuse it if expanding it would exceed the
    budgets. Costs one directory read and expands nothing."""
    infos = zf.infolist()
    if len(infos) > max_members:
        raise ArchiveTooLarge("archive declares %d members, over the %d allowed"
                              % (len(infos), max_members))
    total = compressed = 0
    for info in infos:
        check_name(info.filename)
        if info.flag_bits & 0x1:
            raise UnsafeArchiveMember("archive member %r is encrypted"
                                      % info.filename)
        if info.file_size > max_member_bytes:
            raise ArchiveTooLarge("archive member %r declares %d bytes, over "
                                  "the %d allowed"
                                  % (info.filename, info.file_size,
                                     max_member_bytes))
        total += info.file_size
        compressed += info.compress_size
    if total > max_total_bytes:
        raise ArchiveTooLarge("archive declares %d bytes of members, over the "
                              "%d allowed" % (total, max_total_bytes))
    # a stored (uncompressed) archive has ratio 1 and no compressed size to
    # divide by when it is empty; both are fine and neither is a bomb
    if compressed and total / compressed > max_ratio:
        raise ArchiveTooLarge("archive expands %.0f:1, over the %d:1 allowed"
                              % (total / compressed, max_ratio))
    return zf


def open_zip(file, **budgets):
    """`zipfile.ZipFile` over `file` (a path or a binary file object) with its
    directory already checked against the budgets. Use as a context manager,
    exactly like `ZipFile` itself."""
    zf = zipfile.ZipFile(file)
    # no cleanup branch on a refusal: `check` expands nothing, and the
    # unreturned ZipFile drops its handle as it goes out of scope
    check(zf, **budgets)
    return zf


def read(zf, name, *, max_bytes=MAX_MEMBER_BYTES):
    """One member's bytes, refused when the member expands past `max_bytes`.

    The declared size is checked first (`check` has usually done it already),
    and the read is still bounded: a zip directory can understate what the
    member's own stream produces."""
    check_name(name)
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise ArchiveTooLarge("archive member %r declares %d bytes, over the "
                              "%d allowed" % (name, info.file_size, max_bytes))
    with zf.open(name) as member:
        data = member.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ArchiveTooLarge("archive member %r expands past the %d bytes "
                              "allowed" % (name, max_bytes))
    return data
