"""These three tests should all pass after the agent's fix.
Two pass against the buggy seed; one fails until the agent fixes it."""
import stringutils


def test_slugify_basic():
    assert stringutils.slugify("Hello World") == "hello-world"


def test_slugify_punctuation():
    assert stringutils.slugify("foo, bar! baz?") == "foo-bar-baz"


def test_slugify_strips_leading_trailing_dashes():
    # This is the failing case the agent must fix in stringutils.py.
    assert stringutils.slugify("  leading space ") == "leading-space"
