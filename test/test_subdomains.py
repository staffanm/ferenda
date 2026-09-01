"""Tests for the definite-form subdomain generation (PRD-subdomains.md)."""

import json
import os

import pytest

from ferenda.lib import catalog, compress
from ferenda.subdomains import (
    layout,
    named_span_rows,
    standalone_rows,
    whole_act_rows,
    write_chapter_pages,
    write_sub_tree,
)


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
    assert not os.path.isabs(os.readlink(avtals_link))

    dataskydds_link = tmp_path / "_sub" / "dataskydds.forordningen.nu" / "index.html.br"
    assert dataskydds_link.is_symlink()
    assert dataskydds_link.resolve() == (tmp_path / "eurlex" / "32016R0679.html.br").resolve()
    assert not os.path.isabs(os.readlink(dataskydds_link))

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


def test_standalone_rows_one_row_per_subdomain_page(tmp_path):
    # the file existing under site/subdomain/<zone>/ is the registration --
    # nothing else lists jante.lagen.nu anywhere
    d = tmp_path / "site" / "subdomain" / "lagen.nu"
    d.mkdir(parents=True)
    (d / "jante.md").write_text("---\ntitle: Jantelagen\n---\n\nprosa.\n",
                                encoding="utf-8")
    assert standalone_rows(tmp_path) == {"jante.lagen.nu": "/subdomain/lagen.nu/jante"}


def test_write_sub_tree_merges_whole_act_and_standalone_by_default(tmp_path, monkeypatch):
    # whole_act_rows() reads real repo-wide data (namedlaws.json/namedacts.json)
    # regardless of tmp_path, so this only has to prove the *merge* -- that a
    # standalone page becomes a real row without anyone passing `rows=`
    (tmp_path / "site" / "subdomain" / "lagen.nu").mkdir(parents=True)
    (tmp_path / "site" / "subdomain" / "lagen.nu" / "jante.md").write_text(
        "---\ntitle: Jantelagen\n---\n\nprosa.\n", encoding="utf-8")
    monkeypatch.setattr("ferenda.subdomains.layout.WIKI_ROOT", tmp_path)

    generated = tmp_path / "generated"
    (generated / "subdomain" / "lagen.nu").mkdir(parents=True)
    (generated / "subdomain" / "lagen.nu" / "jante.html").write_text(
        "<html>Jantelagen</html>", encoding="utf-8")

    write_sub_tree(generated)

    served = json.loads((generated / "subdomains.json").read_text(encoding="utf-8"))
    assert served["jante.lagen.nu"] == "/subdomain/lagen.nu/jante"
    assert (generated / "_sub" / "jante.lagen.nu" / "index.html").is_symlink()


def test_write_sub_tree_rejects_a_whole_act_and_standalone_name_collision(tmp_path, monkeypatch):
    (tmp_path / "site" / "subdomain" / "lagen.nu").mkdir(parents=True)
    (tmp_path / "site" / "subdomain" / "lagen.nu" / "avtals.md").write_text(
        "---\ntitle: Avtals\n---\n\nprosa.\n", encoding="utf-8")
    monkeypatch.setattr("ferenda.subdomains.layout.WIKI_ROOT", tmp_path)

    with pytest.raises(ValueError, match="avtals.lagen.nu"):
        write_sub_tree(tmp_path / "generated")


def test_write_sub_tree_symlink_resolves_under_a_different_mount_prefix(tmp_path):
    # ferenda/subdomains.py runs inside the `ferenda` container, which mounts
    # the data root at /app/site/data; nginx mounts the SAME host directory
    # at /usr/share/nginx/generated. An absolute symlink target resolves for
    # whichever container wrote it and is a dangling link for the other one
    # reading it over its own bind mount of the identical directory -- found
    # running this for real on prod (nis2.direktivet.nu 301ed to the apex
    # because nginx's symlink pointed at a path that only existed inside the
    # ferenda container).
    real_root = tmp_path / "real"
    (real_root / "eurlex").mkdir(parents=True)
    (real_root / "eurlex" / "32022L2555.html.br").write_bytes(b"brotli bytes")

    write_sub_tree(real_root, rows={"nis2.direktivet.nu": "/celex/32022L2555"})

    link = real_root / "_sub" / "nis2.direktivet.nu" / "index.html.br"
    assert not os.path.isabs(os.readlink(link))

    # a second, differently-rooted view of the identical directory -- the
    # nginx container's own mount of the same host path
    other_mount = tmp_path / "other-mount-prefix"
    other_mount.symlink_to(real_root)
    other_view_link = other_mount / "_sub" / "nis2.direktivet.nu" / "index.html.br"
    assert other_view_link.read_bytes() == b"brotli bytes"


def test_named_span_rows_matches_the_worked_examples():
    # the real namedlaws.json, not a fixture -- hyreslagen/samtyckeslagen are
    # curated entries, not test data (PRD-subdomains.md section 6)
    rows = named_span_rows()
    assert rows["hyres.lagen.nu"] == "/1970:994#K12-K12"
    assert rows["samtyckes.lagen.nu"] == "/1962:700#K6P1-K6P1"


def _write_namedlaws(path, spans):
    path.write_text(json.dumps({
        lawid: {"label": lawid, "spans": {
            name: {"first": first, "last": last, "reason": reason}}}
        for lawid, name, first, last, reason in spans
    }), encoding="utf-8")


def test_named_span_rows_reads_namedlaws_json(tmp_path, monkeypatch):
    namedlaws = tmp_path / "namedlaws.json"
    _write_namedlaws(namedlaws, [
        ("1970:994", "hyreslagen", "K12", "K12", "..."),
        ("1962:700", "samtyckeslagen", "K6P1", "K6P1", "..."),
    ])
    monkeypatch.setattr("ferenda.subdomains.SFS_NAMEDLAWS", namedlaws)
    assert named_span_rows() == {
        "hyres.lagen.nu": "/1970:994#K12-K12",
        "samtyckes.lagen.nu": "/1962:700#K6P1-K6P1",
    }


def test_named_span_rows_empty_without_any_spans(tmp_path, monkeypatch):
    namedlaws = tmp_path / "namedlaws.json"
    namedlaws.write_text(json.dumps({"1970:994": {"label": "jordabalken"}}),
                         encoding="utf-8")
    monkeypatch.setattr("ferenda.subdomains.SFS_NAMEDLAWS", namedlaws)
    assert named_span_rows() == {}


CHAPTERED_ACT = {
    "uri": "https://lagen.nu/1970:994",
    "metadata": {"properties": {"dcterms:title": "Jordabalk (1970:994)"}},
    "structure": [
        {"type": "kapitel", "id": "K12", "ordinal": "12", "children": [
            {"type": "rubrik", "level": 1, "text": ["12 kap. Hyra"]},
            {"type": "paragraf", "id": "K12P1", "ordinal": "1", "children": [
                {"type": "stycke", "id": "K12P1S1", "beteckning": "1 §",
                 "text": ["Detta kapitel gäller hyra av fast egendom."]},
            ]},
        ]},
    ],
}


def test_write_chapter_pages_renders_the_target_chapter(tmp_path, monkeypatch):
    namedlaws = tmp_path / "namedlaws.json"
    _write_namedlaws(namedlaws, [
        ("1970:994", "hyreslagen", "K12", "K12",
         "Hyreslagen är jordabalkens tolfte kapitel."),
    ])
    monkeypatch.setattr("ferenda.subdomains.SFS_NAMEDLAWS", namedlaws)

    artifact_root = tmp_path / "artifact"
    monkeypatch.setattr("ferenda.subdomains.layout.ARTIFACT", artifact_root)
    art_path = layout.artifact("sfs", "1970:994")
    art_path.parent.mkdir(parents=True, exist_ok=True)
    art_path.write_text(json.dumps(CHAPTERED_ACT), encoding="utf-8")

    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "sfs", [art_path])
    con = catalog.connect(db)

    generated = tmp_path / "generated"
    write_chapter_pages(generated, con)

    dest = generated / "subdomain" / "lagen.nu" / "hyres.html"
    assert compress.exists(dest)
    html = compress.read_text(dest)
    assert "12 kap. Hyra" in html
    assert "hyra av fast egendom" in html
    assert "Hyreslagen" in html
    assert "jordabalkens tolfte kapitel" in html


def test_write_chapter_pages_skips_a_row_whose_act_is_not_built_yet(tmp_path, monkeypatch):
    namedlaws = tmp_path / "namedlaws.json"
    _write_namedlaws(namedlaws, [
        ("1970:994", "hyreslagen", "K12", "K12", "..."),
    ])
    monkeypatch.setattr("ferenda.subdomains.SFS_NAMEDLAWS", namedlaws)
    monkeypatch.setattr("ferenda.subdomains.layout.ARTIFACT", tmp_path / "artifact")

    db = str(tmp_path / "catalog.sqlite")
    catalog.rebuild(db, "sfs", [])
    con = catalog.connect(db)

    generated = tmp_path / "generated"
    write_chapter_pages(generated, con)   # no artifact on disk -- must not raise
    assert not (generated / "subdomain").exists()
