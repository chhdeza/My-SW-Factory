"""Agent response JSON extraction tests."""

import pytest

from factory.parsing import extract_json


def test_bare_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    text = '```json\n{"verdict": "pass", "findings": []}\n```'
    assert extract_json(text)["verdict"] == "pass"


def test_json_with_surrounding_prose():
    text = 'Here is my plan:\n{"contract": "x", "work_units": []}\nDone.'
    assert extract_json(text)["contract"] == "x"


def test_nested_objects():
    text = 'note {"a": {"b": {"c": 3}}} trailing'
    assert extract_json(text) == {"a": {"b": {"c": 3}}}


def test_no_json_raises():
    with pytest.raises(ValueError, match="no JSON object"):
        extract_json("nothing here")
