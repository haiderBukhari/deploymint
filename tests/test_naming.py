"""See docs/22-naming.md."""

import pytest

from deploymint.core.naming import slugify


def test_lowercases_and_replaces_spaces():
    assert slugify("bew proj") == "bew-proj"


def test_uppercase_is_lowercased():
    assert slugify("MyApp") == "myapp"


def test_mixed_punctuation_becomes_hyphens():
    assert slugify("My App! (v2)") == "my-app---v2-"


def test_hyphens_and_underscores_pass_through():
    assert slugify("already-valid_name") == "already-valid_name"


def test_all_punctuation_raises():
    with pytest.raises(ValueError, match="at least one alphanumeric"):
        slugify("---")
