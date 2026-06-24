"""The numbering-gap fix (§3d): a provision whose id the minter suppressed by
content-equality dedup is *unanchored* -- it has no fragment of its own and no
id-bearing ancestor, so a self-reference would attribute to an empty source.
The old pipeline omitted those, so `Projection.inline` drops self-links from
unanchored provisions (an empty `context`) while keeping references to other
laws. Anchored provisions are unaffected."""

from accommodanda.sfs.nf import Projection
from accommodanda.lib.lagrum import LagrumParser


def _proj(basefile="1962:700"):
    # inline only uses the refparser (parse_text + self_law_uri); no minter.
    return Projection(minter=None, refparser=LagrumParser({}, basefile))


def _uris(runs):
    return [r["uri"] for r in runs if isinstance(r, dict)]


def test_self_law_uri_is_the_self_reference_prefix():
    assert LagrumParser({}, "1962:700").self_law_uri == "https://lagen.nu/1962:700"
    # a balk basefile keeps its bare numeric suffix, so self-references mint
    # against the full id ("1736:0123 1" -> ".../1736:0123_1", not ".../1736:0123",
    # which the old pipeline collapsed -- losing the _1/_2 distinction)
    assert LagrumParser({}, "1736:0123 1").self_law_uri == "https://lagen.nu/1736:0123_1"
    assert LagrumParser({}, "1736:0123 2").self_law_uri == "https://lagen.nu/1736:0123_2"


def test_anchored_provision_keeps_self_and_external_refs():
    runs = _proj().inline("Enligt 5 § och lagen (1994:451) om annat.", "K1P1S1")
    uris = _uris(runs)
    assert "https://lagen.nu/1962:700#K1P5" in uris   # self-reference kept
    assert "https://lagen.nu/1994:451" in uris        # external reference kept


def test_unanchored_provision_drops_self_keeps_external():
    runs = _proj().inline("Enligt 5 § och lagen (1994:451) om annat.", "")
    uris = _uris(runs)
    assert not any(u.startswith("https://lagen.nu/1962:700") for u in uris)
    assert "https://lagen.nu/1994:451" in uris


def test_unanchored_provision_drops_andringshanvisning():
    # the closing "Lag (2019:464)." is a self #L change-reference -- dropped when
    # the provision is unanchored (it has no source stycke to credit)
    anchored = _proj().inline("Detta ska gälla. Lag (2019:464).", "K1P1S1")
    assert _uris(anchored) == ["https://lagen.nu/1962:700#L2019:464"]
    unanchored = _proj().inline("Detta ska gälla. Lag (2019:464).", "")
    assert _uris(unanchored) == []
