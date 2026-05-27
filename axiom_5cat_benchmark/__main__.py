"""Entry point — `python3 -m axiom_5cat_benchmark`."""
from __future__ import annotations

import sys

from axiom_5cat_benchmark.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
