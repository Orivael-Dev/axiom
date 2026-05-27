"""Three formatter functions with a duplicated padding block.
Scenario: agent extracts the duplication into one helper without
breaking tests."""
from __future__ import annotations


def format_user(name: str, role: str) -> str:
    # Duplicated padding block #1
    padded_name = name.strip().ljust(20)
    padded_role = role.strip().ljust(15)
    return f"{padded_name} | {padded_role}"


def format_org(name: str, region: str) -> str:
    # Duplicated padding block #2
    padded_name = name.strip().ljust(20)
    padded_region = region.strip().ljust(15)
    return f"{padded_name} | {padded_region}"


def format_project(name: str, status: str) -> str:
    # Duplicated padding block #3
    padded_name = name.strip().ljust(20)
    padded_status = status.strip().ljust(15)
    return f"{padded_name} | {padded_status}"
