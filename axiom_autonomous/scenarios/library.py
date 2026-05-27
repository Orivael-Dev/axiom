"""Load + validate the curated scenarios library.

The library lives at `library.jsonl` next to this module — one JSON
record per line. Keeping it as JSONL (not a single JSON array) means
the user can append scenarios without rewriting the file and the
runner can stream-load if the library ever grows large.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from .criteria import Criteria


_REQUIRED_FIELDS = ("id", "title", "task", "seed", "criteria")


def scenarios_root() -> Path:
    return Path(__file__).resolve().parent


def seeds_root() -> Path:
    return scenarios_root() / "seeds"


def library_path() -> Path:
    return scenarios_root() / "library.jsonl"


@dataclass(frozen=True)
class Scenario:
    """One curated real-world task for the autonomous agent."""

    id:           str
    title:        str
    task:         str
    seed:         str
    criteria:     Criteria
    budget_steps: int = 20
    wall_seconds: int = 600
    tags:         tuple = field(default_factory=tuple)

    def seed_dir(self) -> Path:
        """Resolve the seed workdir for this scenario.

        Raises FileNotFoundError if the seed directory is missing — a
        scenario without a corresponding seed/ subdirectory is a
        library-integrity bug, not a runtime fallback case.
        """
        p = seeds_root() / self.seed
        if not p.is_dir():
            raise FileNotFoundError(
                f"scenario {self.id!r}: seed dir not found at {p}"
            )
        return p

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "title":        self.title,
            "task":         self.task,
            "seed":         self.seed,
            "criteria":     self.criteria.to_dict(),
            "budget_steps": self.budget_steps,
            "wall_seconds": self.wall_seconds,
            "tags":         list(self.tags),
        }


def _parse_scenario(record: Mapping) -> Scenario:
    missing = [k for k in _REQUIRED_FIELDS if k not in record]
    if missing:
        raise ValueError(
            f"scenario record missing required fields: {missing} "
            f"(record id: {record.get('id', '<no id>')})"
        )
    crit = record["criteria"]
    if not isinstance(crit, Mapping):
        raise ValueError(
            f"scenario {record['id']!r}: criteria must be an object, "
            f"got {type(crit).__name__}"
        )
    return Scenario(
        id=str(record["id"]),
        title=str(record["title"]),
        task=str(record["task"]),
        seed=str(record["seed"]),
        criteria=Criteria.from_dict(crit),
        budget_steps=int(record.get("budget_steps", 20)),
        wall_seconds=int(record.get("wall_seconds", 600)),
        tags=tuple(record.get("tags", ())),
    )


def load_library(path: Optional[Path] = None) -> List[Scenario]:
    """Load every scenario from `library.jsonl`.

    Returns scenarios in file order. Blank lines and `#` comments are
    skipped. Duplicate ids raise ValueError — every scenario needs a
    stable, unique id so result rows are joinable across runs.
    """
    path = path or library_path()
    if not path.exists():
        raise FileNotFoundError(f"scenarios library not found: {path}")
    out: List[Scenario] = []
    seen: set = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{path}:{lineno}: invalid JSON: {e.msg}"
            ) from e
        scenario = _parse_scenario(record)
        if scenario.id in seen:
            raise ValueError(
                f"{path}:{lineno}: duplicate scenario id {scenario.id!r}"
            )
        seen.add(scenario.id)
        out.append(scenario)
    return out


def filter_scenarios(
    scenarios: Iterable[Scenario], ids: Optional[Iterable[str]] = None,
) -> List[Scenario]:
    """Return scenarios whose id is in `ids`, preserving library order.

    `ids=None` (or empty) returns all scenarios. Unknown ids raise
    ValueError — a typo on the CLI shouldn't silently skip a scenario.
    """
    items = list(scenarios)
    if not ids:
        return items
    wanted = set(ids)
    have = {s.id for s in items}
    missing = wanted - have
    if missing:
        raise ValueError(
            f"unknown scenario id(s): {sorted(missing)}. "
            f"Available: {sorted(have)}"
        )
    return [s for s in items if s.id in wanted]
