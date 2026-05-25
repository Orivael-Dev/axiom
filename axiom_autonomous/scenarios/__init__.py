"""Real-world scenarios harness for the AXIOM autonomous agent.

A small library of curated coding tasks the AutonomousAgent can be
pointed at via CLI:

    python3 -m axiom_autonomous.scenarios run --sandbox docker_required

Each scenario is a JSON record (one per line in `library.jsonl`)
declaring task text, seed workdir, success criteria, and budgets.
The runner spins one fresh sandbox per scenario, drives
AutonomousAgent.run(), then checks criteria against the post-run
workdir (pytest passes, files present, forbidden files unchanged).
Output is a single signed JSON artifact suitable for investor demos
or internal validation.

See `library.jsonl` for the current 5-scenario set and `seeds/`
for the starter workdirs each scenario copies into the sandbox.
"""
from __future__ import annotations

from .library import Scenario, load_library, scenarios_root, seeds_root
from .criteria import Criteria, check_criteria
from .runner import RunReport, ScenarioRunReport, run_scenarios

__all__ = [
    "Scenario", "load_library", "scenarios_root", "seeds_root",
    "Criteria", "check_criteria",
    "RunReport", "ScenarioRunReport", "run_scenarios",
]
