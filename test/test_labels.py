"""The four reader-facing name forms (lib/labels.py) -- short_id / short_title /
official_title / descriptive_label -- per source (C2 / I2)."""
from accommodanda.lib import labels


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


def test_eurlex_unnamed_judgment_has_number_but_no_name():
    art = {"uri": "https://lagen.nu/ext/celex/62018CJ0001", "celex": "62018CJ0001",
           "doctype": "judgment", "shortname": "C-1/18", "label": "C-1/18",
           "title": "Domstolens dom (femte avdelningen) den 20 juni 2019"}
    lb = labels.document_labels("eurlex", art)
    assert lb.short_id == "C-1/18"
    assert lb.short_title == ""                            # unnamed -> no h1 name
    assert lb.official_title.startswith("Domstolens dom")


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
    art = {"uri": "https://lagen.nu/prop/2019/20:1", "type": "prop",
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
    assert lb.short_title == "CASE OF AVENDI OOD v. BULGARIA"


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
    assert lb.short_title == "tredje Genèvekonventionen (GK III)"


def test_untc_eyebrow_is_the_acronym():
    art = {"uri": "https://lagen.nu/ext/untc/IV-9", "number": "IV-9",
           "title": "Convention against Torture and Other Cruel, Inhuman or "
                    "Degrading Treatment or Punishment"}
    lb = labels.document_labels("untc", art)
    assert lb.short_id == "CAT"
    assert lb.short_title == "tortyrkonventionen (CAT)"


def test_icc_eyebrow_is_the_case_not_the_document():
    art = {"uri": "https://lagen.nu/ext/icc/ICC-01_14-01_18-403",
           "docnumber": "ICC-01/14-01/18-403",
           "title": "The Prosecutor v. Alfred Yekatom and Patrice-Edouard Ngaïssona",
           "metadata": {"caseNumber": "ICC-01/14-01/18",
                        "documentNumber": "ICC-01/14-01/18-403"}}
    lb = labels.document_labels("icc", art)
    assert lb.short_id == "ICC-01/14-01/18"                     # the case, not -403
    assert lb.short_title.startswith("The Prosecutor v.")
