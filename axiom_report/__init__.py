"""Axiom report generator — PDF reports via WeasyPrint + Jinja2.

Per docs/PHASE_1_DECISIONS.md §4, WeasyPrint is the canonical PDF
backend across all Axiom products that emit PDFs:

  - Kid-toy compliance audits (Phase 2 expansion — first user)
  - Certify badges (Phase 4)
  - CallGuard incident reports (Phase 4)
  - Data Gate right-to-erasure certificates (Phase 3)
  - Nightly Review reports (Phase 3)
  - Shield Lite incident reports (Phase 4)

The generator is intentionally stateless: feed it a template name
+ a context dict, get bytes back. Persistence + signing happens at
the caller.
"""
__version__ = "0.1.0"
