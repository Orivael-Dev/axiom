"""Output shape must survive any refactor the agent does."""
import formatters


def test_format_user_padding():
    out = formatters.format_user("alice  ", "admin")
    assert out == "alice".ljust(20) + " | " + "admin".ljust(15)


def test_format_org_padding():
    out = formatters.format_org("acme", " emea ")
    assert out == "acme".ljust(20) + " | " + "emea".ljust(15)


def test_format_project_padding():
    out = formatters.format_project("project x", "open")
    assert out == "project x".ljust(20) + " | " + "open".ljust(15)
