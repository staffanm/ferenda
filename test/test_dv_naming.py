"""lib.casenaming -- the canonical, name-prefixed case title shared by the case
page heading, the listings and every inbound citation, plus the case-URI minter."""

from accommodanda.lib import casenaming as naming
from accommodanda.lib.casenaming import case_uri


def _art(referat=(), malnummer=(), uri=None, court="HDO"):
    return {"uri": uri, "referat": list(referat),
            "malnummer": list(malnummer), "court": court}


def test_canonical_referat_is_page_form_not_lopnummer():
    # the page form mints the document URI; the löpnummer never does
    uri = case_uri("NJA 2025 s. 897")
    art = _art(referat=["NJA 2025:58", "NJA 2025 s. 897"], uri=uri)
    assert naming.canonical_referat(art) == "NJA 2025 s. 897"
    assert naming.case_id(art) == "NJA 2025 s. 897"
    assert naming.lopnummer(art) == ["NJA 2025:58"]     # kept as metadata only


def test_raw_verdict_identifies_by_malnummer():
    art = _art(referat=[], malnummer=["Ö 3043-25"],
               uri="https://lagen.nu/dom/HDO_Ö_3043_25")
    assert naming.canonical_referat(art) is None
    assert naming.case_id(art) == "Ö 3043-25"
    assert naming.lopnummer(art) == []


def test_given_name_leads_the_label(monkeypatch):
    monkeypatch.setattr(naming, "_names",
                        lambda: ({"https://lagen.nu/dom/nja/2025s897": "Meteoriten"},
                                 {"Ö 3043-25": "Umgängesstödet"}))
    named = _art(referat=["NJA 2025:58", "NJA 2025 s. 897"],
                 uri=case_uri("NJA 2025 s. 897"))
    assert naming.case_label(named) == "Meteoriten (NJA 2025 s. 897)"
    # a raw verdict can be named before its referat exists (keyed by målnummer)
    raw = _art(referat=[], malnummer=["Ö 3043-25"], uri="https://lagen.nu/x")
    assert naming.case_label(raw) == "Umgängesstödet (Ö 3043-25)"
    # an unnamed case is just its bare identity
    plain = _art(referat=["NJA 2024 s. 5"], uri=case_uri("NJA 2024 s. 5"))
    assert naming.case_label(plain) == "NJA 2024 s. 5"
