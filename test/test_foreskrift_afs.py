"""AFS page furniture must not become provision or definition text."""

import json
from pathlib import Path

from ferenda.foreskrift import parse as fp
from ferenda.lib.pdftext import Line, Run


def _pages():
    fixture = json.loads((Path(__file__).parent / "files" / "foreskrift"
                          / "afs-2023-2-page-lines.json").read_text())
    return [(page["page"], [Line(**dict(line, runs=[Run(**run) for run in line["runs"]]))
                           for line in page["lines"]]) for page in fixture["pages"]]


def test_afs_chapter_headers_do_not_enter_the_body():
    blocks, _ = fp.parse_body(_pages(), "AFS 2023:2")
    assert not any(block.text.startswith("Kapitel ") for block in blocks)
    assert any(block.kind == "kapitel" and block.num == "2" for block in blocks)
    assert any(block.text == "Systematiskt arbetsmiljöarbete" for block in blocks)


def test_afs_footer_does_not_join_the_last_definition_cell():
    blocks, _ = fp.parse_body(_pages(), "AFS 2023:2")
    definitions = [row for block in blocks if block.kind == "tabell" for row in block.rows]
    meaning = next(row[1] for row in definitions if row[0] == "Kränkande särbehandling")
    assert meaning == (
        "Handlingar som riktas mot en eller flera arbetstagare på ett kränkande sätt "
        "och som kan leda till ohälsa eller att arbetstagare ställs utanför "
        "arbetsplatsens gemenskap.")


def test_afs_running_header_is_removed_for_an_appendix_too():
    lines = _pages()[0][1]
    lines[1].text = "Bilaga 1"
    cleaned = fp._page_body_lines(lines, "AFS 2023:2", 10)
    assert lines[1] not in cleaned


def test_a_chapter_label_without_a_running_identifier_stays():
    lines = _pages()[0][1][1:]
    assert lines[0] in fp._page_body_lines(lines, "AFS 2023:2", 10)


def test_a_distant_chapter_heading_below_the_identifier_stays():
    lines = _pages()[0][1]
    lines[1].top = lines[0].top + 100
    assert lines[1] in fp._page_body_lines(lines, "AFS 2023:2", 10)


def test_a_number_in_the_body_is_not_a_detached_page_footer():
    lines = _pages()[1][1]
    lines[-1].top = lines[-2].bottom + lines[-1].size
    assert lines[-1] in fp._page_body_lines(lines, "AFS 2023:2", 11)
