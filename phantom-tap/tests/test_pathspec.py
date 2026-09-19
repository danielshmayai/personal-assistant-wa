from __future__ import annotations

import pytest

from phantom_tap.pathspec import PathError, find_paths, require, resolve

DOC = {"d": {"items": [{"id": 1, "n": "a"}, {"id": 2, "n": "b"}], "tok": "x", "empty": []}}


@pytest.mark.parametrize(
    "path,expected",
    [
        ("d.tok", "x"),
        ("d.items[0].n", "a"),
        ("d.items[-1].n", "b"),
        ("d.items[*].id", [1, 2]),
        ("d.empty[*]", []),
    ],
)
def test_reads(path, expected):
    assert resolve(DOC, path) == expected


def test_a_miss_returns_the_default():
    assert resolve(DOC, "d.nope.deeper", "fallback") == "fallback"


def test_require_names_where_it_stopped():
    with pytest.raises(PathError, match=r"d has no key 'nope'; has: empty, items, tok"):
        require(DOC, "d.nope")


def test_wildcard_over_a_non_list_is_an_error():
    with pytest.raises(PathError, match="not a list"):
        require(DOC, "d.tok[*]")


def test_wildcard_skips_entries_that_do_not_fit():
    """Real responses mix shapes; one odd entry must not lose the other ten."""
    mixed = {"items": [{"id": 1}, {"other": 2}, {"id": 3}]}
    assert resolve(mixed, "items[*].id") == [1, 3]


def test_find_paths_locates_a_field_by_predicate():
    assert find_paths(DOC, lambda k, v: k == "tok") == ["d.tok"]
