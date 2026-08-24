"""The publishers (utgivare) whose open articles the LAWPUB platform hosts, as
data.

LAWPUB is one platform, not one journal: it hosts the open-access articles of
several Swedish and Scandinavian publishers, each under its own ``utgivare``
number and publisher icon. Two of them overlap journals this repository
already harvests on their own hosts (Förvaltningsrättslig tidskrift and
Stockholm IP Law Review, from the ``lawreview`` vertical) -- the same
underlying articles are reachable on both -- and a handful more are the
platform's own (Nordisk socialrättslig tidskrift, Europarättslig tidskrift,
Scandinavian studies in law, ...). Nothing in the vertical branches on the
publisher except to read one of these entries.

The identifier's leading abbreviation (``FT``, ``SIPLR``, ``NST``, ...) is the
publisher's own icon-file stem, upper-cased, so it is read off the data rather
than re-stated: ``ft-icon.svg`` -> ``FT``, ``siplr_icon.svg`` -> ``SIPLR``,
``sjf_icon.svg`` -> ``SJF``.
"""

import re
from dataclasses import dataclass

__all__ = ["Publisher", "BY_ICON", "kod_from_icon"]

# the stem the icon file carries ("ft-icon.svg" -> "ft", "siplr_icon.svg"
# -> "siplr"): everything before the first underscore or dash, lower-cased
RE_ICON_STEM = re.compile(r"[a-z][a-z0-9]*", re.I)


def kod_from_icon(icon):
    """The publisher's identifier abbreviation, off its icon file's stem
    (``ft-icon.svg`` -> ``FT``, ``siplr_icon.svg`` -> ``SIPLR``). The item
    carries the icon's full path, so the stem is read off the file name alone
    -- a path taken as a whole would name its first directory, not the icon."""
    assert isinstance(icon, str) and icon, "a publisher icon is a path: %r" % (icon,)
    name = icon.rsplit("/", 1)[-1]
    m = RE_ICON_STEM.search(name)
    assert m is not None, "no publisher stem in the icon's name: %r" % (icon,)
    return m.group(0).upper()


@dataclass(frozen=True)
class Publisher:
    kod: str          # the identifier's leading abbreviation ("FT", "SIPLR", ...)
    utgivare: str     # the platform's /utgivare/<n> number
    namn: str         # the publisher's full name
    icon: str         # /utils/media/<...>.svg, the item's publisher icon


_PUBLISHERS = (
    # nst -- Nordisk socialrättslig tidskrift, on the platform since 2010
    Publisher("NST", "3", "Nordisk socialrättslig tidskrift",
              "/utils/media/nst-icon.svg"),
    # ft -- Förvaltningsrättslig tidskrift; overlaps the lawreview `ft` scope
    # (forvaltningsrattslig.org), the same articles on two hosts
    Publisher("FT", "4", "Förvaltningsrättslig tidskrift",
              "/utils/media/ft-icon.svg"),
    # ert -- Europarättslig tidskrift
    Publisher("ERT", "6", "Europarättslig tidskrift",
              "/utils/media/ert-icon.svg"),
    # iri -- the Swedish Law and Informatics Research Institute
    Publisher("IRI", "7", "The Swedish Law and Informatics Research Institute",
              "/utils/media/iri-icon.svg"),
    # siplr -- Stockholm IP Law Review; overlaps the lawreview `siplr` scope
    # (stockholmiplawreview.com), the same articles on two hosts
    Publisher("SIPLR", "9", "Stockholm IP Law Review",
              "/utils/media/siplr_icon.svg"),
    # sjf -- Stiftelsen Juridisk Fakultetslitteratur (Dataskyddet and its
    # companion volumes)
    Publisher("SJF", "10", "Stiftelsen Juridisk Fakultetslitteratur",
              "/utils/media/sjf_icon.svg"),
    # sisl -- Scandinavian studies in law
    Publisher("SSIL", "11", "Scandinavian studies in law",
              "/utils/media/sisl-icon.svg"),
)

# keyed by the icon's stem, the form the item carries (its publisher icon src)
BY_ICON = {kod_from_icon(p.icon).lower(): p for p in _PUBLISHERS}