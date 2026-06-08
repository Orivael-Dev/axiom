"""Shared test isolation.

AX OS now persists settings + persona under a stable home (AX_OS_HOME, default
~/.ax_os) so they survive a restart. Point every test at a throwaway home so the
suite never reads or writes the developer's real state, and defaults stay
deterministic. Tests that need a specific file still set AX_OS_SETTINGS /
AX_OS_PERSONA directly — those win over AX_OS_HOME.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_ax_os_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("ax_os_home")
    monkeypatch.setenv("AX_OS_HOME", str(home))
