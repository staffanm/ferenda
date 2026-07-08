"""Tests for eurlex.casenames.parse_bindings -- Wikidata SPARQL result bindings
projected to the {celex, name} snapshot records (no network)."""

from accommodanda.eurlex.casenames import parse_bindings


def _rows(*pairs):
    return [{"celex": {"value": c}, "itemLabel": {"value": n}} for c, n in pairs]


def test_projects_and_sorts_by_celex():
    out = parse_bindings(_rows(("62018CJ0311", "Schrems II"),
                               ("61962CJ0026", "Van Gend & Loos")))
    assert out == [{"celex": "61962CJ0026", "name": "Van Gend & Loos"},
                   {"celex": "62018CJ0311", "name": "Schrems II"}]


def test_drops_qid_fallback_labels():
    # the label service returns the bare Q-id when an item has no label in any
    # requested language -- that is not a name
    out = parse_bindings(_rows(("62018CJ0311", "Schrems II"),
                               ("62020CJ0001", "Q98765")))
    assert out == [{"celex": "62018CJ0311", "name": "Schrems II"}]


def test_first_name_wins_per_celex():
    out = parse_bindings(_rows(("62018CJ0311", "Schrems II"),
                               ("62018CJ0311", "Facebook Ireland and Schrems")))
    assert out == [{"celex": "62018CJ0311", "name": "Schrems II"}]
