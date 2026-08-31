"""Tests for the definite-form subdomain generation (PRD-subdomains.md)."""

import json

from ferenda.subdomains import whole_act_rows, write_sub_tree


def test_whole_act_rows_matches_the_worked_examples():
    rows = whole_act_rows()
    assert rows["avtals.lagen.nu"] == "/1915:218"
    assert rows["dataskydds.förordningen.nu"] == "/celex/32016R0679"
    assert rows["nis2.direktivet.nu"] == "/celex/32022L2555"
    assert rows["cer.direktivet.nu"] == "/celex/32022L2557"
    assert rows["tryckfrihets.förordningen.nu"] == "/1949:105"


def test_a_regulations_abbreviation_does_not_land_on_direktivet_nu():
    # GDPR (32016R0679) is a regulation: its abbr must not mint a
    # gdpr.direktivet.nu row, only dataskydds.förordningen.nu via its label.
    rows = whole_act_rows()
    assert "gdpr.direktivet.nu" not in rows
    assert not any(v == "/celex/32016R0679" and h.endswith("direktivet.nu")
                   for h, v in rows.items())


def test_a_superseded_name_resolves_to_the_current_act_only():
    # "sjölagen" meant 1891:35_s.1 until 1994-10-01, then 1994:1009 -- only
    # the latter should get the subdomain.
    rows = whole_act_rows()
    assert rows["sjö.lagen.nu"] == "/1994:1009"
    assert "1891:35" not in rows["sjö.lagen.nu"]


def test_write_sub_tree_symlinks_a_br_only_page_and_keeps_the_suffix(tmp_path):
    (tmp_path / "eurlex").mkdir()
    (tmp_path / "1915:218.html.br").write_bytes(b"brotli bytes")
    (tmp_path / "eurlex" / "32016R0679.html.br").write_bytes(b"brotli bytes")

    write_sub_tree(tmp_path, rows={
        "avtals.lagen.nu": "/1915:218",
        "dataskydds.förordningen.nu": "/celex/32016R0679",
    })

    avtals_link = tmp_path / "_sub" / "avtals.lagen.nu" / "index.html.br"
    assert avtals_link.is_symlink()
    assert avtals_link.resolve() == (tmp_path / "1915:218.html.br").resolve()

    dataskydds_link = tmp_path / "_sub" / "dataskydds.forordningen.nu" / "index.html.br"
    assert dataskydds_link.is_symlink()
    assert dataskydds_link.resolve() == (tmp_path / "eurlex" / "32016R0679.html.br").resolve()

    map_text = (tmp_path / "subdomains.map").read_text(encoding="utf-8")
    assert "avtals.lagen.nu avtals.lagen.nu;" in map_text
    # both the real zone and its registered ascii twin (section 4)
    assert "dataskydds.xn--frordningen-rfb.nu dataskydds.forordningen.nu;" in map_text
    assert "dataskydds.forordningen.nu dataskydds.forordningen.nu;" in map_text

    served = json.loads((tmp_path / "subdomains.json").read_text(encoding="utf-8"))
    assert served == {
        "avtals.lagen.nu": "/1915:218",
        "dataskydds.förordningen.nu": "/celex/32016R0679",
    }


def test_write_sub_tree_skips_a_row_with_no_generated_page(tmp_path):
    write_sub_tree(tmp_path, rows={"avtals.lagen.nu": "/1915:218"})
    assert not (tmp_path / "_sub" / "avtals.lagen.nu").exists()
    assert (tmp_path / "subdomains.map").read_text(encoding="utf-8") == ""
    assert json.loads((tmp_path / "subdomains.json").read_text(encoding="utf-8")) == {}


def test_write_sub_tree_maps_an_idn_slug_to_its_a_label_and_ascii_twin(tmp_path):
    (tmp_path / "1960:729.html").write_text("<html></html>", encoding="utf-8")

    write_sub_tree(tmp_path, rows={"upphovsrätts.lagen.nu": "/1960:729"})

    map_text = (tmp_path / "subdomains.map").read_text(encoding="utf-8")
    assert "xn--upphovsrtts-s8a.lagen.nu upphovsratts.lagen.nu;" in map_text
    assert "upphovsratts.lagen.nu upphovsratts.lagen.nu;" in map_text
    assert (tmp_path / "_sub" / "upphovsratts.lagen.nu" / "index.html").is_symlink()


def test_write_sub_tree_does_not_collide_two_zones_folding_to_one_label(tmp_path):
    # "dataskyddslagen" (lagen.nu) and "Dataskyddsförordningen" (förordningen.nu)
    # both fold to the label "dataskydds" -- each needs its own directory and
    # its own act, not one silently overwriting the other's symlink.
    (tmp_path / "eurlex").mkdir()
    (tmp_path / "2018:218.html").write_text("<html>dataskyddslagen</html>", encoding="utf-8")
    (tmp_path / "eurlex" / "32016R0679.html").write_text("<html>gdpr</html>", encoding="utf-8")

    write_sub_tree(tmp_path, rows={
        "dataskydds.lagen.nu": "/2018:218",
        "dataskydds.förordningen.nu": "/celex/32016R0679",
    })

    lagen_link = tmp_path / "_sub" / "dataskydds.lagen.nu" / "index.html"
    forordningen_link = tmp_path / "_sub" / "dataskydds.forordningen.nu" / "index.html"
    assert lagen_link.resolve() == (tmp_path / "2018:218.html").resolve()
    assert forordningen_link.resolve() == (tmp_path / "eurlex" / "32016R0679.html").resolve()

    map_text = (tmp_path / "subdomains.map").read_text(encoding="utf-8")
    assert "dataskydds.lagen.nu dataskydds.lagen.nu;" in map_text
    assert "dataskydds.xn--frordningen-rfb.nu dataskydds.forordningen.nu;" in map_text
    assert "dataskydds.forordningen.nu dataskydds.forordningen.nu;" in map_text
