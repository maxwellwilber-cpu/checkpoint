"""Tests for path resolution."""

from checkpoint.paths import resolve, exists


DATA = {
    "invoice": {"total": 1200, "currency": "USD"},
    "items": [
        {"name": "widget", "price": 400},
        {"name": "gadget", "price": 800},
    ],
    "tags": ["a", "b"],
}


class TestResolve:
    def test_top_level_key(self):
        assert list(resolve(DATA, "tags")) == [("tags", ["a", "b"])]

    def test_nested_key(self):
        assert list(resolve(DATA, "invoice.total")) == [("invoice.total", 1200)]

    def test_specific_index(self):
        assert list(resolve(DATA, "items[1].name")) == [("items[1].name", "gadget")]

    def test_negative_index(self):
        assert list(resolve(DATA, "items[-1].name")) == [("items[-1].name", "gadget")]

    def test_wildcard_yields_every_element_with_concrete_paths(self):
        # The concrete path matters: a report saying "items[1].price is wrong" is
        # actionable, one saying "items[].price is wrong" is not.
        assert list(resolve(DATA, "items[].price")) == [
            ("items[0].price", 400),
            ("items[1].price", 800),
        ]

    def test_empty_path_returns_whole_document(self):
        assert list(resolve(DATA, "")) == [("", DATA)]


class TestMissing:
    def test_missing_key_yields_nothing_rather_than_raising(self):
        assert list(resolve(DATA, "nope")) == []

    def test_missing_nested_key_yields_nothing(self):
        assert list(resolve(DATA, "invoice.nope.deeper")) == []

    def test_index_out_of_range_yields_nothing(self):
        assert list(resolve(DATA, "items[9].name")) == []

    def test_indexing_a_non_list_yields_nothing(self):
        assert list(resolve(DATA, "invoice[0]")) == []


class TestExists:
    def test_true_when_present(self):
        assert exists(DATA, "invoice.currency") is True

    def test_false_when_absent(self):
        assert exists(DATA, "invoice.missing") is False
