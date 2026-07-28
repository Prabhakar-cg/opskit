"""Tests for opskit.file.diffing: the recursive structural comparator (research R6)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from opskit.file import diffing
from opskit.file.models import MISSING

_json_safe = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=10),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=5), children, max_size=3)
    ),
    max_leaves=10,
)


def test_compare_identical_scalars_reports_no_differences():
    assert diffing.compare(1, 1) == ()


def test_compare_differing_scalars_reports_root_difference():
    differences = diffing.compare(1, 2)
    assert len(differences) == 1
    assert differences[0].key_path == "$"
    assert differences[0].left_value == 1
    assert differences[0].right_value == 2


def test_compare_equivalent_dicts_ignores_key_order():
    left = {"a": 1, "b": 2}
    right = {"b": 2, "a": 1}
    assert diffing.compare(left, right) == ()


def test_compare_nested_dict_pinpoints_key_path():
    left = {"server": {"port": 80, "host": "x"}}
    right = {"server": {"port": 443, "host": "x"}}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].key_path == "server.port"
    assert differences[0].left_value == 80
    assert differences[0].right_value == 443


def test_compare_list_index_difference_uses_bracket_notation():
    left = {"server": {"ports": [80, 443]}}
    right = {"server": {"ports": [80, 8443]}}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].key_path == "server.ports[1]"


def test_compare_missing_key_on_right_uses_missing_sentinel():
    left = {"a": 1, "b": 2}
    right = {"a": 1}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].key_path == "b"
    assert differences[0].left_value == 2
    assert differences[0].right_value is MISSING


def test_compare_missing_key_on_left_uses_missing_sentinel():
    left = {"a": 1}
    right = {"a": 1, "b": 2}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].key_path == "b"
    assert differences[0].left_value is MISSING
    assert differences[0].right_value == 2


def test_compare_present_but_null_differs_from_missing():
    left: dict[str, object] = {"a": None}
    right: dict[str, object] = {}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].left_value is None
    assert differences[0].right_value is MISSING


def test_compare_type_mismatch_reports_whole_values():
    left = {"a": [1, 2]}
    right = {"a": {"x": 1}}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].key_path == "a"
    assert differences[0].left_value == [1, 2]
    assert differences[0].right_value == {"x": 1}


def test_compare_list_length_mismatch_reports_extra_elements_as_missing():
    left = {"a": [1, 2, 3]}
    right = {"a": [1, 2]}
    differences = diffing.compare(left, right)
    assert len(differences) == 1
    assert differences[0].key_path == "a[2]"
    assert differences[0].right_value is MISSING


@given(data=_json_safe)
def test_compare_any_value_against_itself_is_equivalent(data):
    assert diffing.compare(data, data) == ()
