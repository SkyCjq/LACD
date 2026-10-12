"""Unit tests: core/versioning.py."""

import pytest

from lacd.core.versioning import (
    classify_bump,
    compare,
    is_supported,
    parse_version,
)


def test_parse_valid():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.0.0") == (0, 0, 0)
    assert parse_version("10.20.30") == (10, 20, 30)
    assert parse_version("  1.0.0\n") == (1, 0, 0)  # surrounding whitespace tolerated


@pytest.mark.parametrize(
    "bad",
    ["", "1", "1.2", "1.2.3.4", "v1.2.3", "1.2.x", "a.b.c", "1.2.3-rc1",
     "1.2.3+build", "01.2.3", "1.02.3", "-1.2.3", "1.2.", ".1.2.3", None, 123],
)
def test_parse_invalid_raises_value_error(bad):
    with pytest.raises(ValueError):
        parse_version(bad)


def test_compare():
    assert compare("1.0.0", "1.0.1") == -1
    assert compare("1.0.1", "1.0.0") == 1
    assert compare("1.0.0", "1.0.0") == 0
    assert compare("2.0.0", "1.9.9") == 1
    assert compare("1.10.0", "1.9.9") == 1  # numeric, not lexicographic


def test_compare_invalid_raises():
    with pytest.raises(ValueError):
        compare("1.0", "1.0.0")


@pytest.mark.parametrize(
    "from_v,to_v,expected",
    [
        ("1.0.0", "1.0.0", "same"),
        ("1.0.0", "1.0.1", "patch"),
        ("1.0.0", "1.1.0", "minor"),
        ("1.0.0", "2.0.0", "major"),
        ("1.2.3", "1.2.4", "patch"),
        ("1.2.3", "1.3.0", "minor"),
        ("1.2.3", "2.0.0", "major"),
        ("2.0.0", "1.9.9", "downgrade"),
        ("1.1.0", "1.0.9", "downgrade"),
    ],
)
def test_classify_bump(from_v, to_v, expected):
    assert classify_bump(from_v, to_v) == expected


def test_classify_bump_invalid_raises():
    with pytest.raises(ValueError):
        classify_bump("1.0", "1.0.1")


def test_is_supported_with_latest_window():
    class Policy:
        latest_version = "1.5.0"
        supported_minors = 2

    assert is_supported("1.5.0", Policy()) is True
    assert is_supported("1.4.0", Policy()) is True   # window: 1.4..1.5
    assert is_supported("1.3.0", Policy()) is False  # outside window
    assert is_supported("2.0.0", Policy()) is False  # different major


def test_is_supported_with_minimum_version():
    class Policy:
        minimum_version = "1.2.0"

    assert is_supported("1.2.0", Policy()) is True
    assert is_supported("1.1.9", Policy()) is False


def test_is_supported_invalid_version_raises():
    with pytest.raises(ValueError):
        is_supported("not-a-version", object())
