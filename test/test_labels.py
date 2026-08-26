"""The four reader-facing name forms (lib/labels.py) -- short_id / short_title /
official_title / descriptive_label -- per source (C2 / I2)."""
from ferenda.lib import labels


def test_sfs_named_law_short_title_is_the_colloquial_name():
    art = {"uri": "https://lagen.nu/2018:585",
           "metadata": {"properties": {
               "dcterms:title": "Säkerhetsskyddslag (2018:585)",
               "dcterms:identifier": "SFS 2018:585 i lydelse enligt SFS 2026:764"}}}
    lb = labels.document_labels("sfs", art)
    assert lb.short_id == "SFS 2018:585"
    assert lb.short_title == "Säkerhetsskyddslagen"       # namedlaws, capitalised
    assert lb.official_title == "Säkerhetsskyddslag (2018:585)"


def test_sfs_unnamed_law_short_title_drops_the_designation():
    # a law with no namedlaws entry: short title is the official title minus its
    # "(YYYY:NN)" designation, wherever it sits in the string
    art = {"uri": "https://lagen.nu/2016:1145",
           "metadata": {"properties": {
               "dcterms:title": "Lag (2016:1145) om offentlig upphandling"}}}
    lb = labels.document_labels("sfs", art)
    assert lb.short_id == "SFS 2016:1145"
    assert lb.short_title == "Lag om offentlig upphandling"


def test_eurlex_act_short_id_is_the_designation():
    art = {"uri": "https://lagen.nu/ext/celex/32016R0679", "celex": "32016R0679",
           "doctype": "regulation", "shortname": "dataskyddsförordningen",
           "abbr": "GDPR",
           "title": "Europaparlamentets och rådets förordning (EU) 2016/679 av den "
                    "27 april 2016 om skydd för fysiska personer",
           "label": "(EU) 2016/679 Allmän dataskyddsförordning"}
    lb = labels.document_labels("eurlex", art)
    assert lb.short_id == "(EU) 2016/679"
    assert lb.short_title == "dataskyddsförordningen (GDPR)"
    assert lb.official_title.startswith("Europaparlamentets och rådets förordning")
    # the compact citing form names the act the way its own page's h1 does. It
    # used to be the artifact's `label` ("… Allmän dataskyddsförordning"), so a
    # rail and the page called one act two different things
    assert lb.descriptive_label == "(EU) 2016/679 dataskyddsförordningen (GDPR)"


def test_eurlex_citing_form_keeps_the_designation_it_can_read():
    """The short name is only spliced onto a designation the *label* carries.
    Rebuilding from `short_id` unconditionally would print raw CELEX
    ("32003L0097") where the label already has "2003/97/EG"."""
    art = {"uri": "https://lagen.nu/ext/celex/32003L0097", "celex": "32003L0097",
           "doctype": "directive", "shortname": "Text av betydelse för EES.",
           "title": "Europaparlamentets och rådets direktiv 2003/97/EG",
           "label": "2003/97/EG Text av betydelse för EES."}
    lb = labels.document_labels("eurlex", art)
    assert lb.descriptive_label == "2003/97/EG Text av betydelse för EES."
    assert "32003L0097" not in lb.descriptive_label


def test_eurlex_unnamed_judgment_has_number_but_no_name():
    art = {"uri": "https://lagen.nu/ext/celex/62018CJ0001", "celex": "62018CJ0001",
           "doctype": "judgment", "shortname": "C-1/18", "label": "C-1/18",
           "title": "Domstolens dom (femte avdelningen) den 20 juni 2019"}
    lb = labels.document_labels("eurlex", art)
    assert lb.short_id == "C-1/18"
    assert lb.short_title == ""                            # unnamed -> no h1 name
    assert lb.official_title.startswith("Domstolens dom")


def test_eurlex_titleless_judgment_falls_back_to_its_case_number():
    # the legacy court pages open straight into the parties, so the artifact
    # carries no title -- the name to show is then the case number, never the
    # URI tail (which headed 3 373 judgments "ext/celex/61979CJ0155")
    art = {"uri": "https://lagen.nu/ext/celex/61979CJ0155", "celex": "61979CJ0155",
           "doctype": "judgment", "shortname": "C-155/79", "label": "C-155/79",
           "title": ""}
    lb = labels.document_labels("eurlex", art)
    assert lb.short_id == lb.official_title == "C-155/79"
    assert "ext/celex" not in lb.official_title


def test_eurlex_named_judgment_splits_number_and_name():
    art = {"uri": "https://lagen.nu/ext/celex/62018CJ0311", "celex": "62018CJ0311",
           "doctype": "judgment", "shortname": "Schrems II",
           "label": "C-311/18 (Schrems II)",
           "title": "Domstolens dom (stora avdelningen) den 16 juli 2020"}
    lb = labels.document_labels("eurlex", art)
    assert lb.short_id == "C-311/18"
    assert lb.short_title == "Schrems II"


def test_eurlex_treaty_uses_the_curated_name():
    # a founding/consolidated treaty carries no extractable short title, so the
    # curated Swedish name stands in as both short and official title; short_id is
    # the CELEX, and the revision '(NN)' suffix is stripped before the lookup (E1)
    art = {"uri": "https://lagen.nu/ext/celex/12016M/TXT", "celex": "12016M/TXT",
           "doctype": "treaty", "title": "12016M/TXT"}
    lb = labels.document_labels("eurlex", art)
    assert lb.short_id == "12016M/TXT"
    assert lb.short_title == "Fördraget om Europeiska unionen (konsoliderad version 2016)"
    assert lb.official_title == lb.short_title
    revised = {**art, "celex": "12019W/TXT(02)",
               "uri": "https://lagen.nu/ext/celex/12019W/TXT(02)"}
    assert labels.document_labels("eurlex", revised).short_title.startswith(
        "Avtalet om Förenade kungarikets utträde")


def test_dv_named_case_leads_with_the_name():
    art = {"uri": "https://lagen.nu/dom/nja/2025s897",
           "label": "Meteoriten (NJA 2025 s. 897)", "referat": ["NJA 2025 s. 897"]}
    lb = labels.document_labels("dv", art)
    assert lb.short_id == "NJA 2025 s. 897"
    assert lb.short_title == "Meteoriten"


def test_dv_unnamed_case_has_no_name():
    art = {"uri": "https://lagen.nu/dom/hfd/2011ref4", "label": "HFD 2011 ref. 4"}
    lb = labels.document_labels("dv", art)
    assert lb.short_id == "HFD 2011 ref. 4"
    assert lb.short_title == ""


def test_dv_pre_referat_named_case_splits_on_the_parenthetical():
    art = {"uri": "https://lagen.nu/dom/hd/O4337-25/2026-07-14",
           "label": "Underhåll och lagval (Högsta domstolen, mål Ö 4337-25)",
           "referat": []}
    lb = labels.document_labels("dv", art)
    assert lb.short_id == "Högsta domstolen, mål Ö 4337-25"
    assert lb.short_title == "Underhåll och lagval"


def test_forarbete_eyebrow_is_the_identifier():
    art = {"uri": "https://lagen.nu/prop/2019/20:1", "doctype": "prop",
           "identifier": "Prop. 2019/20:1", "title": "Budgetpropositionen för 2020"}
    lb = labels.document_labels("forarbete", art)
    assert lb.short_id == "Prop. 2019/20:1"
    assert lb.short_title == "Budgetpropositionen för 2020"


def test_hudoc_eyebrow_is_the_application_number():
    art = {"uri": "https://lagen.nu/dom/echr/001-202613", "itemid": "001-202613",
           "title": "CASE OF AVENDI OOD v. BULGARIA",
           "metadata": {"applicationNumber": ["48786/09"]}}
    lb = labels.document_labels("hudoc", art)
    assert lb.short_id == "no. 48786/09"
    # de-shouted for readers (the official title keeps the Court's form);
    # a company suffix's casing is the known cost ("OOD" -> "Ood")
    assert lb.short_title == "Avendi Ood v. Bulgaria"
    assert lb.official_title == "CASE OF AVENDI OOD v. BULGARIA"


def test_coe_treaty_name_comes_from_the_dataset():
    art = {"uri": "https://lagen.nu/ext/coe/005", "number": "005",
           "identifier": "ETS No. 005",
           "title": "Convention for the Protection of Human Rights and "
                    "Fundamental Freedoms"}
    lb = labels.document_labels("coe", art)
    assert lb.short_title == "Europakonventionen (EKMR)"       # label + abbr
    assert lb.official_title.startswith("Convention for the Protection")


def test_icrc_eyebrow_is_the_acronym():
    art = {"uri": "https://lagen.nu/ext/icrc/375", "number": "375",
           "title": "Convention (III) relative to the Treatment of Prisoners of War."}
    lb = labels.document_labels("icrc", art)
    assert lb.short_id == "GK III"
    # the h1 capitalizes the curated running-text name, as _sfs does
    assert lb.short_title == "Tredje Genèvekonventionen (GK III)"


def test_untc_eyebrow_is_the_acronym():
    # keyed on the UNTS registration, which is what an untc artifact's `number`
    # carries since the identity moved off the MTDSG chapter id
    art = {"uri": "https://lagen.nu/ext/untc/I-24841", "number": "I-24841",
           "title": "Convention against Torture and Other Cruel, Inhuman or "
                    "Degrading Treatment or Punishment"}
    lb = labels.document_labels("untc", art)
    assert lb.short_id == "CAT"
    # the h1 capitalizes the curated running-text name, as _sfs does
    assert lb.short_title == "Tortyrkonventionen (CAT)"


def test_icc_eyebrow_is_the_case_not_the_document():
    art = {"uri": "https://lagen.nu/ext/icc/ICC-01_14-01_18-403",
           "docnumber": "ICC-01/14-01/18-403",
           "title": "The Prosecutor v. Alfred Yekatom and Patrice-Edouard Ngaïssona",
           "metadata": {"caseNumber": "ICC-01/14-01/18",
                        "documentNumber": "ICC-01/14-01/18-403"}}
    lb = labels.document_labels("icc", art)
    assert lb.short_id == "ICC-01/14-01/18"                     # the case, not -403
    assert lb.short_title.startswith("The Prosecutor v.")


def test_begrepp_forms_are_all_the_term():
    # a concept has no identifier apart from its name. Without a handler it fell
    # to `_generic`, whose id-of-last-resort is the uri tail -- and the
    # descriptive form is what an inbound rail prints, so a statute's Begrepp
    # section read "begrepp/Misshandel" (D3).
    art = {"uri": "https://lagen.nu/begrepp/Misshandel", "title": "Misshandel"}
    lb = labels.document_labels("begrepp", art)
    assert lb == ("Misshandel",) * 4


def test_begrepp_without_a_title_falls_back_to_the_uri_tail():
    art = {"uri": "https://lagen.nu/begrepp/Allmän_handling"}
    assert labels.document_labels("begrepp", art).descriptive_label \
        == "Allmän_handling"


def test_hudoc_captions_are_deshouted_for_readers():
    """The Court prints "CASE OF VLASOV v. RUSSIA"; every reader-facing label
    reads "Vlasov v. Russia" while the official title keeps the Court's own
    form. Initials, small words and multi-party captions all survive; a
    caption without the filing prefix passes through untouched."""
    assert labels.case_name("CASE OF VLASOV v. RUSSIA") == "Vlasov v. Russia"
    assert labels.case_name("CASE OF GILLAN AND QUINTON v. THE UNITED KINGDOM") \
        == "Gillan and Quinton v. the United Kingdom"
    assert labels.case_name("CASE OF S.W. v. THE UNITED KINGDOM") \
        == "S.W. v. the United Kingdom"
    # decisions shout without the filing prefix -- the gate is shoutedness
    assert labels.case_name("DOBRE v. ROMANIA") == "Dobre v. Romania"
    # ...so an already-mixed caption passes through
    assert labels.case_name("Affaire linguistique belge") \
        == "Affaire linguistique belge"
    # the recorded cost of blind title-casing: particles and Mc-names
    assert labels.case_name("CASE OF MCCANN AND OTHERS v. THE UNITED KINGDOM") \
        == "Mccann and Others v. the United Kingdom"
    # ...and a party initial "V." mid-caption reads as the separator
    assert labels.case_name("CASE OF A.V. v. UKRAINE") == "A.v. v. Ukraine"
    lb = labels.document_labels("hudoc", {
        "title": "CASE OF VLASOV v. RUSSIA",
        "metadata": {"applicationNumber": ["78146/01"]}})
    assert lb.short_title == "Vlasov v. Russia"
    assert lb.descriptive_label == "Vlasov v. Russia"
    assert lb.official_title == "CASE OF VLASOV v. RUSSIA"
    assert lb.short_id == "no. 78146/01"
